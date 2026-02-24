"""Race condition tests for concurrent reconnect operations.

Tests the following race condition scenarios in the reconnect subsystem:

1. Manual reconnect vs health-check auto-reconnect for the same session
2. Create new session with same name while old session is being deleted/reconnected
3. Two concurrent health checks both decide to reconnect the same worker
4. Reconnect starts just as tunnel_health_loop restarts the tunnel
5. trigger_reconnect called twice rapidly (double-click scenario)
6. Background reconnect thread updates DB status while main thread reads stale data

Each test exercises the locking, state transitions, and DB updates using
threading and controlled timing. SSH/tmux operations are fully mocked.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.state.db import get_memory_connection
from orchestrator.state.migrations.runner import apply_migrations
from orchestrator.state.models import Session
from orchestrator.state.repositories import sessions as repo

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def race_db():
    """In-memory SQLite DB with schema applied for race condition tests."""
    conn = get_memory_connection()
    apply_migrations(conn)
    return conn


@pytest.fixture
def worker_session(race_db):
    """Create a disconnected remote worker session for reconnect tests."""
    s = repo.create_session(race_db, "worker-1", "user/rdev-vm", work_dir="/home/user/code")
    repo.update_session(race_db, s.id, status="disconnected", auto_reconnect=True)
    return repo.get_session(race_db, s.id)


@pytest.fixture(autouse=True)
def _clear_reconnect_locks():
    """Clear the global reconnect lock registry between tests."""
    from orchestrator.session.reconnect import _reconnect_locks, _registry_lock

    with _registry_lock:
        _reconnect_locks.clear()
    yield
    with _registry_lock:
        _reconnect_locks.clear()


def _make_mock_session(
    session_id="sess-1",
    name="worker-1",
    host="user/rdev-vm",
    status="disconnected",
    auto_reconnect=True,
):
    """Build a mock Session with all required attributes."""
    s = MagicMock(spec=Session)
    s.id = session_id
    s.name = name
    s.host = host
    s.status = status
    s.work_dir = "/home/user/code"
    s.auto_reconnect = auto_reconnect
    s.claude_session_id = None
    s.last_status_changed_at = "2025-01-01T00:00:00Z"
    s.session_type = "worker"
    return s


# ============================================================================
# Race 1: Manual reconnect vs health-check auto-reconnect
# ============================================================================


class TestManualVsAutoReconnectRace:
    """User clicks Reconnect while a health-check auto-reconnect is in progress.

    Bug scenario:
        T1 (health check) acquires lock, starts reconnect_remote_worker.
        T2 (manual /reconnect) calls trigger_reconnect, which calls
        reconnect_remote_worker -> tries lock.acquire(timeout=5).
        If T1 finishes within 5s, T2 succeeds -> double reconnect attempt.
        If T1 takes >5s, T2 skips silently -> confusing to user.

    Severity: Stuck state / cosmetic. The per-session lock prevents true
    corruption, but the user sees no feedback when their click is silently
    dropped due to lock timeout.

    Fix: trigger_reconnect should check if a reconnect is already in
    progress (lock.locked()) and return an informative message instead of
    spawning a thread that will likely time out.
    """

    @patch("orchestrator.session.reconnect.reconnect_remote_worker")
    @patch("orchestrator.session.reconnect.os.makedirs")
    @patch("orchestrator.terminal.manager.subprocess")
    def test_second_reconnect_skipped_by_lock(
        self, mock_subprocess, mock_makedirs, mock_reconnect, race_db, worker_session
    ):
        """When lock is held by health-check, manual reconnect's inner call
        to reconnect_remote_worker should skip (lock.acquire timeout)."""
        from orchestrator.session.reconnect import get_reconnect_lock

        lock = get_reconnect_lock(worker_session.id)

        reconnect_entered = threading.Event()
        reconnect_proceed = threading.Event()

        # T1: Simulate health-check holding the lock and doing a slow reconnect
        def health_check_reconnect():
            lock.acquire()
            try:
                reconnect_entered.set()
                reconnect_proceed.wait(timeout=10)
            finally:
                lock.release()

        t1 = threading.Thread(target=health_check_reconnect, daemon=True)
        t1.start()
        reconnect_entered.wait(timeout=5)

        # T2: Try to acquire lock with a short timeout (simulating the real 5s timeout)
        acquired = lock.acquire(timeout=0.5)
        assert not acquired, (
            "Lock should NOT be acquirable while health-check reconnect is in progress"
        )

        # Clean up
        reconnect_proceed.set()
        t1.join(timeout=5)

    @patch("orchestrator.session.reconnect.reconnect_remote_worker")
    @patch("orchestrator.session.reconnect.os.makedirs")
    @patch("orchestrator.terminal.manager.subprocess")
    def test_both_reconnects_succeed_if_first_finishes_fast(
        self, mock_subprocess, mock_makedirs, mock_reconnect, race_db, worker_session
    ):
        """If health-check reconnect finishes quickly (<5s), a subsequent manual
        reconnect will acquire the lock and run. This is a valid but wasteful
        double-reconnect."""
        from orchestrator.session.reconnect import get_reconnect_lock

        lock = get_reconnect_lock(worker_session.id)
        calls = []

        # T1: Fast reconnect
        def fast_reconnect():
            with lock:
                calls.append("t1")

        t1 = threading.Thread(target=fast_reconnect, daemon=True)
        t1.start()
        t1.join(timeout=5)

        # T2: Should now succeed because T1 released the lock
        acquired = lock.acquire(timeout=1)
        assert acquired, "Lock should be available after T1 finishes"
        calls.append("t2")
        lock.release()

        assert "t1" in calls and "t2" in calls


# ============================================================================
# Race 2: Create new session with same name vs delete/reconnect of old session
# ============================================================================


class TestCreateVsDeleteReconnectRace:
    """User creates session 'worker-1' while an old 'worker-1' is being
    deleted or reconnected in a background thread.

    Bug scenario:
        T1 (delete_session) removes DB row, kills tmux window, cleans up dirs.
        T2 (create_session) creates a new DB row with same name, creates tmux window.
        If T2's create_session runs between T1's tmux kill and T1's repo.delete,
        T1 deletes the NEW session's DB row, leaving an orphaned tmux window.

    Severity: Data corruption. The new session exists in tmux but not in DB.
    The user sees a "session not found" error on subsequent operations.

    Fix: delete_session should atomically delete the DB row and mark the
    session as "deleting" before starting the slow cleanup. create_session
    should check for name uniqueness with an advisory lock.
    """

    def test_unique_name_constraint_prevents_duplicate(self, race_db):
        """The sessions table has a UNIQUE constraint on name.

        This means creating a session with a duplicate name fails with
        IntegrityError. This is actually GOOD -- it prevents the orphan
        scenario above. However, the user must delete the old session first.

        The race window is: if T1 (delete) hasn't committed the DELETE yet,
        T2 (create) will fail. If T1 has committed, T2 succeeds.
        """
        import sqlite3

        # Create the original session
        s1 = repo.create_session(race_db, "worker-dup", "user/rdev-vm")

        # Attempting to create a second session with the same name should fail
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint"):
            repo.create_session(race_db, "worker-dup", "user/rdev-vm")

        # After deleting the first, creating with same name succeeds
        repo.delete_session(race_db, s1.id)
        s2 = repo.create_session(race_db, "worker-dup", "user/rdev-vm")
        assert s2.id != s1.id

    def test_reconnect_during_delete_uses_stale_session(self, race_db, worker_session):
        """If trigger_reconnect reads session data just before delete removes it,
        the background reconnect thread will operate on a session that no longer
        exists in the DB."""
        session_id = worker_session.id

        # Simulate: T1 reads session, T2 deletes it, T1 tries to update status
        stale_session = repo.get_session(race_db, session_id)
        assert stale_session is not None

        # T2 deletes the session
        repo.delete_session(race_db, session_id)
        assert repo.get_session(race_db, session_id) is None

        # T1 tries to update the now-deleted session
        # update_session does UPDATE ... WHERE id = ? which silently affects 0 rows
        result = repo.update_session(race_db, session_id, status="connecting")
        # get_session returns None for the deleted row
        assert result is None, (
            "Updating a deleted session should return None because get_session "
            "at the end of update_session finds no row"
        )


# ============================================================================
# Race 3: Two concurrent health checks both decide to reconnect
# ============================================================================


class TestDualHealthCheckReconnectRace:
    """Two health check cycles (e.g., health-check-all called by two browser
    tabs or the periodic loop fires while a manual health-check-all is running)
    both find the same worker disconnected and both call trigger_reconnect.

    Bug scenario:
        T1: check_all_workers_health -> finds worker-1 disconnected
        T2: check_all_workers_health -> finds worker-1 disconnected (same stale status)
        T1: trigger_reconnect(worker-1) -> sets status=connecting, spawns bg thread
        T2: trigger_reconnect(worker-1) -> status already updated to connecting?
            No -- T2 read the session before T1's status update committed.
            T2 also spawns a bg thread. Both bg threads race on the reconnect lock.

    Severity: Wasteful (double thread), but the per-session lock prevents actual
    damage. The second thread times out after 5s and logs a warning.

    Fix: trigger_reconnect should do a compare-and-swap on status:
    UPDATE sessions SET status='connecting' WHERE id=? AND status IN ('disconnected', ...)
    If rowcount=0, another reconnect is already in flight.
    """

    def test_double_trigger_reconnect_second_thread_blocked_by_lock(self):
        """Two calls to trigger_reconnect spawn two bg threads, but the second
        is blocked by the per-session lock inside reconnect_remote_worker.

        We simulate this by having two threads compete for the per-session lock
        just as reconnect_remote_worker does internally.
        """
        from orchestrator.session.reconnect import get_reconnect_lock

        session_id = "dual-health-check-sess"
        lock = get_reconnect_lock(session_id)
        thread_results = []
        reconnect_started = threading.Event()
        reconnect_barrier = threading.Event()

        def simulated_reconnect_remote_worker(thread_name):
            """Simulates reconnect_remote_worker's lock acquisition pattern."""
            acquired = lock.acquire(timeout=5)
            if not acquired:
                thread_results.append(f"{thread_name}:skipped")
                return
            try:
                if thread_name == "t1":
                    reconnect_started.set()
                    reconnect_barrier.wait(timeout=10)
                thread_results.append(f"{thread_name}:completed")
            finally:
                lock.release()

        t1 = threading.Thread(target=simulated_reconnect_remote_worker, args=("t1",), daemon=True)
        t2 = threading.Thread(target=simulated_reconnect_remote_worker, args=("t2",), daemon=True)

        t1.start()
        reconnect_started.wait(timeout=5)
        # T1 is now holding the lock; start T2 which will block
        t2.start()
        time.sleep(0.5)
        # Release T1
        reconnect_barrier.set()

        t1.join(timeout=10)
        t2.join(timeout=10)

        assert "t1:completed" in thread_results
        # T2 should also complete (it waited for the lock and got it after T1 released)
        assert "t2:completed" in thread_results
        assert len(thread_results) == 2, f"Both threads should complete, got {thread_results}"

    def test_both_status_updates_overwrite_each_other(self, race_db):
        """When two reconnect threads both update the same session's status,
        the last writer wins (no CAS protection)."""
        s = repo.create_session(race_db, "cas-worker", "user/rdev-vm")
        repo.update_session(race_db, s.id, status="disconnected")

        # Simulate T1 and T2 both reading session as 'disconnected'
        # then both setting to 'connecting'
        repo.update_session(race_db, s.id, status="connecting")
        # T2 also sets 'connecting' (redundant but not an error)
        repo.update_session(race_db, s.id, status="connecting")

        result = repo.get_session(race_db, s.id)
        assert result.status == "connecting"


# ============================================================================
# Race 4: Reconnect starts just as tunnel_health_loop restarts the tunnel
# ============================================================================


class TestReconnectVsTunnelHealthLoopRace:
    """reconnect_remote_worker's Step 2 restarts the tunnel, but the
    tunnel_health_loop also detects the dead tunnel and restarts it
    concurrently.

    Bug scenario:
        T1 (tunnel_health_loop): tunnel_manager.restart_tunnel(sess_id, ...) -> pid=1001
        T2 (reconnect worker): tunnel_manager.restart_tunnel(sess_id, ...) -> pid=1002
        T1: repo.update_session(conn, sess_id, tunnel_pid=1001)
        T2: repo.update_session(conn, sess_id, tunnel_pid=1002)
        Result: DB has pid=1002 but pid=1001 is also running (orphaned process).

    Severity: Resource leak (orphaned SSH process). The orphaned tunnel
    continues running until manually killed or the machine reboots.

    Fix: ReverseTunnelManager.restart_tunnel should be idempotent -- if a
    tunnel was started within the last N seconds, reuse it instead of
    spawning a new one. Alternatively, restart_tunnel should stop any
    existing tunnel before starting a new one (it already does this via
    stop_tunnel(session_id), but there's still a TOCTOU gap).
    """

    def test_concurrent_tunnel_restarts_produce_different_pids(self):
        """Two concurrent restart_tunnel calls should not both succeed in
        creating separate tunnel processes. With proper locking in the tunnel
        manager, the second call reuses the first's tunnel.

        We verify the current behavior to document the race condition.
        """
        mock_tm = MagicMock()
        # Simulate two rapid restart calls returning different PIDs
        mock_tm.restart_tunnel.side_effect = [1001, 1002]

        # Two callers restart the tunnel concurrently
        pid1 = mock_tm.restart_tunnel("sess-1", "worker-1", "user/rdev-vm")
        pid2 = mock_tm.restart_tunnel("sess-1", "worker-1", "user/rdev-vm")

        # Document the bug: both callers get different PIDs
        assert pid1 != pid2, "Without idempotency, concurrent restarts create separate tunnels"
        assert mock_tm.restart_tunnel.call_count == 2

    def test_last_writer_wins_for_tunnel_pid(self, race_db, worker_session):
        """When both health check and tunnel monitor call restart_tunnel for
        the same session, the DB stores whichever tunnel_pid was written last.
        The other tunnel process is orphaned."""
        # Simulate: T1 (tunnel monitor) writes pid=1001
        repo.update_session(race_db, worker_session.id, tunnel_pid=1001)

        # T2 (reconnect) writes pid=1002
        repo.update_session(race_db, worker_session.id, tunnel_pid=1002)

        final = repo.get_session(race_db, worker_session.id)
        assert final.tunnel_pid == 1002, "Last writer wins for tunnel_pid"

    @patch("orchestrator.session.reconnect.time.sleep")
    @patch("orchestrator.session.reconnect.check_tui_running_in_pane", return_value=False)
    @patch("orchestrator.session.health.check_worker_ssh_alive", return_value=False)
    @patch("orchestrator.terminal.ssh.remote_connect")
    @patch("orchestrator.terminal.ssh.wait_for_prompt", return_value=True)
    @patch("orchestrator.session.reconnect._ensure_local_configs_exist")
    @patch("orchestrator.session.reconnect._copy_configs_to_remote")
    @patch("orchestrator.terminal.session._install_screen_if_needed", return_value=True)
    @patch(
        "orchestrator.session.reconnect.check_screen_exists_via_tmux",
        return_value=(False, False),
    )
    @patch("orchestrator.session.reconnect.safe_send_keys")
    @patch("orchestrator.session.reconnect._launch_claude_in_screen")
    @patch("orchestrator.terminal.manager.subprocess")
    @patch("orchestrator.terminal.manager.ensure_window")
    def test_reconnect_worker_restarts_dead_tunnel(
        self,
        mock_ensure_window,
        mock_subprocess,
        mock_launch,
        mock_safe_send,
        mock_screen_check,
        mock_install_screen,
        mock_copy_configs,
        mock_ensure_configs,
        mock_wait_prompt,
        mock_remote_connect,
        mock_ssh_alive,
        mock_tui,
        mock_sleep,
        race_db,
        worker_session,
    ):
        """reconnect_remote_worker restarts a dead tunnel in Step 2 and writes
        the new tunnel_pid to the DB via the repo."""
        from orchestrator.session.reconnect import reconnect_remote_worker

        mock_tm = MagicMock()
        mock_tm.restart_tunnel.return_value = 5555
        mock_tm.is_alive.return_value = False

        mock_repo = MagicMock()

        reconnect_remote_worker(
            race_db,
            worker_session,
            "orchestrator",
            "worker-1",
            8093,
            "/tmp/orchestrator/workers/worker-1",
            mock_repo,
            tunnel_manager=mock_tm,
        )

        # Verify that restart_tunnel was called (Step 2: fix tunnel if dead)
        assert mock_tm.restart_tunnel.called, "Reconnect should restart dead tunnel"
        # Verify the tunnel_pid was written to the DB via repo.update_session
        tunnel_update_calls = [
            c for c in mock_repo.update_session.call_args_list if "tunnel_pid" in str(c)
        ]
        assert len(tunnel_update_calls) >= 1, "Should update tunnel_pid in DB"


# ============================================================================
# Race 5: trigger_reconnect called twice rapidly (double-click)
# ============================================================================


class TestDoubleClickReconnectRace:
    """User double-clicks the Reconnect button, firing two API requests in
    quick succession. Both hit the /reconnect endpoint.

    Bug scenario:
        T1: reconnect_session -> trigger_reconnect -> sets status=connecting,
            spawns bg thread that calls reconnect_remote_worker
        T2: reconnect_session -> sees status=connecting (just set by T1),
            is_reconnectable("connecting") returns False -> returns error.
            OR: T2 runs before T1's status update -> also spawns bg thread.

    The 5s lock timeout is the only protection. If the reconnect takes >5s
    (which it often does for rdev workers), the second thread silently gives
    up.

    Severity: Cosmetic (confusing user feedback). No data corruption because
    the lock serializes actual reconnect work.

    Fix: trigger_reconnect should atomically check-and-set status to prevent
    double spawning. Use a CAS-style update:
    UPDATE ... SET status='connecting' WHERE id=? AND status IN (reconnectable_states)
    """

    @patch("orchestrator.terminal.ssh.is_remote_host", return_value=True)
    @patch("orchestrator.terminal.manager.subprocess")
    def test_double_click_both_calls_return_ok(
        self, mock_subprocess, mock_is_remote, race_db, worker_session
    ):
        """Both rapid trigger_reconnect calls return {"ok": True} even though
        only one will actually do the reconnect work."""
        from orchestrator.session.reconnect import trigger_reconnect

        with patch("orchestrator.session.reconnect.reconnect_remote_worker"):
            r1 = trigger_reconnect(
                worker_session,
                race_db,
                db_path=":memory:",
                api_port=8093,
            )
            r2 = trigger_reconnect(
                worker_session,
                race_db,
                db_path=":memory:",
                api_port=8093,
            )

        # Both return ok because trigger_reconnect doesn't check lock status
        assert r1["ok"] is True
        assert r2["ok"] is True

    @patch("orchestrator.terminal.ssh.is_remote_host", return_value=False)
    @patch("orchestrator.terminal.manager.subprocess")
    def test_double_click_local_worker_second_call_blocks(
        self, mock_subprocess, mock_is_remote, race_db, worker_session
    ):
        """For local workers (synchronous reconnect), the second call blocks
        until the first finishes, then may attempt reconnect again."""
        from orchestrator.session.reconnect import trigger_reconnect

        results = []
        barrier = threading.Event()

        def slow_local_reconnect(*args, **kwargs):
            barrier.wait(timeout=10)

        with patch(
            "orchestrator.session.reconnect.reconnect_local_worker",
            side_effect=slow_local_reconnect,
        ):

            def call_trigger():
                try:
                    r = trigger_reconnect(
                        worker_session,
                        race_db,
                        db_path=None,
                        api_port=8093,
                    )
                    results.append(r)
                except Exception as e:
                    results.append({"ok": False, "error": str(e)})

            t1 = threading.Thread(target=call_trigger, daemon=True)
            t2 = threading.Thread(target=call_trigger, daemon=True)
            t1.start()
            time.sleep(0.2)  # Ensure T1 starts first
            t2.start()

            # Give T2 time to block on the lock inside reconnect_local_worker
            time.sleep(0.5)
            barrier.set()

            t1.join(timeout=10)
            t2.join(timeout=10)

        assert len(results) == 2, f"Both calls should complete, got {len(results)}"

    def test_lock_timeout_behavior(self):
        """Verify the 5s lock timeout means a second reconnect attempt is
        silently dropped if the first takes longer than 5 seconds."""
        from orchestrator.session.reconnect import get_reconnect_lock

        lock = get_reconnect_lock("test-timeout-sess")
        lock.acquire()

        # Second acquire with 0.5s timeout (simulating the 5s real timeout scaled down)
        start = time.monotonic()
        acquired = lock.acquire(timeout=0.5)
        elapsed = time.monotonic() - start

        assert not acquired, "Should fail to acquire held lock"
        assert elapsed >= 0.4, "Should have waited close to the timeout"

        lock.release()


# ============================================================================
# Race 6: Background reconnect thread updates DB while main thread reads stale data
# ============================================================================


class TestStaleDBReadRace:
    """Background reconnect thread updates session status in its own DB
    connection, while the main thread (API handler) reads the session
    from a different connection/snapshot.

    Bug scenario:
        T1 (bg reconnect): reconnect succeeds -> update_session(status="waiting")
        T2 (API /sessions): reads session -> sees status="connecting" (stale)
        User sees "connecting" in the UI even though worker is already "waiting".

    Severity: Cosmetic (stale status display). Self-correcting on next poll.
    With SQLite WAL mode, readers see a consistent snapshot but may miss
    recent writes from other connections.

    Fix: This is inherent to SQLite's isolation model and mostly harmless.
    Could add a version/timestamp field and have the UI poll until it changes.
    """

    def test_stale_read_after_bg_update(self, race_db):
        """Demonstrate that reading from the same connection after a commit
        on that connection sees the updated value.

        Note: In the real system, the bg thread opens its OWN connection,
        so the main thread's connection may not see the update until its
        next transaction begins.
        """
        s = repo.create_session(race_db, "stale-worker", "user/rdev-vm")
        repo.update_session(race_db, s.id, status="connecting")

        # Simulate bg thread completing reconnect on same connection
        repo.update_session(race_db, s.id, status="waiting")

        # Read back
        updated = repo.get_session(race_db, s.id)
        assert updated.status == "waiting", "Same-connection read should see committed update"

    def test_status_transitions_during_reconnect(self, race_db):
        """Track the full sequence of status transitions during a reconnect
        lifecycle to verify no intermediate states are lost."""
        s = repo.create_session(race_db, "transition-worker", "user/rdev-vm")
        repo.update_session(race_db, s.id, status="disconnected")

        transitions = []

        def track_status(conn, sid, **kwargs):
            if "status" in kwargs and kwargs["status"] is not None:
                transitions.append(kwargs["status"])
            return repo.update_session(conn, sid, **kwargs)

        # Simulate the trigger_reconnect -> reconnect_remote_worker flow:
        # 1. trigger_reconnect sets "connecting"
        track_status(race_db, s.id, status="connecting")

        # 2. reconnect_remote_worker on success sets "waiting"
        track_status(race_db, s.id, status="waiting")

        assert transitions == ["connecting", "waiting"], (
            f"Expected ['connecting', 'waiting'], got {transitions}"
        )

        final = repo.get_session(race_db, s.id)
        assert final.status == "waiting"


# ============================================================================
# Race: Reconnect lock cleanup vs concurrent reconnect
# ============================================================================


class TestLockCleanupRace:
    """Session delete calls cleanup_reconnect_lock while a reconnect is
    in progress using that same lock.

    Bug scenario:
        T1 (reconnect): holding the lock, performing reconnect operations
        T2 (delete): calls cleanup_reconnect_lock(session_id)
           -> removes the lock object from _reconnect_locks dict
        T1: finishes reconnect, calls lock.release()
           -> this works fine (lock object still exists in T1's scope)
        T3 (new reconnect): calls get_reconnect_lock(session_id)
           -> creates a NEW lock object (old one was removed)
           -> T3 acquires the new lock while T1's old lock is still in use
           -> Two reconnects for same session can now run concurrently

    Severity: Potential concurrent reconnects if timing is just right.
    Unlikely in practice since session delete typically kills the worker,
    making further reconnects fail anyway.
    """

    def test_lock_cleanup_during_active_reconnect(self):
        """Demonstrate that cleaning up a lock while it's held creates a
        new lock on next get_reconnect_lock, potentially allowing concurrent
        access."""
        from orchestrator.session.reconnect import (
            cleanup_reconnect_lock,
            get_reconnect_lock,
        )

        session_id = "cleanup-race-sess"
        lock1 = get_reconnect_lock(session_id)
        lock1.acquire()

        # Delete session cleans up the lock registry entry
        cleanup_reconnect_lock(session_id)

        # New get_reconnect_lock creates a DIFFERENT lock object
        lock2 = get_reconnect_lock(session_id)
        assert lock2 is not lock1, "After cleanup, a new lock object should be created"

        # The new lock is NOT held -- a second reconnect could proceed
        acquired = lock2.acquire(timeout=0)
        assert acquired, (
            "New lock is not held, so concurrent reconnect is possible "
            "even though the old lock is still held by the first reconnect"
        )
        lock2.release()
        lock1.release()


# ============================================================================
# Race: Health check auto-reconnect with tunnel monitor
# ============================================================================


class TestHealthCheckVsTunnelMonitor:
    """check_and_update_worker_health detects a dead tunnel and calls
    tunnel_manager.restart_tunnel. Meanwhile tunnel_health_loop also
    detects the same dead tunnel and restarts it.

    Both write different tunnel_pids to the DB for the same session.
    """

    def test_health_check_and_tunnel_monitor_both_restart_tunnel(self, race_db, worker_session):
        """Both health check and tunnel monitor call restart_tunnel for
        the same session, resulting in two tunnel processes."""
        mock_tm = MagicMock()
        pids_assigned = []

        def track_restart(session_id, name, host):
            pid = 9000 + len(pids_assigned)
            pids_assigned.append(pid)
            return pid

        mock_tm.restart_tunnel.side_effect = track_restart
        mock_tm.is_alive.return_value = False

        # Simulate health check restart
        pid1 = mock_tm.restart_tunnel(worker_session.id, worker_session.name, worker_session.host)
        repo.update_session(race_db, worker_session.id, tunnel_pid=pid1)

        # Simulate tunnel monitor restart (happens concurrently)
        pid2 = mock_tm.restart_tunnel(worker_session.id, worker_session.name, worker_session.host)
        repo.update_session(race_db, worker_session.id, tunnel_pid=pid2)

        # DB should have the LAST writer's PID
        final = repo.get_session(race_db, worker_session.id)
        assert final.tunnel_pid == pid2, "Last writer wins for tunnel_pid"
        assert pid1 != pid2, "Two restarts should produce different PIDs"
        assert len(pids_assigned) == 2, "Two tunnel processes were started (one is orphaned)"


# ============================================================================
# Race: trigger_reconnect sets 'connecting' but bg thread fails immediately
# ============================================================================


class TestConnectingStatusStuckRace:
    """trigger_reconnect sets status='connecting' on the main thread, then
    spawns a bg thread. If the bg thread crashes before updating status,
    the session is stuck in 'connecting' forever.

    The health check has a 10-minute timeout for stuck 'connecting' sessions,
    but that's a long time for the user to wait.

    The bg thread opens a SEPARATE DB connection (via db_path), so its error
    handler writing status='disconnected' goes to a different DB. With
    :memory: as db_path, each get_connection creates a brand new empty database,
    meaning the bg thread's recovery write is completely lost.
    """

    @patch("orchestrator.terminal.ssh.is_remote_host", return_value=True)
    @patch("orchestrator.terminal.manager.subprocess")
    def test_session_stuck_connecting_when_bg_uses_separate_db(
        self, mock_subprocess, mock_is_remote, race_db, worker_session
    ):
        """Demonstrate that the main thread's DB shows 'connecting' even after
        the bg thread crashes and tries to write 'disconnected' to its own
        separate DB connection."""
        from orchestrator.session.reconnect import trigger_reconnect

        # trigger_reconnect sets status=connecting in race_db, then spawns
        # a bg thread. The bg thread opens a NEW :memory: connection (empty DB)
        # and the error handler's update_session fails silently.

        def crashing_reconnect(*args, **kwargs):
            raise RuntimeError("SSH binary not found")

        bg_threads = []
        real_thread_cls = threading.Thread  # Save before patching

        with patch(
            "orchestrator.session.reconnect.reconnect_remote_worker",
            side_effect=crashing_reconnect,
        ):
            with patch("orchestrator.session.reconnect.threading.Thread") as mock_t:

                def capture_and_start(**kwargs):
                    t = real_thread_cls(target=kwargs["target"], daemon=True)
                    bg_threads.append(t)
                    m = MagicMock()
                    m.start = t.start  # trigger_reconnect will call this
                    return m

                mock_t.side_effect = capture_and_start

                trigger_reconnect(
                    worker_session,
                    race_db,
                    db_path=":memory:",
                    api_port=8093,
                )

        # Thread was already started by trigger_reconnect via m.start = t.start
        for t in bg_threads:
            t.join(timeout=5)

        # Main thread's DB still shows 'connecting' because the bg thread
        # wrote to a different (empty) in-memory DB
        final = repo.get_session(race_db, worker_session.id)
        assert final.status == "connecting", (
            "Status should be stuck at 'connecting' because the bg thread's "
            "error handler wrote to a separate DB connection"
        )

    @patch("orchestrator.terminal.ssh.is_remote_host", return_value=True)
    @patch("orchestrator.terminal.manager.subprocess")
    def test_bg_thread_recovery_works_with_shared_db(
        self, mock_subprocess, mock_is_remote, race_db, worker_session
    ):
        """When the bg thread shares the same DB connection (db_path=None),
        the error handler successfully sets status='disconnected'."""
        from orchestrator.session.reconnect import trigger_reconnect

        def crashing_reconnect(*args, **kwargs):
            raise RuntimeError("SSH binary not found")

        bg_threads = []
        real_thread_cls = threading.Thread  # Save before patching

        with patch(
            "orchestrator.session.reconnect.reconnect_remote_worker",
            side_effect=crashing_reconnect,
        ):
            with patch("orchestrator.session.reconnect.threading.Thread") as mock_t:

                def capture_and_start(**kwargs):
                    t = real_thread_cls(target=kwargs["target"], daemon=True)
                    bg_threads.append(t)
                    m = MagicMock()
                    m.start = t.start  # trigger_reconnect will call this
                    return m

                mock_t.side_effect = capture_and_start

                # db_path=None means bg thread reuses the same connection
                trigger_reconnect(
                    worker_session,
                    race_db,
                    db_path=None,
                    api_port=8093,
                )

        # Thread was already started by trigger_reconnect via m.start = t.start
        for t in bg_threads:
            t.join(timeout=5)

        # When sharing the DB, the error handler successfully updates status
        final = repo.get_session(race_db, worker_session.id)
        assert final.status == "disconnected", (
            "With shared DB, bg thread's error handler should set 'disconnected'"
        )


# ============================================================================
# Race: Per-session lock is NOT reentrant
# ============================================================================


class TestLockReentrancy:
    """The per-session lock (threading.Lock) is NOT reentrant.

    If any code path accidentally calls get_reconnect_lock and tries to
    acquire it while already holding it (e.g., a reconnect function that
    calls another function that also tries to lock), it will deadlock.

    This documents the design choice and verifies the behavior.
    """

    def test_lock_is_not_reentrant(self):
        """threading.Lock cannot be acquired twice by the same thread."""
        from orchestrator.session.reconnect import get_reconnect_lock

        lock = get_reconnect_lock("reentrant-test")
        lock.acquire()

        # Attempting to acquire again from the same thread should fail/block
        acquired = lock.acquire(timeout=0.2)
        assert not acquired, (
            "threading.Lock is not reentrant; second acquire should fail. "
            "If this passes, the lock was changed to RLock which changes "
            "the concurrency semantics."
        )

        lock.release()

    def test_rlock_would_be_reentrant(self):
        """Contrast: threading.RLock IS reentrant. This documents what would
        happen if the lock type were changed."""
        rlock = threading.RLock()
        rlock.acquire()
        acquired = rlock.acquire(timeout=0.2)
        assert acquired, "RLock should allow reentrant acquisition"
        rlock.release()
        rlock.release()


# ============================================================================
# Integration: Full trigger_reconnect race with real DB
# ============================================================================


class TestTriggerReconnectIntegrationRace:
    """End-to-end test of trigger_reconnect under concurrent access
    using a real in-memory SQLite database."""

    @patch("orchestrator.terminal.ssh.is_remote_host", return_value=True)
    @patch("orchestrator.terminal.manager.subprocess")
    def test_multiple_reconnects_serialize_via_lock(self, mock_subprocess, mock_is_remote, race_db):
        """Multiple trigger_reconnect calls for the same session should
        serialize through the per-session lock, not run in parallel.

        We mock reconnect_remote_worker to acquire the per-session lock
        (just like the real function does) so that serialization is tested.
        """
        from orchestrator.session.reconnect import get_reconnect_lock, trigger_reconnect

        s = repo.create_session(race_db, "race-worker", "user/rdev-vm")
        repo.update_session(race_db, s.id, status="disconnected")
        session = repo.get_session(race_db, s.id)

        execution_order = []
        tracking_lock = threading.Lock()

        def tracked_reconnect(conn, sess, ts, tw, api_port, td, r, **kwargs):
            """Mock that acquires the per-session lock like the real function."""
            lock = get_reconnect_lock(sess.id)
            if not lock.acquire(timeout=5):
                with tracking_lock:
                    execution_order.append(("skipped", threading.current_thread().name))
                return
            try:
                with tracking_lock:
                    execution_order.append(("start", threading.current_thread().name))
                time.sleep(0.3)  # Simulate work
                with tracking_lock:
                    execution_order.append(("end", threading.current_thread().name))
            finally:
                lock.release()

        with patch(
            "orchestrator.session.reconnect.reconnect_remote_worker",
            side_effect=tracked_reconnect,
        ):
            # Launch 3 concurrent reconnects
            for _ in range(3):
                r = trigger_reconnect(
                    session,
                    race_db,
                    db_path=":memory:",
                    api_port=8093,
                )
                assert r["ok"] is True

        # Wait for all bg threads to finish (daemon threads + lock contention)
        time.sleep(8)

        # Count how many actually started
        starts = [e for e in execution_order if e[0] == "start"]

        # With a 5s lock timeout and 0.3s work per reconnect, all 3 should
        # complete (first takes 0.3s, second waits 0.3s then takes 0.3s, etc.)
        assert len(starts) >= 1, "At least one reconnect should have started"

        # Verify no two "start" entries appear without an intervening "end"
        active = 0
        max_concurrent = 0
        for event_type, _ in execution_order:
            if event_type == "start":
                active += 1
                max_concurrent = max(max_concurrent, active)
            elif event_type == "end":
                active -= 1

        assert max_concurrent <= 1, (
            f"At most 1 reconnect should run at a time, but saw {max_concurrent} "
            f"concurrent. Execution order: {execution_order}"
        )


# ============================================================================
# Race: auto_reconnect toggle vs health check
# ============================================================================


class TestAutoReconnectToggleRace:
    """User toggles auto_reconnect ON while a health check is running.

    Bug scenario:
        T1 (health check): reads session with auto_reconnect=False
        T2 (toggle API): sets auto_reconnect=True, triggers reconnect
        T1: finishes health check, sees disconnected but auto_reconnect=False
            (stale read), does NOT add to auto_reconnect_candidates
        Result: T2's reconnect runs, health check skips. Mostly harmless,
        but the user might see a brief status flicker.
    """

    def test_toggle_during_health_check_stale_read(self, race_db):
        """Health check reads auto_reconnect=False, then user toggles it ON.
        Health check doesn't see the new value for this cycle."""
        s = repo.create_session(race_db, "toggle-worker", "user/rdev-vm")
        repo.update_session(race_db, s.id, status="disconnected", auto_reconnect=False)

        # T1: Health check reads session
        stale = repo.get_session(race_db, s.id)
        assert stale.auto_reconnect is False

        # T2: User toggles auto_reconnect on
        repo.update_session(race_db, s.id, auto_reconnect=True)

        # T1 still has the stale session object
        assert stale.auto_reconnect is False, (
            "Stale session object should still show auto_reconnect=False"
        )

        # But a fresh read shows the new value
        fresh = repo.get_session(race_db, s.id)
        assert fresh.auto_reconnect is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
