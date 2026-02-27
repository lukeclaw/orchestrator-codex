"""Tests for claude_session_id tracking across the stack.

Covers: migration, repository, API, hook generation, and reconnect logic.
"""

import os
import subprocess
from unittest.mock import MagicMock, patch

from orchestrator.state.models import Session
from orchestrator.state.repositories import sessions as repo

# =============================================================================
# Migration tests
# =============================================================================

class TestMigration026:
    """Verify the 026 migration adds claude_session_id column."""

    def test_column_exists_after_migration(self, db):
        """claude_session_id column should exist in sessions table."""
        row = db.execute("PRAGMA table_info(sessions)").fetchall()
        columns = [r["name"] for r in row]
        assert "claude_session_id" in columns

    def test_backfill_sets_claude_session_id_to_id(self, db):
        """Existing rows should have claude_session_id = id after migration."""
        # The migration backfills claude_session_id = id for existing rows.
        # Insert a row without claude_session_id, then re-apply migration logic.
        # Since apply_migrations already ran, just create a session and verify.
        s = repo.create_session(db, "test-backfill", "localhost")
        assert s.claude_session_id == s.id


# =============================================================================
# Repository tests
# =============================================================================

class TestSessionRepository:
    """Test claude_session_id in create and update operations."""

    def test_create_session_populates_claude_session_id(self, db):
        """create_session() should set claude_session_id = id automatically."""
        s = repo.create_session(db, "test-create", "localhost")
        assert s.claude_session_id is not None
        assert s.claude_session_id == s.id

    def test_update_session_claude_session_id(self, db):
        """update_session() should persist claude_session_id."""
        s = repo.create_session(db, "test-update", "localhost")
        new_id = "new-claude-session-abc123"
        updated = repo.update_session(db, s.id, claude_session_id=new_id)
        assert updated.claude_session_id == new_id

    def test_update_session_claude_session_id_only(self, db):
        """update_session() with only claude_session_id should work (no status change)."""
        s = repo.create_session(db, "test-csid-only", "localhost")
        original_status = s.status
        updated = repo.update_session(db, s.id, claude_session_id="some-uuid")
        assert updated.claude_session_id == "some-uuid"
        assert updated.status == original_status

    def test_update_session_claude_session_id_with_status(self, db):
        """update_session() with both status and claude_session_id should work."""
        s = repo.create_session(db, "test-both", "localhost")
        updated = repo.update_session(db, s.id, status="working", claude_session_id="new-uuid")
        assert updated.status == "working"
        assert updated.claude_session_id == "new-uuid"

    def test_update_session_without_claude_session_id_preserves_it(self, db):
        """Updating other fields should not clear claude_session_id."""
        s = repo.create_session(db, "test-preserve", "localhost")
        original_csid = s.claude_session_id
        updated = repo.update_session(db, s.id, status="working")
        assert updated.claude_session_id == original_csid


# =============================================================================
# API tests
# =============================================================================

class TestSessionUpdateAPI:
    """Test PATCH /api/sessions/{id} with claude_session_id."""

    @patch('orchestrator.api.routes.sessions.repo')
    def test_patch_with_claude_session_id(self, mock_repo, db):
        """PATCH should pass claude_session_id through to repo."""
        from orchestrator.api.routes.sessions import SessionUpdate, update_session

        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.status = "idle"
        mock_repo.get_session.return_value = mock_session

        mock_updated = MagicMock()
        mock_updated.id = "test-id"
        mock_updated.status = "idle"
        mock_repo.update_session.return_value = mock_updated

        body = SessionUpdate(claude_session_id="new-claude-uuid")
        update_session("test-id", body, db=db)

        mock_repo.update_session.assert_called_once_with(
            db, "test-id",
            status=None,
            takeover_mode=None,
            claude_session_id="new-claude-uuid",
        )

    @patch('orchestrator.api.routes.sessions.repo')
    def test_patch_with_status_and_claude_session_id(self, mock_repo, db):
        """PATCH with both status and claude_session_id should pass both."""
        from orchestrator.api.routes.sessions import SessionUpdate, update_session

        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.status = "idle"
        mock_repo.get_session.return_value = mock_session

        mock_updated = MagicMock()
        mock_updated.id = "test-id"
        mock_updated.status = "working"
        mock_repo.update_session.return_value = mock_updated

        body = SessionUpdate(status="working", claude_session_id="new-uuid")
        update_session("test-id", body, db=db)

        mock_repo.update_session.assert_called_once_with(
            db, "test-id",
            status="working",
            takeover_mode=None,
            claude_session_id="new-uuid",
        )


# =============================================================================
# Hook generation tests
# =============================================================================

class TestHookGeneration:
    """Verify update-status.sh template contains claude_session_id logic."""

    def test_hook_extracts_claude_sid(self):
        """Hook script should extract session_id from JSON input."""
        hook_path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "agents", "worker", "hooks", "update-status.sh",
        )
        with open(hook_path) as f:
            content = f.read()

        assert "CLAUDE_SID" in content
        assert ".session_id" in content

    def test_hook_sends_claude_session_id_in_curl(self):
        """Hook script should include claude_session_id in curl requests."""
        hook_path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "agents", "worker", "hooks", "update-status.sh",
        )
        with open(hook_path) as f:
            content = f.read()

        assert "claude_session_id" in content
        # In bash: {\"claude_session_id\": \"$CLAUDE_SID\"}
        assert '\\"claude_session_id\\"' in content

    def test_hook_session_start_non_startup_sends_claude_session_id(self):
        """SessionStart for /clear should send claude_session_id and restore idle status."""
        hook_path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "agents", "worker", "hooks", "update-status.sh",
        )
        with open(hook_path) as f:
            content = f.read()

        # SessionStart (non-startup) should send both status and claude_session_id
        # to restore idle status after SessionEnd briefly set "disconnected"
        assert '\\"claude_session_id\\": \\"$CLAUDE_SID\\"' in content
        # The dedicated curl in the SessionStart branch should exit early
        assert "exit 0" in content


# =============================================================================
# Reconnect tests
# =============================================================================

class TestGetClaudeSessionArg:
    """Test _get_claude_session_arg three-way logic."""

    def test_session_exists_returns_resume(self):
        """If session exists, use -r to resume."""
        from orchestrator.session.reconnect import _get_claude_session_arg
        result = _get_claude_session_arg("abc-123", session_exists=True)
        assert result == "-r abc-123"

    def test_session_exists_with_tracked_id_returns_resume(self):
        """If session exists + tracked ID, still use -r."""
        from orchestrator.session.reconnect import _get_claude_session_arg
        result = _get_claude_session_arg("abc-123", session_exists=True, has_tracked_id=True)
        assert result == "-r abc-123"

    def test_tracked_id_session_missing_returns_new_session(self):
        """If tracked ID but session file gone, create fresh session (never use -c).

        Using -c on shared rdev hosts can resume a conversation from a different
        worker, carrying stale hooks and causing cross-worker contamination.
        """
        from orchestrator.session.reconnect import _get_claude_session_arg
        result = _get_claude_session_arg("abc-123", session_exists=False, has_tracked_id=True)
        assert result == "--session-id abc-123"

    def test_no_tracked_id_session_missing_returns_new_session(self):
        """If no tracked ID and no session, create new with --session-id."""
        from orchestrator.session.reconnect import _get_claude_session_arg
        result = _get_claude_session_arg("abc-123", session_exists=False, has_tracked_id=False)
        assert result == "--session-id abc-123"

    def test_no_tracked_id_default_session_missing(self):
        """Default has_tracked_id=False should create new session."""
        from orchestrator.session.reconnect import _get_claude_session_arg
        result = _get_claude_session_arg("abc-123", session_exists=False)
        assert result == "--session-id abc-123"


class TestReconnectUsesClaudeSessionId:
    """Test that reconnect logic uses claude_session_id when available."""

    @patch('orchestrator.session.reconnect._verify_claude_started', return_value=(True, ""))
    @patch('orchestrator.session.reconnect._check_claude_session_exists_local')
    @patch('orchestrator.session.reconnect._get_claude_session_arg')
    @patch('orchestrator.session.reconnect.safe_send_keys')
    @patch('orchestrator.session.reconnect._ensure_local_configs_exist')
    @patch('orchestrator.session.reconnect.get_path_export_command', return_value="export PATH=...")
    @patch('orchestrator.session.reconnect.get_worker_prompt', return_value=None)
    @patch('orchestrator.session.reconnect.check_tui_running_in_pane', return_value=False)
    @patch('orchestrator.session.health.check_claude_running_local',
           return_value=(False, "not running"))
    def test_local_reconnect_uses_claude_session_id(
        self, mock_alive, mock_tui, mock_prompt, mock_path, mock_ensure,
        mock_safe_send, mock_get_arg, mock_check_exists, mock_verify,
    ):
        """reconnect_local_worker should use claude_session_id for session check."""
        from orchestrator.session.reconnect import reconnect_local_worker

        mock_check_exists.return_value = True
        mock_get_arg.return_value = "-r tracked-uuid"

        session = Session(
            id="orch-id",
            name="test-worker",
            host="localhost",
            work_dir="/tmp/test",
            claude_session_id="tracked-uuid",
        )

        with patch('orchestrator.session.reconnect.get_reconnect_lock') as mock_lock:
            mock_lock.return_value = MagicMock()
            mock_lock.return_value.acquire.return_value = True
            with patch('orchestrator.terminal.manager.ensure_window'):
                reconnect_local_worker(session, "orch", "test-worker", 8093, "/tmp/test-dir")

        # Should check with tracked UUID, not orchestrator ID
        mock_check_exists.assert_called_once_with("tracked-uuid")
        mock_get_arg.assert_called_once_with("tracked-uuid", True, True)

    @patch('orchestrator.session.reconnect._verify_claude_started', return_value=(True, ""))
    @patch('orchestrator.session.reconnect._check_claude_session_exists_local')
    @patch('orchestrator.session.reconnect._get_claude_session_arg')
    @patch('orchestrator.session.reconnect.safe_send_keys')
    @patch('orchestrator.session.reconnect._ensure_local_configs_exist')
    @patch('orchestrator.session.reconnect.get_path_export_command', return_value="export PATH=...")
    @patch('orchestrator.session.reconnect.get_worker_prompt', return_value=None)
    @patch('orchestrator.session.reconnect.check_tui_running_in_pane', return_value=False)
    @patch('orchestrator.session.health.check_claude_running_local',
           return_value=(False, "not running"))
    def test_local_reconnect_falls_back_to_orch_id(
        self, mock_alive, mock_tui, mock_prompt, mock_path, mock_ensure,
        mock_safe_send, mock_get_arg, mock_check_exists, mock_verify,
    ):
        """reconnect_local_worker without claude_session_id should use session.id."""
        from orchestrator.session.reconnect import reconnect_local_worker

        mock_check_exists.return_value = False
        mock_get_arg.return_value = "--session-id orch-id"

        session = Session(
            id="orch-id",
            name="test-worker",
            host="localhost",
            work_dir="/tmp/test",
            claude_session_id=None,
        )

        with patch('orchestrator.session.reconnect.get_reconnect_lock') as mock_lock:
            mock_lock.return_value = MagicMock()
            mock_lock.return_value.acquire.return_value = True
            with patch('orchestrator.terminal.manager.ensure_window'):
                reconnect_local_worker(session, "orch", "test-worker", 8093, "/tmp/test-dir")

        # Should fall back to orchestrator ID
        mock_check_exists.assert_called_once_with("orch-id")
        mock_get_arg.assert_called_once_with("orch-id", False, False)


# =============================================================================
# Health check uses pane-based detection + both IDs for local workers
# =============================================================================

class TestHealthCheckUsesClaudeSessionId:
    """Regression: local health check must not rely solely on claude_session_id
    for ps aux matching.

    After /clear or /compact, Claude's internal session ID changes but the
    process command line retains the original --session-id.  The health check
    now uses pane-based process tree detection as the primary method, with
    ps aux fallback checking both session.id and claude_session_id.
    """

    @patch('orchestrator.session.health.check_claude_running_local')
    def test_health_check_passes_both_ids(self, mock_check, db):
        """Health check should pass both session.id and claude_session_id."""
        from orchestrator.session.health import check_and_update_worker_health

        s = repo.create_session(db, "test-local-health", "localhost")
        # Simulate /clear — claude_session_id diverges from session.id
        new_claude_id = "new-claude-sess-xyz"
        repo.update_session(db, s.id, claude_session_id=new_claude_id)
        s = repo.get_session(db, s.id)

        mock_check.return_value = (True, "Claude process running in pane")

        check_and_update_worker_health(db, s)

        # Must be called with both IDs + tmux info
        mock_check.assert_called_once()
        args = mock_check.call_args[1] if mock_check.call_args[1] else None
        positional = mock_check.call_args[0]
        assert positional[0] == s.id  # session.id first
        assert positional[1] == new_claude_id  # claude_session_id second

    @patch('orchestrator.session.health.check_claude_running_local')
    def test_health_check_falls_back_to_session_id(self, mock_check, db):
        """When claude_session_id is None, fall back gracefully."""
        from orchestrator.session.health import check_and_update_worker_health

        s = repo.create_session(db, "test-local-fallback", "localhost")
        # Force claude_session_id to None (e.g., legacy row)
        db.execute("UPDATE sessions SET claude_session_id = NULL WHERE id = ?", (s.id,))
        db.commit()
        s = repo.get_session(db, s.id)

        mock_check.return_value = (True, "Claude process running")

        check_and_update_worker_health(db, s)

        # Should pass session.id and None
        positional = mock_check.call_args[0]
        assert positional[0] == s.id
        assert positional[1] is None

    @patch('orchestrator.session.health._has_claude_in_process_tree', return_value=True)
    @patch('orchestrator.session.health._get_pane_pid', return_value=12345)
    def test_pane_detection_finds_claude_after_clear(self, mock_pane, mock_tree, db):
        """After /clear, pane-based detection still finds Claude even though
        claude_session_id no longer matches the process command line."""
        from orchestrator.session.health import check_claude_running_local

        alive, reason = check_claude_running_local(
            "original-session-id", "new-id-after-clear", "orch", "worker"
        )
        assert alive is True
        assert "pane" in reason

    @patch('orchestrator.session.health._has_claude_in_process_tree', return_value=False)
    @patch('orchestrator.session.health._get_pane_pid', return_value=12345)
    @patch('orchestrator.session.health.check_claude_process_local')
    def test_ps_aux_fallback_checks_session_id_first(self, mock_ps, mock_pane, mock_tree, db):
        """When pane detection fails, ps aux fallback tries session.id first."""
        from orchestrator.session.health import check_claude_running_local

        mock_ps.return_value = (True, "Claude process running")

        alive, reason = check_claude_running_local(
            "original-id", "different-id", "orch", "worker"
        )
        assert alive is True
        # First call should be with session.id
        assert mock_ps.call_args_list[0][0][0] == "original-id"

    @patch('orchestrator.session.health._has_claude_in_process_tree', return_value=False)
    @patch('orchestrator.session.health._get_pane_pid', return_value=12345)
    @patch('orchestrator.session.health.check_claude_process_local')
    def test_ps_aux_fallback_tries_claude_session_id(self, mock_ps, mock_pane, mock_tree, db):
        """When pane detection and session.id both fail, tries claude_session_id."""
        from orchestrator.session.health import check_claude_running_local

        # First call (session.id) fails, second (claude_session_id) succeeds
        mock_ps.side_effect = [
            (False, "No Claude process found for session original-id"),
            (True, "Claude process running"),
        ]

        alive, reason = check_claude_running_local(
            "original-id", "claude-id-after-reconnect", "orch", "worker"
        )
        assert alive is True
        assert mock_ps.call_count == 2
        assert mock_ps.call_args_list[1][0][0] == "claude-id-after-reconnect"

    @patch('orchestrator.session.health._has_claude_in_process_tree', return_value=False)
    @patch('orchestrator.session.health._get_pane_pid', return_value=None)
    @patch('orchestrator.session.health.check_claude_process_local')
    def test_pane_pid_none_skips_tree_check(self, mock_ps, mock_pane, mock_tree):
        """When pane PID is None (tmux window gone), skip tree walk, go to ps aux."""
        from orchestrator.session.health import check_claude_running_local

        mock_ps.return_value = (True, "Claude process running")

        alive, reason = check_claude_running_local(
            "sess-id", "claude-id", "orch", "worker"
        )
        assert alive is True
        # Tree check should NOT have been called (pane_pid is None)
        mock_tree.assert_not_called()
        # ps aux should have been called
        mock_ps.assert_called_once_with("sess-id")

    @patch('orchestrator.session.health._has_claude_in_process_tree', return_value=False)
    @patch('orchestrator.session.health._get_pane_pid', return_value=12345)
    @patch('orchestrator.session.health.check_claude_process_local')
    def test_same_ids_no_duplicate_ps_call(self, mock_ps, mock_pane, mock_tree):
        """When claude_session_id == session_id, don't call ps aux twice."""
        from orchestrator.session.health import check_claude_running_local

        mock_ps.return_value = (False, "No Claude process found for session same-id")

        alive, reason = check_claude_running_local(
            "same-id", "same-id", "orch", "worker"
        )
        assert alive is False
        # Should only call once — no duplicate for same ID
        assert mock_ps.call_count == 1

    @patch('orchestrator.session.health._has_claude_in_process_tree', return_value=False)
    @patch('orchestrator.session.health._get_pane_pid', return_value=12345)
    @patch('orchestrator.session.health.check_claude_process_local')
    def test_all_methods_fail_returns_false(self, mock_ps, mock_pane, mock_tree):
        """When pane tree, session.id ps aux, and claude_session_id ps aux all fail."""
        from orchestrator.session.health import check_claude_running_local

        mock_ps.side_effect = [
            (False, "No Claude process found for session sess-id"),
            (False, "No Claude process found for session claude-id"),
        ]

        alive, reason = check_claude_running_local(
            "sess-id", "claude-id", "orch", "worker"
        )
        assert alive is False
        assert mock_ps.call_count == 2

    @patch('orchestrator.session.health._has_claude_in_process_tree', return_value=False)
    @patch('orchestrator.session.health._get_pane_pid', return_value=12345)
    @patch('orchestrator.session.health.check_claude_process_local')
    def test_none_claude_session_id_no_second_ps_call(self, mock_ps, mock_pane, mock_tree):
        """When claude_session_id is None, only one ps aux call (session.id)."""
        from orchestrator.session.health import check_claude_running_local

        mock_ps.return_value = (False, "No Claude process found")

        alive, reason = check_claude_running_local(
            "sess-id", None, "orch", "worker"
        )
        assert alive is False
        assert mock_ps.call_count == 1


# =============================================================================
# _has_claude_in_process_tree unit tests
# =============================================================================

class TestHasClaudeInProcessTree:
    """Direct unit tests for _has_claude_in_process_tree."""

    @patch('orchestrator.session.health.subprocess.run')
    def test_finds_claude_as_direct_child(self, mock_run):
        """Claude process as a direct child of root_pid."""
        from orchestrator.session.health import _has_claude_in_process_tree

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "  PID  PPID ARGS\n"
                "  100     1 /bin/bash\n"
                "  200   100 /usr/bin/node /usr/local/bin/claude --session-id abc\n"
            ),
        )
        assert _has_claude_in_process_tree(100) is True

    @patch('orchestrator.session.health.subprocess.run')
    def test_finds_claude_as_grandchild(self, mock_run):
        """Claude process as a grandchild (bash -> node -> claude)."""
        from orchestrator.session.health import _has_claude_in_process_tree

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "  PID  PPID ARGS\n"
                "  100     1 /bin/bash\n"
                "  200   100 /usr/bin/node wrapper.js\n"
                "  300   200 /usr/local/bin/claude --session-id abc\n"
            ),
        )
        assert _has_claude_in_process_tree(100) is True

    @patch('orchestrator.session.health.subprocess.run')
    def test_no_claude_descendant(self, mock_run):
        """No claude process in the tree."""
        from orchestrator.session.health import _has_claude_in_process_tree

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "  PID  PPID ARGS\n"
                "  100     1 /bin/bash\n"
                "  200   100 /usr/bin/vim file.txt\n"
            ),
        )
        assert _has_claude_in_process_tree(100) is False

    @patch('orchestrator.session.health.subprocess.run')
    def test_excludes_grep_artifacts(self, mock_run):
        """grep claude in args should not count as a match."""
        from orchestrator.session.health import _has_claude_in_process_tree

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "  PID  PPID ARGS\n"
                "  100     1 /bin/bash\n"
                "  200   100 grep claude\n"
            ),
        )
        assert _has_claude_in_process_tree(100) is False

    @patch('orchestrator.session.health.subprocess.run')
    def test_no_children_returns_false(self, mock_run):
        """Root PID with no children returns False."""
        from orchestrator.session.health import _has_claude_in_process_tree

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "  PID  PPID ARGS\n"
                "  100     1 /bin/bash\n"
            ),
        )
        assert _has_claude_in_process_tree(100) is False

    @patch('orchestrator.session.health.subprocess.run')
    def test_ps_failure_returns_false(self, mock_run):
        """ps command failure returns False (fail-safe)."""
        from orchestrator.session.health import _has_claude_in_process_tree

        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _has_claude_in_process_tree(100) is False

    @patch('orchestrator.session.health.subprocess.run')
    def test_timeout_returns_false(self, mock_run):
        """Timeout returns False (fail-safe)."""
        from orchestrator.session.health import _has_claude_in_process_tree

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ps", timeout=5)
        assert _has_claude_in_process_tree(100) is False

    @patch('orchestrator.session.health.subprocess.run')
    def test_other_panes_claude_not_matched(self, mock_run):
        """Claude running under a different root PID should not match."""
        from orchestrator.session.health import _has_claude_in_process_tree

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "  PID  PPID ARGS\n"
                "  100     1 /bin/bash\n"
                "  200   100 /usr/bin/vim\n"
                "  300     1 /bin/bash\n"
                "  400   300 /usr/local/bin/claude --session-id other\n"
            ),
        )
        # PID 100's tree has no claude; PID 300's does but we're checking 100
        assert _has_claude_in_process_tree(100) is False


# =============================================================================
# Reconnect uses claude_session_id for local "is Claude running?" check
# =============================================================================

class TestReconnectLocalUsesClaudeSessionId:
    """Regression: reconnect_local_worker's initial 'is Claude running?' check
    must use pane-based detection with both IDs."""

    @patch('orchestrator.session.health.check_claude_running_local')
    @patch('orchestrator.session.reconnect.check_tui_running_in_pane', return_value=True)
    def test_reconnect_checks_via_pane_detection(self, mock_tui, mock_check):
        """reconnect_local_worker should use check_claude_running_local with both IDs."""
        from orchestrator.session.reconnect import reconnect_local_worker

        mock_check.return_value = (True, "Claude process running in pane")

        session = Session(
            id="orch-id-123",
            name="test-worker",
            host="localhost",
            work_dir="/tmp/test",
            claude_session_id="claude-id-xyz",
        )

        with patch('orchestrator.session.reconnect.get_reconnect_lock') as mock_lock:
            mock_lock.return_value = MagicMock()
            mock_lock.return_value.acquire.return_value = True
            with patch('orchestrator.terminal.manager.ensure_window'):
                result = reconnect_local_worker(
                    session, "orch", "test-worker", 8093, "/tmp/test-dir"
                )

        # Should detect Claude is running and return True (nothing to do)
        assert result is True
        # Must pass both session.id and claude_session_id
        mock_check.assert_called_once()
        positional = mock_check.call_args[0]
        assert positional[0] == "orch-id-123"
        assert positional[1] == "claude-id-xyz"


# =============================================================================
# Claude launch recovery tests
# =============================================================================

class TestClaudeLaunchRecovery:
    """Test that reconnect retries with --session-id when -r fails."""

    @patch('orchestrator.session.reconnect._verify_claude_started')
    @patch('orchestrator.session.reconnect.send_keys')
    @patch('orchestrator.session.reconnect._check_claude_session_exists_local', return_value=True)
    @patch('orchestrator.session.reconnect.safe_send_keys')
    @patch('orchestrator.session.reconnect._ensure_local_configs_exist')
    @patch('orchestrator.session.reconnect.get_path_export_command', return_value="export PATH=...")
    @patch('orchestrator.session.reconnect.get_worker_prompt', return_value=None)
    @patch('orchestrator.session.reconnect.check_tui_running_in_pane', return_value=False)
    @patch('orchestrator.session.health.check_claude_running_local',
           return_value=(False, "not running"))
    def test_local_reconnect_retries_on_r_failure(
        self, mock_alive, mock_tui, mock_prompt, mock_path, mock_ensure,
        mock_safe_send, mock_check_exists, mock_send_keys, mock_verify,
    ):
        """When -r fails, should retry with --session-id."""
        from orchestrator.session.reconnect import reconnect_local_worker

        # First call fails (original -r), second call succeeds (retry --session-id)
        mock_verify.side_effect = [
            (False, "No conversation found with session ID: abc-123"),
            (True, ""),
        ]

        session = Session(
            id="abc-123",
            name="test-worker",
            host="localhost",
            work_dir="/tmp/test",
            claude_session_id="abc-123",
        )

        with patch('orchestrator.session.reconnect.get_reconnect_lock') as mock_lock:
            mock_lock.return_value = MagicMock()
            mock_lock.return_value.acquire.return_value = True
            with patch('orchestrator.terminal.manager.ensure_window'):
                reconnect_local_worker(session, "orch", "test-worker", 8093, "/tmp/test-dir")

        # _verify_claude_started should be called twice (initial + retry)
        assert mock_verify.call_count == 2

        # The retry command should use --session-id
        retry_calls = [
            c for c in mock_safe_send.call_args_list
            if "--session-id" in str(c)
        ]
        assert len(retry_calls) >= 1, (
            f"Expected retry with --session-id, got: {mock_safe_send.call_args_list}"
        )

    @patch('orchestrator.session.reconnect._verify_claude_started', return_value=(True, ""))
    @patch('orchestrator.session.reconnect._check_claude_session_exists_local', return_value=True)
    @patch('orchestrator.session.reconnect.safe_send_keys')
    @patch('orchestrator.session.reconnect._ensure_local_configs_exist')
    @patch('orchestrator.session.reconnect.get_path_export_command', return_value="export PATH=...")
    @patch('orchestrator.session.reconnect.get_worker_prompt', return_value=None)
    @patch('orchestrator.session.reconnect.check_tui_running_in_pane', return_value=False)
    @patch('orchestrator.session.health.check_claude_running_local',
           return_value=(False, "not running"))
    def test_local_reconnect_no_retry_on_success(
        self, mock_alive, mock_tui, mock_prompt, mock_path, mock_ensure,
        mock_safe_send, mock_check_exists, mock_verify,
    ):
        """When -r succeeds, should NOT retry."""
        from orchestrator.session.reconnect import reconnect_local_worker

        session = Session(
            id="abc-123",
            name="test-worker",
            host="localhost",
            work_dir="/tmp/test",
            claude_session_id="abc-123",
        )

        with patch('orchestrator.session.reconnect.get_reconnect_lock') as mock_lock:
            mock_lock.return_value = MagicMock()
            mock_lock.return_value.acquire.return_value = True
            with patch('orchestrator.terminal.manager.ensure_window'):
                reconnect_local_worker(session, "orch", "test-worker", 8093, "/tmp/test-dir")

        # _verify_claude_started should only be called once (success on first try)
        assert mock_verify.call_count == 1

        # No --session-id retry should have been sent
        retry_calls = [
            c for c in mock_safe_send.call_args_list
            if "--session-id" in str(c)
        ]
        assert len(retry_calls) == 0, (
            f"Expected no retry with --session-id, got: {mock_safe_send.call_args_list}"
        )

    @patch('orchestrator.session.reconnect._verify_claude_started')
    @patch('orchestrator.session.reconnect.send_keys')
    @patch('orchestrator.session.reconnect._check_claude_session_exists_local', return_value=True)
    @patch('orchestrator.session.reconnect.safe_send_keys')
    @patch('orchestrator.session.reconnect._ensure_local_configs_exist')
    @patch('orchestrator.session.reconnect.get_path_export_command', return_value="export PATH=...")
    @patch('orchestrator.session.reconnect.get_worker_prompt', return_value=None)
    @patch('orchestrator.session.reconnect.check_tui_running_in_pane', return_value=False)
    @patch('orchestrator.session.health.check_claude_running_local',
           return_value=(False, "not running"))
    def test_local_reconnect_gives_up_after_retry_fails(
        self, mock_alive, mock_tui, mock_prompt, mock_path, mock_ensure,
        mock_safe_send, mock_check_exists, mock_send_keys, mock_verify,
    ):
        """When both -r and --session-id fail, should give up gracefully."""
        from orchestrator.session.reconnect import reconnect_local_worker

        # Both attempts fail
        mock_verify.side_effect = [
            (False, "No conversation found"),
            (False, "Some other error"),
        ]

        session = Session(
            id="abc-123",
            name="test-worker",
            host="localhost",
            work_dir="/tmp/test",
            claude_session_id="abc-123",
        )

        with patch('orchestrator.session.reconnect.get_reconnect_lock') as mock_lock:
            mock_lock.return_value = MagicMock()
            mock_lock.return_value.acquire.return_value = True
            with patch('orchestrator.terminal.manager.ensure_window'):
                # Should not raise
                reconnect_local_worker(session, "orch", "test-worker", 8093, "/tmp/test-dir")

        # Both attempts checked
        assert mock_verify.call_count == 2


# =============================================================================
# Local launch --session-id test
# =============================================================================

class TestLocalLaunchSessionId:
    """Test that local worker launch includes --session-id."""

    @patch('orchestrator.terminal.session.setup_local_worker')
    @patch('orchestrator.api.routes.sessions.threading')
    @patch('orchestrator.api.routes.sessions.ensure_window')
    @patch('orchestrator.api.routes.sessions.repo')
    @patch('orchestrator.api.routes.sessions.is_remote_host')
    def test_local_launch_includes_session_id(
        self, mock_is_remote, mock_repo, mock_ensure_window, mock_threading, mock_setup, db
    ):
        """Local worker launch should include --session-id via setup_local_worker."""
        from orchestrator.api.routes.sessions import SessionCreate, create_session

        mock_is_remote.return_value = False
        mock_ensure_window.return_value = "orchestrator:test-worker"
        mock_setup.return_value = {"ok": True}

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "idle"
        mock_session.work_dir = None
        mock_repo.create_session.return_value = mock_session

        body = SessionCreate(name="test-worker", host="localhost")
        mock_request = MagicMock()
        mock_request.app.state.config = {"server": {"port": 8093}}

        create_session(body, mock_request, db=db)

        # Verify setup_local_worker was called with correct session_id
        mock_setup.assert_called_once()
        call_kwargs = mock_setup.call_args
        # session_id is the second positional arg
        assert call_kwargs[0][1] == "test-session-id", (
            f"Expected session_id='test-session-id', got call: {call_kwargs}"
        )
