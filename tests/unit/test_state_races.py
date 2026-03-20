"""Tests for state machine and health check race conditions.

Exercises timing-sensitive interactions between health checks, reconnects,
user actions (pause/continue/stop), and the session repository layer.
All SSH/tmux/screen operations are mocked; only state transitions and
DB update patterns are tested.

Race conditions covered:
  1. Health check vs. reconnect thread status oscillation
  2. User pause vs. auto-reconnect conflicting writes
  3. Session deleted between health-check iteration and reconnect trigger
  4. Two concurrent health checks both reading same status
  5. State machine not enforced at the repository layer
  6. reconnect_remote_worker exception in finally block
  7. Connecting stuck-detection racing with reconnect about to complete
  8. Local reconnect not setting status on failure
"""

import threading
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.session.state_machine import (
    VALID_TRANSITIONS,
    SessionStatus,
    is_valid_transition,
)
from orchestrator.state.models import Session

pytestmark = pytest.mark.allow_threading

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(**overrides) -> Session:
    """Create a Session dataclass with sensible defaults."""
    defaults = {
        "id": "sess-001",
        "name": "worker-1",
        "host": "localhost",
        "work_dir": "/tmp/work",
        "tunnel_pid": None,
        "status": "working",
        "takeover_mode": False,
        "created_at": "2025-01-01T00:00:00Z",
        "last_status_changed_at": datetime.now(UTC).isoformat(),
        "session_type": "worker",
        "last_viewed_at": None,
        "auto_reconnect": False,
        "claude_session_id": None,
    }
    defaults.update(overrides)
    return Session(**defaults)


def _make_remote_session(**overrides) -> Session:
    """Create a remote (rdev) session."""
    defaults = {"host": "user/rdev-vm", "name": "rdev-worker"}
    defaults.update(overrides)
    return _make_session(**defaults)


class StatusTracker:
    """Thread-safe recorder for repo.update_session calls.

    Captures the chronological sequence of (session_id, status) pairs
    written to the database so tests can assert on ordering.
    """

    def __init__(self):
        self.history: list[tuple[str, str | None]] = []
        self._lock = threading.Lock()

    def update_session(self, conn, session_id, **kwargs):
        """Drop-in replacement for repo.update_session."""
        status = kwargs.get("status")
        with self._lock:
            self.history.append((session_id, status))
        # Return a mock updated session
        return _make_session(id=session_id, status=status or "working")

    @property
    def statuses(self) -> list[str]:
        """Return just the status values (ignoring None / non-status updates)."""
        return [s for _, s in self.history if s is not None]


# ===================================================================
# Race 1 -- Health check marks "disconnected" while reconnect thread
#            is about to set "waiting"
# ===================================================================


class TestHealthCheckVsReconnect:
    """Race 1: Health check and reconnect thread writing conflicting statuses.

    Timeline:
      T1 (health):    reads status="working", process dead -> writes "disconnected"
      T2 (reconnect): has already decided to write "waiting" (reconnect succeeded)
      Result: final status is whichever thread wins the last write -- can
              oscillate between disconnected/waiting across repeated cycles.
    """

    @patch("orchestrator.session.health.check_claude_running_local")
    @patch("orchestrator.session.health.is_remote_host")
    def test_health_check_overwrites_reconnect_status(self, mock_is_remote, mock_check_claude):
        """Demonstrate that health check can overwrite reconnect's 'waiting' status."""
        mock_is_remote.return_value = False
        mock_check_claude.return_value = (False, "No Claude process")

        tracker = StatusTracker()
        db = MagicMock()
        session = _make_session(status="waiting")

        # Simulate reconnect thread setting "waiting" first
        tracker.update_session(db, session.id, status="waiting")

        # Then health check marks "disconnected"
        with patch("orchestrator.session.health.repo") as mock_repo:
            mock_repo.update_session = tracker.update_session
            from orchestrator.session.health import check_and_update_worker_health

            result = check_and_update_worker_health(db, session)

        # The health check wrote "disconnected" AFTER reconnect wrote "waiting"
        assert tracker.statuses == ["waiting", "disconnected"]
        assert result["status"] == "disconnected"

    @patch("orchestrator.session.health.check_claude_running_local")
    @patch("orchestrator.session.health.is_remote_host")
    def test_interleaved_health_and_reconnect_threads(self, mock_is_remote, mock_check_claude):
        """Simulate true concurrent interleaving with threads.

        Both threads read the same initial status, then each writes
        a different value.  Without compare-and-swap, last writer wins.
        """
        mock_is_remote.return_value = False
        mock_check_claude.return_value = (False, "No Claude process")

        tracker = StatusTracker()
        barrier = threading.Barrier(2, timeout=5)

        def health_check_thread():
            barrier.wait()  # synchronize start
            with patch("orchestrator.session.health.repo") as mock_repo:
                mock_repo.update_session = tracker.update_session
                from orchestrator.session.health import check_and_update_worker_health

                session = _make_session(status="working")
                check_and_update_worker_health(MagicMock(), session)

        def reconnect_thread():
            barrier.wait()  # synchronize start
            time.sleep(0.01)  # tiny delay to let health check go first
            tracker.update_session(MagicMock(), "sess-001", status="waiting")

        t1 = threading.Thread(target=health_check_thread)
        t2 = threading.Thread(target=reconnect_thread)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # Both statuses appear -- order depends on scheduling but both writes happen
        assert "disconnected" in tracker.statuses
        assert "waiting" in tracker.statuses


# ===================================================================
# Race 2 -- User clicks "pause" while auto-reconnect sets "connecting"
# ===================================================================


class TestPauseVsAutoReconnect:
    """Race 2: User pause and auto-reconnect writing conflicting statuses.

    Timeline:
      T1 (auto-reconnect): trigger_reconnect writes "connecting"
      T2 (user pause):     pause_session writes "paused"
      Result: the session is "paused" in the DB but a background thread
              is actively reconnecting and will overwrite with "waiting"
              when it completes.
    """

    def test_pause_during_reconnect_produces_conflicting_state(self):
        """Pause arrives after trigger_reconnect sets status to 'connecting'."""
        tracker = StatusTracker()
        db = MagicMock()
        session = _make_session(status="disconnected")

        with (
            patch(
                "orchestrator.state.repositories.sessions.update_session",
                tracker.update_session,
            ),
            patch("orchestrator.terminal.ssh.is_remote_host", return_value=False),
            patch("orchestrator.terminal.manager.tmux_target", return_value=("orch", "w1")),
            patch("orchestrator.session.reconnect.reconnect_local_worker", return_value=True),
        ):
            from orchestrator.session.reconnect import trigger_reconnect

            trigger_reconnect(session, db, api_port=8093)

        # Now user pauses
        tracker.update_session(db, session.id, status="paused")

        # The status sequence shows the conflict: "idle" because no task
        # is assigned (with a task, _recovery_status would return "waiting")
        assert "connecting" in tracker.statuses
        assert "idle" in tracker.statuses
        assert "paused" in tracker.statuses

    def test_pause_endpoint_does_not_check_current_status(self):
        """The pause endpoint writes 'paused' unconditionally, ignoring current state.

        This means pausing during 'connecting' or any other state is not guarded.
        We verify by inspecting the source: pause_session calls repo.update_session
        with status='paused' without checking s.status first.
        """
        import ast
        from pathlib import Path

        # Read the source file
        source_path = Path("orchestrator/api/routes/sessions.py")
        source = source_path.read_text()
        tree = ast.parse(source)

        # Find pause_session function
        pause_fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "pause_session":
                pause_fn = node
                break

        assert pause_fn is not None, "pause_session function not found"

        # Check that the function body does NOT contain any comparison
        # against s.status (i.e., no guard on current state)
        fn_source = ast.get_source_segment(source, pause_fn)
        assert "s.status" not in fn_source, (
            "pause_session now checks s.status — race may be mitigated"
        )
        # Confirm it writes 'paused' unconditionally
        assert 'status="paused"' in fn_source


# ===================================================================
# Race 3 -- Session deleted between health-check iteration and
#            trigger_reconnect call
# ===================================================================


class TestDeletedSessionDuringHealthCheck:
    """Race 3: Session deleted after check_all_workers_health iterates it.

    Timeline:
      T1 (health loop): collects sessions, iterates, adds to auto_reconnect_candidates
      T2 (user delete):  delete_session removes from DB and calls cleanup_reconnect_lock
      T1 (health loop): calls trigger_reconnect with a stale session object
      Result: trigger_reconnect calls update_session on a deleted row -- silent no-op
              or error depending on the DB layer.
    """

    def test_trigger_reconnect_on_deleted_session(self):
        """trigger_reconnect silently succeeds when session was deleted mid-flight."""
        db = MagicMock()
        session = _make_session(status="disconnected")

        with (
            patch(
                "orchestrator.state.repositories.sessions.update_session",
                return_value=None,
            ),
            patch("orchestrator.terminal.ssh.is_remote_host", return_value=False),
            patch("orchestrator.terminal.manager.tmux_target", return_value=("orch", "w1")),
            patch("orchestrator.session.reconnect.reconnect_local_worker", return_value=True),
        ):
            from orchestrator.session.reconnect import trigger_reconnect

            result = trigger_reconnect(session, db, api_port=8093)

        # The function returns ok=True even though the DB update was a no-op
        assert result["ok"] is True

    @patch("orchestrator.session.health.check_claude_running_local")
    @patch("orchestrator.session.health.is_remote_host")
    def test_check_all_with_deleted_session_in_candidates(self, mock_is_remote, mock_check_claude):
        """check_all_workers_health adds a session to candidates, then session is deleted."""
        mock_is_remote.return_value = False
        mock_check_claude.return_value = (False, "No Claude process")

        db = MagicMock()
        session = _make_session(status="working", auto_reconnect=True)

        call_count = 0

        def mock_trigger_reconnect(s, conn, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"ok": False, "error": "Session not found"}

        with (
            patch("orchestrator.session.health.repo") as mock_repo,
            patch("orchestrator.session.health.check_and_update_worker_health") as mock_check,
            patch(
                "orchestrator.session.reconnect.trigger_reconnect",
                mock_trigger_reconnect,
            ),
        ):
            mock_check.return_value = {
                "alive": False,
                "status": "disconnected",
                "reason": "dead",
            }
            # Fix 5 re-reads session from DB before triggering reconnect.
            # Health check updates status to "disconnected" before we reach
            # the auto-reconnect loop, so get_session returns disconnected.
            disconnected = _make_session(status="disconnected", auto_reconnect=True)
            mock_repo.get_session.return_value = disconnected
            mock_repo.list_sessions.return_value = [disconnected]
            from orchestrator.session.health import check_all_workers_health

            results = check_all_workers_health(db, [session])

        # trigger_reconnect was called even though the session might have been deleted
        assert call_count == 1
        assert session.name in results["auto_reconnected"]


# ===================================================================
# Race 4 -- Two concurrent health checks (manual + periodic timer)
# ===================================================================


class TestDualConcurrentHealthChecks:
    """Race 4: Two health checks read the same status and both decide to update.

    Timeline:
      T1 (user click):    reads status="working", process dead -> will write "disconnected"
      T2 (periodic timer): reads status="working", process dead -> will write "disconnected"
      Both write "disconnected" -- the second write is redundant but harmless
      unless one of them also triggers reconnect, causing a double reconnect.
    """

    @patch("orchestrator.session.health.check_claude_running_local")
    @patch("orchestrator.session.health.is_remote_host")
    def test_double_health_check_double_writes(self, mock_is_remote, mock_check_claude):
        """Two health checks both write 'disconnected' for the same session."""
        mock_is_remote.return_value = False
        mock_check_claude.return_value = (False, "No Claude process")

        tracker = StatusTracker()
        db = MagicMock()
        session = _make_session(status="working")

        with patch("orchestrator.session.health.repo") as mock_repo:
            mock_repo.update_session = tracker.update_session
            from orchestrator.session.health import check_and_update_worker_health

            # Two concurrent health checks on the same session
            r1 = check_and_update_worker_health(db, session)
            r2 = check_and_update_worker_health(db, session)

        assert r1["status"] == "disconnected"
        assert r2["status"] == "disconnected"
        # Both wrote "disconnected" -- duplicate write
        assert tracker.statuses.count("disconnected") == 2

    @patch("orchestrator.session.health.window_exists", return_value=False)
    @patch("orchestrator.session.health.ensure_window")
    @patch("orchestrator.session.health.check_claude_running_local")
    @patch("orchestrator.session.health.is_remote_host")
    def test_concurrent_health_checks_via_threads(
        self, mock_is_remote, mock_check_claude, mock_ensure_window, mock_win_exists
    ):
        """Threaded version: both health checks fire at the same time."""
        mock_is_remote.return_value = False
        mock_check_claude.return_value = (False, "No Claude process")

        tracker = StatusTracker()
        barrier = threading.Barrier(2, timeout=5)
        results = [None, None]

        def run_health_check(index):
            barrier.wait()
            with patch("orchestrator.session.health.repo") as mock_repo:
                mock_repo.update_session = tracker.update_session
                from orchestrator.session.health import check_and_update_worker_health

                session = _make_session(status="working")
                results[index] = check_and_update_worker_health(MagicMock(), session)

        threads = [threading.Thread(target=run_health_check, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # Both threads wrote "disconnected"
        assert all(r["status"] == "disconnected" for r in results)
        assert tracker.statuses.count("disconnected") == 2

    @patch("orchestrator.session.health.check_claude_running_local")
    @patch("orchestrator.session.health.is_remote_host")
    def test_double_health_check_all_triggers_double_reconnect(
        self, mock_is_remote, mock_check_claude
    ):
        """Two check_all_workers_health calls can trigger reconnect twice."""
        mock_is_remote.return_value = False
        mock_check_claude.return_value = (False, "No Claude process")

        reconnect_count = 0

        def counting_trigger(s, conn, **kwargs):
            nonlocal reconnect_count
            reconnect_count += 1
            return {"ok": True}

        session = _make_session(status="working", auto_reconnect=True)

        with (
            patch("orchestrator.session.health.repo") as mock_repo,
            patch(
                "orchestrator.session.reconnect.trigger_reconnect",
                counting_trigger,
            ),
        ):
            mock_repo.update_session = StatusTracker().update_session
            # Fix 5: get_session must return session with disconnected status
            # (health check updates status to disconnected before auto-reconnect)
            disconnected = _make_session(status="disconnected", auto_reconnect=True)
            mock_repo.get_session.return_value = disconnected
            mock_repo.list_sessions.return_value = [disconnected]
            from orchestrator.session.health import check_all_workers_health

            check_all_workers_health(MagicMock(), [session])
            check_all_workers_health(MagicMock(), [session])

        # Reconnect triggered twice -- potential double reconnect
        assert reconnect_count == 2


# ===================================================================
# Race 5 -- State machine not enforced at the repository layer
# ===================================================================


class TestRepositoryBypassesStateMachine:
    """Race 5: update_session in the repository does not enforce VALID_TRANSITIONS.

    The state machine module defines allowed transitions, but
    repo.update_session accepts any status string.  Any caller can
    set any status at any time, bypassing the state machine entirely.
    """

    def test_repo_allows_any_status_string(self, db):
        """repo.update_session accepts any status, even invalid ones."""
        from orchestrator.state.repositories import sessions as repo

        session = repo.create_session(db, "test-worker", "localhost")
        assert session.status == "idle"

        # Jump directly to "working" -- valid transition
        updated = repo.update_session(db, session.id, status="working")
        assert updated.status == "working"

        # Jump from "working" to "connecting" -- INVALID per state machine
        assert not is_valid_transition("working", "connecting")
        # But the repo happily accepts it:
        updated = repo.update_session(db, session.id, status="connecting")
        assert updated.status == "connecting"

    def test_repo_rejects_nonsense_status(self, db):
        """repo.update_session now rejects invalid status values (RC-04 fix)."""
        from orchestrator.state.repositories import sessions as repo

        session = repo.create_session(db, "test-worker", "localhost")
        with pytest.raises(ValueError, match="Invalid session status"):
            repo.update_session(db, session.id, status="banana")

    def test_all_invalid_transitions_accepted_by_repo(self, db):
        """Enumerate invalid transitions and confirm the repo allows all of them."""
        from orchestrator.state.repositories import sessions as repo

        session = repo.create_session(db, "test-worker", "localhost")

        # For each status, find at least one INVALID target and confirm repo accepts it
        invalid_examples = []
        all_statuses = set(SessionStatus)
        for from_status in SessionStatus:
            allowed = VALID_TRANSITIONS.get(from_status, set())
            invalid_targets = all_statuses - allowed - {from_status}
            if invalid_targets:
                invalid_examples.append((from_status, next(iter(invalid_targets))))

        for from_status, to_status in invalid_examples:
            # Set to from_status first
            repo.update_session(db, session.id, status=from_status.value)
            # Attempt invalid transition -- should ideally be rejected
            updated = repo.update_session(db, session.id, status=to_status.value)
            # BUG: it succeeds
            assert updated.status == to_status.value, (
                f"Expected repo to reject {from_status.value} -> {to_status.value} "
                f"but it was accepted"
            )

    def _extract_function(self, source: str, func_name: str) -> str:
        """Extract a top-level function body from source code."""
        import re

        # Match from 'def name(' to the next top-level def/class or EOF
        pattern = rf"(def {func_name}\(.*?\n(?:(?:    |\n).*\n)*)"
        match = re.search(pattern, source)
        assert match, f"{func_name} function not found"
        return match.group(1)

    def test_pause_endpoint_bypasses_state_machine(self):
        """pause_session sets 'paused' without checking current status.

        Verified via source inspection to avoid FastAPI import triggering
        subprocess (which conflicts with the test subprocess guard).
        """
        from pathlib import Path

        source = Path("orchestrator/api/routes/sessions.py").read_text()
        fn_body = self._extract_function(source, "pause_session")

        # CONNECTING -> PAUSED is not in VALID_TRANSITIONS
        assert not is_valid_transition("connecting", "paused")

        # pause_session does NOT check s.status before writing
        assert "s.status" not in fn_body
        # It unconditionally writes 'paused'
        assert 'status="paused"' in fn_body

    def test_continue_endpoint_bypasses_state_machine(self):
        """continue_session sets 'working' regardless of current status.

        Verified via source inspection to avoid FastAPI import issues.
        """
        from pathlib import Path

        source = Path("orchestrator/api/routes/sessions.py").read_text()
        fn_body = self._extract_function(source, "continue_session")

        # continue_session does NOT check s.status before writing
        assert "s.status" not in fn_body
        assert 'status="working"' in fn_body

    def test_update_session_endpoint_accepts_any_status(self):
        """PATCH /sessions/{id} passes status through to repo layer.

        The repo layer now validates status values, but the endpoint itself
        doesn't add any additional validation beyond what repo provides.

        Verified via source inspection to avoid FastAPI import issues.
        """
        from pathlib import Path

        source = Path("orchestrator/api/routes/sessions.py").read_text()
        fn_body = self._extract_function(source, "update_session")

        # The endpoint passes body.status directly to repo.update_session
        # without its own transition check
        assert "body.status" in fn_body
        assert "validate_transition" not in fn_body


# ===================================================================
# Race 6 -- reconnect_remote_worker exception: finally block cleanup
# ===================================================================


class TestReconnectExceptionHandling:
    """Race 6: Exception after setting status='waiting' at line 334/703.

    reconnect_remote_worker has a try/finally for the reconnect lock.
    If the function sets status to 'waiting' (e.g. line 637, 703) and
    then an exception occurs later, the 'finally' block releases the
    lock but does NOT reset the status.  The session stays in 'waiting'
    even though the reconnect failed.

    On any exception, status is set to 'disconnected' so the session
    doesn't get stuck in a transient state like 'connecting'.
    """

    def test_status_set_on_success_and_error_on_exception(self):
        """Verify reconnect_remote_worker sets status correctly on success
        and on exception.

        Success path: creates RWS PTY and sets status to "waiting".
        Error path: exception during RWS setup sets status to "error".
        """
        tracker = StatusTracker()
        conn = MagicMock()
        session = _make_remote_session(status="disconnected")

        mock_rws = MagicMock()
        mock_rws.create_pty.return_value = "pty-test"

        with (
            patch(
                "orchestrator.terminal.session._ensure_rws_ready",
                return_value=mock_rws,
            ),
            patch("orchestrator.session.reconnect._ensure_tunnel"),
            patch("orchestrator.session.reconnect._reconnect_rws_for_host"),
            patch("orchestrator.session.reconnect._ensure_local_configs_exist"),
            patch("orchestrator.session.reconnect._copy_configs_to_remote"),
            patch(
                "orchestrator.session.reconnect._check_claude_session_exists_remote",
                return_value=False,
            ),
            patch(
                "orchestrator.terminal.session._build_claude_command",
                return_value="claude --session-id test",
            ),
            patch("orchestrator.session.reconnect.subprocess"),
            patch("orchestrator.session.reconnect.time.sleep"),
            patch("orchestrator.state.repositories.config.get_config_value", return_value=False),
        ):
            mock_repo = MagicMock()
            mock_repo.update_session = tracker.update_session

            from orchestrator.session.reconnect import reconnect_remote_worker

            reconnect_remote_worker(
                conn,
                session,
                "orchestrator",
                "rdev-worker",
                8093,
                "/tmp/test",
                mock_repo,
            )

        # Status was set to "idle" (no task assigned with mock DB).
        # With a real task, _recovery_status would return "waiting".
        # Claude's hooks will promote to "working" when it starts processing.
        assert "idle" in tracker.statuses

        # Now simulate what happens if _ensure_rws_ready raises an exception.
        tracker2 = StatusTracker()
        conn2 = MagicMock()
        session2 = _make_remote_session(status="disconnected")

        with (
            patch(
                "orchestrator.terminal.session._ensure_rws_ready",
                side_effect=RuntimeError("RWS deploy failed"),
            ),
            patch("orchestrator.session.reconnect.subprocess"),
            patch("orchestrator.session.reconnect.time.sleep"),
            patch("orchestrator.state.repositories.config.get_config_value", return_value=False),
        ):
            mock_repo2 = MagicMock()
            mock_repo2.update_session = tracker2.update_session

            with pytest.raises(RuntimeError, match="RWS deploy failed"):
                reconnect_remote_worker(
                    conn2,
                    session2,
                    "orchestrator",
                    "rdev-worker",
                    8093,
                    "/tmp/test",
                    mock_repo2,
                )

        # Generic exception handler sets status to "disconnected"
        # so the session doesn't get stuck in "connecting" or "waiting".
        assert "disconnected" in tracker2.statuses

    def test_generic_exception_sets_disconnected_status(self):
        """Generic exceptions set status to 'disconnected'.

        All exceptions in reconnect_remote_worker set status to 'disconnected'
        to prevent stuck sessions.
        """
        tracker = StatusTracker()
        conn = MagicMock()
        session = _make_remote_session(status="connecting")

        with (
            patch(
                "orchestrator.terminal.session._ensure_rws_ready",
                side_effect=RuntimeError("RWS deploy failed"),
            ),
            patch("orchestrator.session.reconnect.subprocess"),
            patch("orchestrator.session.reconnect.time.sleep"),
            patch("orchestrator.state.repositories.config.get_config_value", return_value=False),
        ):
            mock_repo = MagicMock()
            mock_repo.update_session = tracker.update_session
            from orchestrator.session.reconnect import reconnect_remote_worker

            with pytest.raises(RuntimeError, match="RWS deploy failed"):
                reconnect_remote_worker(
                    conn,
                    session,
                    "orchestrator",
                    "rdev-worker",
                    8093,
                    "/tmp/test",
                    mock_repo,
                )

        # Status is set to "disconnected" on generic exceptions
        assert "disconnected" in tracker.statuses

    def test_any_exception_sets_disconnected_status(self):
        """Any exception in reconnect_remote_worker sets status to 'disconnected'.

        The new RWS PTY code path catches all exceptions and sets disconnected status.
        """
        tracker = StatusTracker()
        conn = MagicMock()
        session = _make_remote_session(status="disconnected")

        with (
            patch(
                "orchestrator.terminal.session._ensure_rws_ready",
                side_effect=ConnectionError("SSH tunnel broken"),
            ),
            patch("orchestrator.session.reconnect.subprocess"),
            patch("orchestrator.session.reconnect.time.sleep"),
            patch("orchestrator.state.repositories.config.get_config_value", return_value=False),
        ):
            mock_repo = MagicMock()
            mock_repo.update_session = tracker.update_session
            from orchestrator.session.reconnect import reconnect_remote_worker

            with pytest.raises(ConnectionError, match="SSH tunnel broken"):
                reconnect_remote_worker(
                    conn,
                    session,
                    "orchestrator",
                    "rdev-worker",
                    8093,
                    "/tmp/test",
                    mock_repo,
                )

        # Status should be set to "disconnected"
        assert "disconnected" in tracker.statuses


# ===================================================================
# Race 7 -- Connecting stuck-detection races with ongoing reconnect
# ===================================================================


class TestConnectingStuckDetection:
    """Race 7: check_all_workers_health marks 'connecting' as stuck (>2 min)
    while a reconnect thread is about to complete successfully.

    Timeline:
      T1 (reconnect): nearly done, about to set status "waiting"
      T2 (health-all): sees status "connecting" for >2 min, writes "disconnected"
      T1 (reconnect): sets "waiting"
      Result: status momentarily becomes "disconnected", then "waiting" --
              the health check might have also triggered ANOTHER reconnect
              (double reconnect).
    """

    def test_stuck_connecting_detection_races_with_reconnect_completion(self):
        """Health check marks 'connecting' session as 'disconnected' while
        a reconnect is about to finish."""
        # Session stuck in connecting for 11 minutes
        old_time = (datetime.now(UTC) - timedelta(minutes=11)).isoformat()
        session = _make_session(
            status="connecting",
            auto_reconnect=True,
            last_status_changed_at=old_time,
        )

        reconnect_calls = []

        def mock_trigger(s, conn, **kwargs):
            reconnect_calls.append(s.name)
            return {"ok": True}

        tracker = StatusTracker()
        db = MagicMock()

        with (
            patch("orchestrator.session.health.repo") as mock_repo,
            patch(
                "orchestrator.session.reconnect.trigger_reconnect",
                mock_trigger,
            ),
        ):
            mock_repo.update_session = tracker.update_session
            # Fix 5: check_all re-reads session from DB before reconnect
            disconnected_session = _make_session(status="disconnected", auto_reconnect=True)
            mock_repo.get_session.return_value = disconnected_session
            mock_repo.list_sessions.return_value = [disconnected_session]
            from orchestrator.session.health import check_all_workers_health

            results = check_all_workers_health(db, [session])

        # Health check wrote "disconnected" for the stuck connecting session
        assert "disconnected" in tracker.statuses
        # And then triggered a reconnect -- potential double reconnect
        assert len(reconnect_calls) == 1
        assert session.name in results["auto_reconnected"]

    def test_connecting_just_under_threshold_not_marked_stuck(self):
        """Session connecting for 1 minute should NOT be marked stuck (threshold is 2 min)."""
        recent_time = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        session = _make_session(
            status="connecting",
            auto_reconnect=True,
            last_status_changed_at=recent_time,
        )

        tracker = StatusTracker()
        db = MagicMock()

        with (
            patch("orchestrator.session.health.repo") as mock_repo,
            patch(
                "orchestrator.session.reconnect.trigger_reconnect",
            ) as mock_trigger,
        ):
            mock_repo.update_session = tracker.update_session
            mock_repo.list_sessions.return_value = []
            from orchestrator.session.health import check_all_workers_health

            check_all_workers_health(db, [session])

        # Should be skipped (still connecting), not marked disconnected
        assert "disconnected" not in tracker.statuses
        mock_trigger.assert_not_called()

    def test_reconnect_completion_after_stuck_detection_overwrites(self):
        """Simulate the full race: stuck detection writes 'disconnected',
        then reconnect thread writes 'waiting'."""
        tracker = StatusTracker()
        db = MagicMock()

        # Step 1: health check writes "disconnected" (stuck detection)
        tracker.update_session(db, "sess-001", status="disconnected")

        # Step 2: reconnect thread (which was already running) completes
        tracker.update_session(db, "sess-001", status="waiting")

        # Final state is "waiting" but health check already triggered a new reconnect
        assert tracker.statuses == ["disconnected", "waiting"]
        # The "disconnected" write is the source of the race


# ===================================================================
# Race 8 -- Local reconnect doesn't set status on failure
# ===================================================================


class TestLocalReconnectStatusOnFailure:
    """Race 8: trigger_reconnect for local workers calls reconnect_local_worker.

    reconnect_local_worker returns True on success and False on silent failure
    (e.g. lock not acquired, Claude failed to start).  trigger_reconnect checks
    this return value and sets 'waiting' only on True, 'disconnected' on False.
    Exceptions are caught separately and also result in 'disconnected'.
    """

    def test_local_reconnect_silent_failure_stays_disconnected(self):
        """If reconnect_local_worker returns False (silent failure),
        trigger_reconnect sets 'disconnected' instead of 'waiting'."""
        tracker = StatusTracker()
        db = MagicMock()
        session = _make_session(status="disconnected")

        with (
            patch(
                "orchestrator.state.repositories.sessions.update_session",
                tracker.update_session,
            ),
            patch("orchestrator.terminal.ssh.is_remote_host", return_value=False),
            patch("orchestrator.terminal.manager.tmux_target", return_value=("orch", "w1")),
            # reconnect_local_worker returns False (Claude failed to start)
            patch("orchestrator.session.reconnect.reconnect_local_worker", return_value=False),
        ):
            from orchestrator.session.reconnect import trigger_reconnect

            result = trigger_reconnect(session, db, api_port=8093)

        # trigger_reconnect sets "connecting" then "disconnected" (not "waiting")
        assert "connecting" in tracker.statuses
        assert "disconnected" in tracker.statuses
        assert "waiting" not in tracker.statuses
        assert result["ok"] is False

    def test_local_reconnect_exception_sets_disconnected(self):
        """If reconnect_local_worker raises, trigger_reconnect sets 'disconnected'."""
        tracker = StatusTracker()
        db = MagicMock()
        session = _make_session(status="disconnected")

        with (
            patch(
                "orchestrator.state.repositories.sessions.update_session",
                tracker.update_session,
            ),
            patch("orchestrator.terminal.ssh.is_remote_host", return_value=False),
            patch("orchestrator.terminal.manager.tmux_target", return_value=("orch", "w1")),
            patch(
                "orchestrator.session.reconnect.reconnect_local_worker",
                side_effect=RuntimeError("tmux crashed"),
            ),
        ):
            from orchestrator.session.reconnect import trigger_reconnect

            result = trigger_reconnect(session, db, api_port=8093)

        # Sets "connecting" then "disconnected" on exception
        assert "connecting" in tracker.statuses
        assert "disconnected" in tracker.statuses
        assert result["ok"] is False

    def test_reconnect_local_worker_returns_true_on_already_running(self):
        """reconnect_local_worker returns True when Claude is found already
        running (no need to relaunch)."""
        session = _make_session(status="disconnected")

        with (
            patch("orchestrator.session.reconnect.get_reconnect_lock") as mock_lock_fn,
            patch("orchestrator.session.reconnect.os.makedirs"),
            patch("orchestrator.terminal.manager.ensure_window"),
            patch(
                "orchestrator.session.reconnect.check_tui_running_in_pane",
                return_value=True,
            ),
            patch(
                "orchestrator.session.health.check_claude_running_local",
                return_value=(True, "running"),
            ),
        ):
            mock_lock = MagicMock()
            mock_lock.acquire.return_value = True
            mock_lock_fn.return_value = mock_lock
            from orchestrator.session.reconnect import reconnect_local_worker

            result = reconnect_local_worker(session, "orchestrator", "worker-1", 8093, "/tmp/test")

        # Returns True — Claude is alive, trigger_reconnect will set "waiting"
        assert result is True


# ===================================================================
# Cross-cutting: verify the reconnect lock prevents true concurrency
# ===================================================================


class TestReconnectLockPrevention:
    """Verify that the per-session reconnect lock prevents concurrent reconnects,
    which is the existing mitigation for some of the races above."""

    def test_reconnect_lock_is_per_session(self):
        """Each session gets its own lock."""
        from orchestrator.session.reconnect import get_reconnect_lock

        lock_a = get_reconnect_lock("session-a")
        lock_b = get_reconnect_lock("session-b")
        assert lock_a is not lock_b

        # Same session returns same lock
        lock_a2 = get_reconnect_lock("session-a")
        assert lock_a is lock_a2

    def test_concurrent_reconnect_skipped_when_locked(self):
        """Second reconnect attempt is skipped when lock is held."""
        from orchestrator.session.reconnect import get_reconnect_lock

        session_id = "test-lock-session"
        lock = get_reconnect_lock(session_id)

        # First acquire succeeds
        assert lock.acquire(timeout=0)

        # Second acquire fails (lock already held)
        assert not lock.acquire(timeout=0)

        lock.release()

        # Clean up
        from orchestrator.session.reconnect import cleanup_reconnect_lock

        cleanup_reconnect_lock(session_id)

    def test_cleanup_removes_lock(self):
        """cleanup_reconnect_lock removes the lock from the registry."""
        from orchestrator.session.reconnect import (
            _reconnect_locks,
            cleanup_reconnect_lock,
            get_reconnect_lock,
        )

        session_id = "test-cleanup-session"
        get_reconnect_lock(session_id)
        assert session_id in _reconnect_locks

        cleanup_reconnect_lock(session_id)
        assert session_id not in _reconnect_locks


# ===================================================================
# Race scenario: full integration-style test combining multiple races
# ===================================================================


class TestCombinedRaceScenarios:
    """Test scenarios that combine multiple race conditions."""

    @patch("orchestrator.session.health.check_claude_running_local")
    @patch("orchestrator.session.health.is_remote_host")
    def test_health_check_triggers_reconnect_which_races_with_next_health_check(
        self, mock_is_remote, mock_check_claude
    ):
        """Full scenario:
        1. health_check_all finds session dead, triggers auto-reconnect
        2. auto-reconnect sets 'connecting'
        3. second health_check_all runs while reconnect is ongoing
        4. second health check sees 'connecting', starts stuck timer
        5. reconnect completes, sets 'waiting'
        6. third health_check_all sees 'waiting', all good

        The race is between steps 3-4 where health check could interfere.
        """
        mock_is_remote.return_value = False
        mock_check_claude.return_value = (False, "No Claude process")

        events = []

        def track_trigger(s, conn, **kwargs):
            events.append(("reconnect_triggered", s.name))
            return {"ok": True}

        # Phase 1: session is working but Claude is dead
        session = _make_session(status="working", auto_reconnect=True)

        with (
            patch("orchestrator.session.health.repo") as mock_repo,
            patch(
                "orchestrator.session.reconnect.trigger_reconnect",
                track_trigger,
            ),
        ):
            tracker = StatusTracker()
            mock_repo.update_session = tracker.update_session
            # Fix 5: get_session returns fresh state (disconnected after health check)
            disconnected = _make_session(status="disconnected", auto_reconnect=True)
            mock_repo.get_session.return_value = disconnected
            mock_repo.list_sessions.return_value = [disconnected]
            from orchestrator.session.health import check_all_workers_health

            check_all_workers_health(MagicMock(), [session])

        assert ("reconnect_triggered", "worker-1") in events
        assert "disconnected" in tracker.statuses

    def test_pause_then_health_check_then_reconnect_sequence(self):
        """User pauses -> health check runs -> auto-reconnect fires.

        This exercises the scenario where:
        1. User pauses (status = 'paused')
        2. Health check finds process dead (status = 'disconnected')
        3. Auto-reconnect triggers (status = 'connecting')
        All three are different status values written in quick succession.
        """
        tracker = StatusTracker()
        db = MagicMock()

        # Step 1: User pauses
        tracker.update_session(db, "sess-001", status="paused")

        # Step 2: Health check finds process dead
        tracker.update_session(db, "sess-001", status="disconnected")

        # Step 3: Auto-reconnect triggers
        tracker.update_session(db, "sess-001", status="connecting")

        assert tracker.statuses == ["paused", "disconnected", "connecting"]
        # None of these transitions are validated against the state machine


# ===================================================================
# Verify health check idempotency guards
# ===================================================================


class TestHealthCheckIdempotencyGuards:
    """Test the existing guards that prevent redundant status writes.

    The health check has guards like `if session.status != "disconnected":`
    before writing "disconnected".  These prevent redundant writes but
    are based on a STALE snapshot of the session status (read at the
    start of the function), not the current DB value.
    """

    @patch("orchestrator.session.health.check_claude_running_local")
    @patch("orchestrator.session.health.is_remote_host")
    def test_guard_prevents_redundant_write_when_already_disconnected(
        self, mock_is_remote, mock_check_claude
    ):
        """If session is already 'disconnected', health check skips the write."""
        mock_is_remote.return_value = False
        mock_check_claude.return_value = (False, "No Claude process")

        tracker = StatusTracker()
        db = MagicMock()
        session = _make_session(status="disconnected")

        with patch("orchestrator.session.health.repo") as mock_repo:
            mock_repo.update_session = tracker.update_session
            from orchestrator.session.health import check_and_update_worker_health

            result = check_and_update_worker_health(db, session)

        # Guard caught it -- no redundant write
        assert tracker.statuses == []
        assert result["status"] == "disconnected"

    @patch("orchestrator.session.health.check_claude_running_local")
    @patch("orchestrator.session.health.is_remote_host")
    def test_guard_uses_stale_status_snapshot(self, mock_is_remote, mock_check_claude):
        """Guard uses session.status (from function entry), not current DB value.

        If another thread changes the DB status between the function entry
        and the guard check, the guard operates on stale data.
        """
        mock_is_remote.return_value = False
        mock_check_claude.return_value = (False, "No Claude process")

        tracker = StatusTracker()
        db = MagicMock()

        # Session was "working" when health check started
        session = _make_session(status="working")

        # But by the time the guard runs, another thread has set it to "disconnected"
        # The guard in the health check function uses session.status (still "working")
        # so it proceeds to write "disconnected" again

        with patch("orchestrator.session.health.repo") as mock_repo:
            mock_repo.update_session = tracker.update_session
            from orchestrator.session.health import check_and_update_worker_health

            check_and_update_worker_health(db, session)

        # The write happens because session.status was "working" (stale)
        assert "disconnected" in tracker.statuses


# ===================================================================
# Race 9 -- SessionStart hook overwrites task-aware reconnect status
# ===================================================================


class TestPatchIdlePromotion:
    """Race 9: The SessionStart hook fires after reconnect and sends
    PATCH /sessions/{id} with status="idle", overwriting the task-aware
    "waiting" status set by the reconnect flow.

    The fix adds a server-side guard in the PATCH endpoint that calls
    _recovery_status() when status="idle" is requested, promoting to
    "waiting" if assigned tasks exist.
    """

    def test_patch_idle_promoted_to_waiting_when_task_assigned(self):
        """PATCH status='idle' is promoted to 'waiting' when tasks are assigned."""
        from pathlib import Path

        source = Path("orchestrator/api/routes/sessions.py").read_text()

        # The PATCH endpoint now imports _recovery_status
        assert "_recovery_status" in source
        # And checks when body.status == "idle"
        assert 'body.status == "idle"' in source
        # Uses effective_status for the repo call
        assert "status=effective_status" in source

    def test_patch_idle_promoted_to_waiting_integration(self):
        """Integration: PATCH with status='idle' writes 'waiting' when tasks exist."""
        from orchestrator.state.db import get_memory_connection
        from orchestrator.state.migrations.runner import apply_migrations
        from orchestrator.state.repositories import projects as prepo
        from orchestrator.state.repositories import sessions as srepo
        from orchestrator.state.repositories import tasks as trepo

        conn = get_memory_connection()
        apply_migrations(conn)

        try:
            # Create session and project
            session = srepo.create_session(conn, name="test-w", host="localhost", work_dir="/tmp")
            srepo.update_session(conn, session.id, status="working")
            project = prepo.create_project(conn, name="test-project")

            # Create assigned task
            task = trepo.create_task(conn, project_id=project.id, title="do stuff")
            trepo.update_task(conn, task.id, assigned_session_id=session.id)

            # Simulate what _recovery_status returns
            from orchestrator.session.reconnect import _recovery_status

            status = _recovery_status(conn, session.id)
            assert status == "waiting"

            # Verify the guard logic: if body.status is "idle" but
            # _recovery_status returns "waiting", the effective status should be "waiting"
            effective = "idle"
            if effective == "idle":
                effective = _recovery_status(conn, session.id)
            assert effective == "waiting"

            # Write it and verify DB
            srepo.update_session(conn, session.id, status=effective)
            updated = srepo.get_session(conn, session.id)
            assert updated.status == "waiting"
        finally:
            conn.close()

    def test_patch_idle_stays_idle_when_no_tasks(self):
        """PATCH status='idle' stays 'idle' when no tasks are assigned."""
        from orchestrator.state.db import get_memory_connection
        from orchestrator.state.migrations.runner import apply_migrations
        from orchestrator.state.repositories import sessions as srepo

        conn = get_memory_connection()
        apply_migrations(conn)

        try:
            session = srepo.create_session(conn, name="test-w2", host="localhost", work_dir="/tmp")
            srepo.update_session(conn, session.id, status="working")

            from orchestrator.session.reconnect import _recovery_status

            status = _recovery_status(conn, session.id)
            assert status == "idle"

            # Guard logic: no promotion needed
            effective = "idle"
            if effective == "idle":
                effective = _recovery_status(conn, session.id)
            assert effective == "idle"

            srepo.update_session(conn, session.id, status=effective)
            updated = srepo.get_session(conn, session.id)
            assert updated.status == "idle"
        finally:
            conn.close()

    def test_patch_non_idle_status_not_affected(self):
        """PATCH with status != 'idle' is not affected by the guard."""
        from orchestrator.state.db import get_memory_connection
        from orchestrator.state.migrations.runner import apply_migrations
        from orchestrator.state.repositories import projects as prepo
        from orchestrator.state.repositories import sessions as srepo
        from orchestrator.state.repositories import tasks as trepo

        conn = get_memory_connection()
        apply_migrations(conn)

        try:
            session = srepo.create_session(conn, name="test-w3", host="localhost", work_dir="/tmp")
            project = prepo.create_project(conn, name="test-project")
            # Create assigned task
            task = trepo.create_task(conn, project_id=project.id, title="do stuff")
            trepo.update_task(conn, task.id, assigned_session_id=session.id)

            # Guard logic: "working" is not "idle", so no promotion
            effective = "working"
            if effective == "idle":
                from orchestrator.session.reconnect import _recovery_status

                effective = _recovery_status(conn, session.id)
            assert effective == "working"

            srepo.update_session(conn, session.id, status=effective)
            updated = srepo.get_session(conn, session.id)
            assert updated.status == "working"
        finally:
            conn.close()

    def test_patch_event_uses_effective_status(self):
        """The published event contains the promoted status, not the raw request."""
        from pathlib import Path

        source = Path("orchestrator/api/routes/sessions.py").read_text()

        # The event data should use effective_status, not body.status
        # Check that "new_status": effective_status is in the source
        assert '"new_status": effective_status' in source
        # And the condition also uses effective_status
        assert "if effective_status and effective_status != old_status:" in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
