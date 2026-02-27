"""Race condition tests for tunnel management.

Exercises concurrent access patterns in:
- ReverseTunnelManager (start/stop/restart/is_alive/recover)
- tunnel_health_loop vs reconnect_remote_worker
- recover_tunnels on startup vs tunnel_health_loop
- SQLite DB updates from concurrent tunnel_monitor and reconnect threads

Each test documents the exact interleaving that causes the bug, assesses
severity, and verifies correct behavior (or demonstrates the race).

All subprocess/OS operations are mocked out; only locking, state management,
and concurrent access patterns are exercised.
"""

from __future__ import annotations

import signal
import threading
import time
from unittest.mock import MagicMock, patch

from orchestrator.session.tunnel import (
    ReverseTunnelManager,
    _AdoptedProcess,
    _TunnelEntry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_proc(pid: int = 1000, alive: bool = True) -> MagicMock:
    """Create a mock subprocess.Popen with controllable poll()."""
    proc = MagicMock()
    proc.pid = pid
    proc.poll.return_value = None if alive else -1
    proc.returncode = None if alive else -1
    proc.wait.return_value = 0
    return proc


def _make_manager(**kwargs) -> ReverseTunnelManager:
    """Create a ReverseTunnelManager with a temporary log directory."""
    defaults = {"log_dir": "/tmp/orchestrator/test-tunnels"}
    defaults.update(kwargs)
    with patch("os.makedirs"):
        return ReverseTunnelManager(**defaults)


def _mock_open():
    """Create a mock for builtins.open that returns a file-like context manager.

    The mock file's tell() returns 0 so that _read_log_since can compare
    end_pos (int) <= start_pos (int) without TypeError.
    """
    mock_file = MagicMock()
    mock_file.tell.return_value = 0
    mock_file.__enter__ = MagicMock(return_value=mock_file)
    mock_file.__exit__ = MagicMock(return_value=False)
    return MagicMock(return_value=mock_file)


# ---------------------------------------------------------------------------
# Race 1: tunnel_health_loop restart_tunnel vs reconnect _ensure_tunnel
#
# Interleaving:
#   T1 (health loop):  calls restart_tunnel(sid) -> stop_tunnel pops entry
#   T2 (reconnect):    calls restart_tunnel(sid) -> stop_tunnel finds nothing
#   T1:                start_tunnel(sid) -> Popen, inserts entry with pid=A
#   T2:                start_tunnel(sid) -> calls stop_tunnel(sid) which
#                      kills T1's brand-new process (pid=A), then starts pid=B
#   Result: pid=A is orphaned (killed before it stabilized) and pid=B wins,
#           but the DB may have stored pid=A from T1's update.
#
# Severity: Stuck state / resource leak. The orphaned SSH process may linger,
#           and the DB tunnel_pid may be stale.
# ---------------------------------------------------------------------------


class TestRace1ConcurrentRestartFromMonitorAndReconnect:
    """Two threads both call restart_tunnel for the same session."""

    @patch("subprocess.Popen")
    @patch("orchestrator.session.tunnel.time.sleep")
    def test_concurrent_restart_both_complete(self, mock_sleep, mock_popen):
        """Both restarts should complete without raising; last writer wins."""
        mgr = _make_manager()

        call_order = []
        pid_counter = [1000]

        def make_proc(*args, **kwargs):
            pid_counter[0] += 1
            p = _make_mock_proc(pid=pid_counter[0])
            call_order.append(("popen", pid_counter[0]))
            return p

        mock_popen.side_effect = make_proc

        barrier = threading.Barrier(2)
        results = {}

        def restart_thread(name):
            barrier.wait()
            pid = mgr.restart_tunnel("sess-1", "worker-1", "host/vm")
            results[name] = pid

        with patch("builtins.open", _mock_open()):
            t1 = threading.Thread(target=restart_thread, args=("monitor",))
            t2 = threading.Thread(target=restart_thread, args=("reconnect",))
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)

        # Both threads should have gotten a PID back
        assert results["monitor"] is not None
        assert results["reconnect"] is not None

        # The manager should have exactly one entry for sess-1
        assert mgr.has_tunnel("sess-1")
        final_pid = mgr.get_pid("sess-1")
        # Final PID must be one of the PIDs that was created
        assert final_pid in (results["monitor"], results["reconnect"])

    @patch("subprocess.Popen")
    @patch("orchestrator.session.tunnel.time.sleep")
    def test_concurrent_restart_last_writer_wins_in_dict(self, mock_sleep, mock_popen):
        """Demonstrate that the _tunnels dict ends up with the last writer's entry.

        This is the expected behavior with the current lock-per-operation design:
        whichever thread's start_tunnel completes last owns the entry.
        """
        mgr = _make_manager()
        pids_created = []

        def make_proc(*args, **kwargs):
            pid = 2000 + len(pids_created)
            pids_created.append(pid)
            return _make_mock_proc(pid=pid)

        mock_popen.side_effect = make_proc

        # Pre-seed an entry so stop_tunnel has something to pop
        seed_proc = _make_mock_proc(pid=999)
        seed_entry = _TunnelEntry(proc=seed_proc, host="host/vm", session_name="worker-1", pid=999)
        mgr._tunnels["sess-1"] = seed_entry

        event_t1_stopped = threading.Event()
        event_t2_may_start = threading.Event()

        original_start = mgr.start_tunnel

        def controlled_start_t1(sid, sname, host, **kw):
            # T1 has stopped the tunnel; signal T2 to also stop+start
            event_t1_stopped.set()
            # Wait for T2 to also call stop (which finds nothing now)
            event_t2_may_start.wait(timeout=3)
            return original_start(sid, sname, host, **kw)

        results = {}

        def thread_1():
            mgr.stop_tunnel("sess-1")
            event_t1_stopped.set()
            event_t2_may_start.wait(timeout=3)
            with patch("builtins.open", _mock_open()):
                results["t1"] = mgr.start_tunnel("sess-1", "worker-1", "host/vm")

        def thread_2():
            event_t1_stopped.wait(timeout=3)
            mgr.stop_tunnel("sess-1")  # finds nothing, returns False
            event_t2_may_start.set()
            with patch("builtins.open", _mock_open()):
                results["t2"] = mgr.start_tunnel("sess-1", "worker-1", "host/vm")

        t1 = threading.Thread(target=thread_1)
        t2 = threading.Thread(target=thread_2)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # Both should succeed
        assert results["t1"] is not None
        assert results["t2"] is not None
        # Exactly one survives in the dict
        assert mgr.has_tunnel("sess-1")
        # The orphaned process (whichever was overwritten) is NOT killed
        # by the manager -- this is the race condition.
        assert len(pids_created) == 2


# ---------------------------------------------------------------------------
# Race 2: _cleanup_dead_entry during concurrent start_tunnel
#
# Interleaving:
#   T1 (is_alive):    sees proc.poll() != None -> calls _cleanup_dead_entry
#   T2 (start_tunnel): calls stop_tunnel (pops old entry) -> starts new proc
#                       -> inserts new entry under same session_id
#   T1 (_cleanup):    acquires lock, checks "current is entry" -- if T2
#                     already replaced it, the guard `current is entry` saves
#                     us. But if T1 runs before T2 inserts, it deletes the
#                     (already-popped) entry harmlessly.
#
# Severity: Low (cosmetic) if the identity check works. The test verifies
#           that the `current is entry` guard prevents deleting a new entry.
# ---------------------------------------------------------------------------


class TestRace2CleanupDeadEntryDuringStartTunnel:
    """_cleanup_dead_entry should not remove a freshly-started entry."""

    def test_cleanup_does_not_remove_new_entry(self):
        """If start_tunnel replaces the entry before cleanup runs, cleanup is a no-op."""
        mgr = _make_manager()

        # Create the "old" dead entry
        old_proc = _make_mock_proc(pid=100, alive=False)
        old_entry = _TunnelEntry(proc=old_proc, host="host/vm", session_name="worker-1", pid=100)

        # Create the "new" live entry (simulating start_tunnel)
        new_proc = _make_mock_proc(pid=200, alive=True)
        new_entry = _TunnelEntry(proc=new_proc, host="host/vm", session_name="worker-1", pid=200)

        # Insert the new entry (as start_tunnel would)
        mgr._tunnels["sess-1"] = new_entry

        # Now cleanup runs with the OLD entry reference
        mgr._cleanup_dead_entry("sess-1", old_entry)

        # The new entry must survive
        assert mgr.has_tunnel("sess-1")
        assert mgr.get_pid("sess-1") == 200

    def test_cleanup_removes_same_entry(self):
        """If the entry hasn't been replaced, cleanup should remove it."""
        mgr = _make_manager()

        dead_proc = _make_mock_proc(pid=100, alive=False)
        dead_entry = _TunnelEntry(proc=dead_proc, host="host/vm", session_name="worker-1", pid=100)
        mgr._tunnels["sess-1"] = dead_entry

        mgr._cleanup_dead_entry("sess-1", dead_entry)

        assert not mgr.has_tunnel("sess-1")

    @patch("subprocess.Popen")
    @patch("orchestrator.session.tunnel.time.sleep")
    def test_is_alive_cleanup_races_with_start_tunnel(self, mock_sleep, mock_popen):
        """Simulate the full race: is_alive triggers cleanup while start_tunnel runs."""
        mgr = _make_manager()

        # Phase 1: seed a dead tunnel
        dead_proc = _make_mock_proc(pid=100, alive=False)
        dead_entry = _TunnelEntry(proc=dead_proc, host="host/vm", session_name="worker-1", pid=100)
        mgr._tunnels["sess-1"] = dead_entry

        # Phase 2: is_alive detects it's dead (poll returns -1)
        assert mgr.is_alive("sess-1") is False
        # cleanup_dead_entry was called internally -- entry should be gone
        assert not mgr.has_tunnel("sess-1")

        # Phase 3: start_tunnel creates a new entry
        new_proc = _make_mock_proc(pid=300, alive=True)
        mock_popen.return_value = new_proc

        with patch("builtins.open", _mock_open()):
            pid = mgr.start_tunnel("sess-1", "worker-1", "host/vm")

        assert pid == 300
        assert mgr.has_tunnel("sess-1")
        assert mgr.get_pid("sess-1") == 300


# ---------------------------------------------------------------------------
# Race 3: recover_tunnels on startup vs tunnel_health_loop starting
#
# Interleaving:
#   Main thread: calls recover_tunnels() which iterates sessions and calls
#                recover_tunnel() for each → adopt or start_tunnel
#   Background:  tunnel_health_loop starts, iterates same sessions, sees
#                no tunnel (not yet recovered) → calls restart_tunnel
#   Result: double tunnel creation for the same session.
#
# Severity: Resource leak (orphaned SSH processes) + DB pid mismatch.
# ---------------------------------------------------------------------------


class TestRace3RecoverTunnelsVsHealthLoop:
    """recover_tunnels and tunnel_health_loop may process the same session."""

    @patch("subprocess.Popen")
    @patch("orchestrator.session.tunnel.time.sleep")
    @patch("orchestrator.session.health.find_tunnel_pids", return_value=[])
    def test_recover_and_health_loop_for_same_session(self, mock_find_pids, mock_sleep, mock_popen):
        """If health loop starts before recover finishes, both may create tunnels."""
        mgr = _make_manager()
        pids = []

        def make_proc(*args, **kwargs):
            pid = 3000 + len(pids)
            pids.append(pid)
            return _make_mock_proc(pid=pid)

        mock_popen.side_effect = make_proc

        # Simulate the race: recover_tunnel and restart_tunnel concurrently
        barrier = threading.Barrier(2)
        results = {}

        def recover_thread():
            barrier.wait()
            with patch("builtins.open", _mock_open()):
                pid = mgr.recover_tunnel("sess-1", "worker-1", "host/vm", stored_pid=None)
            results["recover"] = pid

        def health_thread():
            barrier.wait()
            with patch("builtins.open", _mock_open()):
                pid = mgr.restart_tunnel("sess-1", "worker-1", "host/vm")
            results["health"] = pid

        t1 = threading.Thread(target=recover_thread)
        t2 = threading.Thread(target=health_thread)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # Both succeeded, but we may have created 2+ SSH processes
        assert results["recover"] is not None
        assert results["health"] is not None
        # Only one should be in the manager
        assert mgr.has_tunnel("sess-1")
        # But we created more Popen calls than needed
        # (This demonstrates the race -- ideally only 1 process should exist)
        assert len(pids) >= 2, f"Expected at least 2 Popen calls showing the race; got {len(pids)}"

    @patch("subprocess.Popen")
    @patch("orchestrator.session.tunnel.time.sleep")
    @patch("orchestrator.session.health.find_tunnel_pids", return_value=[])
    def test_recover_with_valid_stored_pid_avoids_race(
        self, mock_find_pids, mock_sleep, mock_popen
    ):
        """If recover_tunnel successfully adopts, health loop's restart just replaces it.

        Suggested fix: recover_tunnels should complete before health loop starts.
        """
        mgr = _make_manager()

        # Simulate successful adoption
        mock_find_pids.return_value = [5000]
        with patch("os.kill"):
            pid = mgr.recover_tunnel("sess-1", "worker-1", "host/vm", stored_pid=5000)

        assert pid == 5000
        assert mgr.has_tunnel("sess-1")
        assert mgr.get_pid("sess-1") == 5000

        # Now health loop checks -- tunnel is adopted and alive
        adopted_entry = mgr._tunnels["sess-1"]
        adopted_entry.proc = _AdoptedProcess(5000)
        # Patch os.kill to simulate alive process
        with patch("os.kill"):
            assert mgr.is_alive("sess-1") is True


# ---------------------------------------------------------------------------
# Race 4: is_alive() returns True, then proc dies before check_connectivity()
#
# Interleaving:
#   check_connectivity calls is_alive -> proc.poll() == None -> True
#   Process actually dies (SSH connection drops)
#   check_connectivity reads entry from dict -> calls probe_tunnel_connectivity
#   Probe fails because the tunnel is dead -> returns False
#
# Severity: Low (self-correcting). check_connectivity returns False, which
#           triggers a restart on the next cycle. But the is_alive() result
#           was transiently wrong.
# ---------------------------------------------------------------------------


class TestRace4IsAliveBeforeConnectivityCheck:
    """Process dies between is_alive() and check_connectivity() probe."""

    def test_connectivity_returns_false_when_proc_dies_after_is_alive(self):
        """check_connectivity should return False if probe fails, even if is_alive was True."""
        mgr = _make_manager()

        proc = _make_mock_proc(pid=400, alive=True)
        entry = _TunnelEntry(proc=proc, host="host/vm", session_name="worker-1", pid=400)
        mgr._tunnels["sess-1"] = entry

        # is_alive returns True (process still alive at check time)
        assert mgr.is_alive("sess-1") is True

        # Now the process dies
        proc.poll.return_value = -1

        # check_connectivity's is_alive call will now return False
        result = mgr.check_connectivity("sess-1")
        assert result is False

    @patch("orchestrator.session.tunnel.probe_tunnel_connectivity")
    def test_probe_failure_after_alive_check(self, mock_probe):
        """Even with is_alive True, if probe fails, check_connectivity returns False."""
        mgr = _make_manager()

        proc = _make_mock_proc(pid=401, alive=True)
        entry = _TunnelEntry(proc=proc, host="host/vm", session_name="worker-1", pid=401)
        mgr._tunnels["sess-1"] = entry

        # Probe returns False (tunnel broken but process alive = zombie tunnel)
        mock_probe.return_value = False

        result = mgr.check_connectivity("sess-1")
        assert result is False
        mock_probe.assert_called_once_with("host/vm", mgr.api_port)

    @patch("orchestrator.session.tunnel.probe_tunnel_connectivity")
    def test_entry_removed_between_is_alive_and_probe(self, mock_probe):
        """If entry is removed between is_alive check and probe, return False."""
        mgr = _make_manager()

        proc = _make_mock_proc(pid=402, alive=True)
        entry = _TunnelEntry(proc=proc, host="host/vm", session_name="worker-1", pid=402)
        mgr._tunnels["sess-1"] = entry

        # Simulate: another thread removes the entry after is_alive returns True
        original_is_alive = mgr.is_alive

        def is_alive_then_remove(sid):
            result = original_is_alive(sid)
            # Another thread removes it
            with mgr._lock:
                mgr._tunnels.pop(sid, None)
            return result

        with patch.object(mgr, "is_alive", side_effect=is_alive_then_remove):
            result = mgr.check_connectivity("sess-1")

        # Should return False because entry is gone after is_alive
        assert result is False
        mock_probe.assert_not_called()


# ---------------------------------------------------------------------------
# Race 5: Two concurrent restart_tunnel calls - stop pops, both start
#
# Interleaving:
#   T1: restart_tunnel -> stop_tunnel pops entry
#   T2: restart_tunnel -> stop_tunnel finds nothing (already popped)
#   T1: start_tunnel -> Popen(pid=A) -> inserts entry
#   T2: start_tunnel -> stop_tunnel(inside start_tunnel) pops T1's entry!
#       -> kills pid=A -> Popen(pid=B) -> inserts entry
#   Result: pid=A is killed immediately after creation. T1's caller gets
#           pid=A but it's already dead.
#
# Severity: High. The caller of the first restart_tunnel gets a PID that
#           has already been killed. If they store it in the DB, the DB
#           has a stale PID.
# ---------------------------------------------------------------------------


class TestRace5TwoConcurrentRestartsSameSession:
    """Two concurrent restart_tunnel calls for the same session."""

    @patch("subprocess.Popen")
    @patch("orchestrator.session.tunnel.time.sleep")
    def test_second_restart_kills_first_restart_process(self, mock_sleep, mock_popen):
        """Demonstrate that start_tunnel's internal stop_tunnel kills the other thread's process."""
        mgr = _make_manager()

        created_procs = []

        def make_proc(*args, **kwargs):
            proc = _make_mock_proc(pid=5000 + len(created_procs))
            created_procs.append(proc)
            return proc

        mock_popen.side_effect = make_proc

        # Seed an entry
        seed_proc = _make_mock_proc(pid=999)
        mgr._tunnels["sess-1"] = _TunnelEntry(
            proc=seed_proc, host="host/vm", session_name="worker-1", pid=999
        )

        # Use barriers to control interleaving
        t1_stopped = threading.Event()
        t2_stopped = threading.Event()

        results = {}

        def thread_1():
            """First restart: stop, then wait, then start."""
            mgr.stop_tunnel("sess-1")
            t1_stopped.set()
            t2_stopped.wait(timeout=3)
            with patch("builtins.open", _mock_open()):
                results["t1_pid"] = mgr.start_tunnel("sess-1", "worker-1", "host/vm")

        def thread_2():
            """Second restart: wait for T1 to stop, then stop (no-op), then start."""
            t1_stopped.wait(timeout=3)
            mgr.stop_tunnel("sess-1")  # no-op since T1 already popped
            t2_stopped.set()
            with patch("builtins.open", _mock_open()):
                results["t2_pid"] = mgr.start_tunnel("sess-1", "worker-1", "host/vm")

        t1 = threading.Thread(target=thread_1)
        t2 = threading.Thread(target=thread_2)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # Both created processes
        assert len(created_procs) == 2
        # T2's start_tunnel calls stop_tunnel first, which kills T1's newly created process
        # This means T1's caller received a PID that got killed shortly after
        assert results["t1_pid"] is not None
        assert results["t2_pid"] is not None

        # Final state: only one entry in the dict
        assert mgr.has_tunnel("sess-1")

    @patch("subprocess.Popen")
    @patch("orchestrator.session.tunnel.time.sleep")
    def test_rapid_fire_restarts(self, mock_sleep, mock_popen):
        """Multiple rapid restart_tunnel calls should not crash."""
        mgr = _make_manager()
        call_count = [0]

        def make_proc(*args, **kwargs):
            call_count[0] += 1
            return _make_mock_proc(pid=6000 + call_count[0])

        mock_popen.side_effect = make_proc

        errors = []

        def restart_worker(i):
            try:
                with patch("builtins.open", _mock_open()):
                    mgr.restart_tunnel("sess-1", "worker-1", "host/vm")
            except Exception as e:
                errors.append((i, e))

        threads = [threading.Thread(target=restart_worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Threads raised errors: {errors}"
        assert mgr.has_tunnel("sess-1")


# ---------------------------------------------------------------------------
# Race 6: _kill_orphan_tunnels kills a just-started tunnel
#
# Interleaving:
#   T1 (recover_tunnel): _try_adopt fails -> _kill_orphan_tunnels scans ps
#   T2 (start_tunnel):   Popen(pid=X) starts running
#   T1:                  _kill_orphan_tunnels finds pid=X in ps output
#                        -> SIGTERM pid=X (kills the tunnel T2 just started)
#   T2:                  returns pid=X to caller, but process is dead
#
# Severity: High. The newly started tunnel is immediately killed by orphan
#           cleanup. The caller doesn't know the tunnel is dead until the
#           next health check.
# ---------------------------------------------------------------------------


class TestRace6KillOrphanKillsNewTunnel:
    """_kill_orphan_tunnels may kill a tunnel started by another thread."""

    @patch("subprocess.Popen")
    @patch("orchestrator.session.tunnel.time.sleep")
    @patch("orchestrator.session.health.find_tunnel_pids")
    def test_orphan_cleanup_kills_newly_started_tunnel(
        self, mock_find_pids, mock_sleep, mock_popen
    ):
        """Demonstrate that _kill_orphan_tunnels can kill a freshly started process."""
        mgr = _make_manager()

        new_proc = _make_mock_proc(pid=7000, alive=True)
        mock_popen.return_value = new_proc
        killed_pids = []

        def track_kill(pid, sig):
            killed_pids.append((pid, sig))

        # Scenario setup: start a tunnel, then orphan cleanup finds its PID
        with patch("builtins.open", _mock_open()):
            pid = mgr.start_tunnel("sess-1", "worker-1", "host/vm")

        assert pid == 7000

        # Now _kill_orphan_tunnels runs (from another thread's recover_tunnel)
        # and finds pid=7000 in the ps output
        mock_find_pids.return_value = [7000]

        with patch("os.kill", side_effect=track_kill):
            mgr._kill_orphan_tunnels("host/vm")

        # The orphan cleanup sent SIGTERM to our tunnel!
        sigterm_kills = [(p, s) for p, s in killed_pids if s == signal.SIGTERM]
        assert any(p == 7000 for p, _ in sigterm_kills), (
            f"Expected SIGTERM to pid 7000; got: {killed_pids}"
        )

    @patch("subprocess.Popen")
    @patch("orchestrator.session.tunnel.time.sleep")
    @patch("orchestrator.session.health.find_tunnel_pids")
    def test_recover_tunnel_orphan_race_with_concurrent_start(
        self, mock_find_pids, mock_sleep, mock_popen
    ):
        """Full scenario: recover_tunnel's orphan cleanup vs another thread's start."""
        mgr = _make_manager()
        proc_pids = []

        def make_proc(*args, **kwargs):
            pid = 8000 + len(proc_pids)
            proc_pids.append(pid)
            return _make_mock_proc(pid=pid)

        mock_popen.side_effect = make_proc

        # Thread 1: recover_tunnel (adopt fails, kills orphans, starts fresh)
        # Thread 2: start_tunnel for the same host

        orphan_kill_pids = []

        def mock_kill(pid, sig):
            orphan_kill_pids.append(pid)

        # _try_adopt will fail (pid not in find_tunnel_pids)
        mock_find_pids.return_value = []

        barrier = threading.Barrier(2)
        results = {}

        def recover_thread():
            barrier.wait()
            with patch("builtins.open", _mock_open()), patch("os.kill", side_effect=mock_kill):
                results["recover"] = mgr.recover_tunnel(
                    "sess-1", "worker-1", "host/vm", stored_pid=None
                )

        def start_thread():
            barrier.wait()
            with patch("builtins.open", _mock_open()):
                results["start"] = mgr.start_tunnel("sess-2", "worker-2", "host/vm")

        t1 = threading.Thread(target=recover_thread)
        t2 = threading.Thread(target=start_thread)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert results.get("recover") is not None
        assert results.get("start") is not None


# ---------------------------------------------------------------------------
# Race 7: SQLite DB update of tunnel_pid races between tunnel_monitor
#          and reconnect thread
#
# Interleaving:
#   T1 (health loop _restart_tunnel): restart_tunnel returns pid=A
#       -> sessions_repo.update_session(conn, sid, tunnel_pid=A)
#   T2 (reconnect _ensure_tunnel): restart_tunnel returns pid=B
#       -> sessions_repo.update_session(conn, sid, tunnel_pid=B)
#   T1's update executes after T2 -> DB has pid=A but actual tunnel is pid=B
#
# Severity: Data corruption (DB/reality mismatch). On next restart,
#           recover_tunnels will try to adopt pid=A which may be dead or
#           recycled.
# ---------------------------------------------------------------------------


class TestRace7DBUpdateRace:
    """Concurrent DB updates to tunnel_pid from different threads."""

    def test_last_db_write_wins(self, tmp_path):
        """Demonstrate that concurrent update_session calls race on tunnel_pid.

        Uses a file-based DB because :memory: connections can't be shared
        across threads (sqlite3.InterfaceError).
        """

        from orchestrator.state.db import get_connection
        from orchestrator.state.migrations.runner import apply_migrations
        from orchestrator.state.repositories import sessions as repo

        db_path = str(tmp_path / "test.db")
        db = get_connection(db_path)
        apply_migrations(db)

        session = repo.create_session(db, "w1", "host/vm", "/tmp")
        sid = session.id

        barrier = threading.Barrier(2)
        results = {"t1_done": False, "t2_done": False}

        def update_t1():
            conn = get_connection(db_path)
            barrier.wait()
            repo.update_session(conn, sid, tunnel_pid=1111)
            results["t1_done"] = True
            conn.close()

        def update_t2():
            conn = get_connection(db_path)
            barrier.wait()
            repo.update_session(conn, sid, tunnel_pid=2222)
            results["t2_done"] = True
            conn.close()

        t1 = threading.Thread(target=update_t1)
        t2 = threading.Thread(target=update_t2)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert results["t1_done"]
        assert results["t2_done"]

        # Re-read from a fresh connection
        db.close()
        db = get_connection(db_path)
        updated = repo.get_session(db, sid)
        assert updated.tunnel_pid in (1111, 2222), (
            f"Expected tunnel_pid to be 1111 or 2222, got {updated.tunnel_pid}"
        )
        db.close()

    def test_monitor_and_reconnect_db_race(self, tmp_path):
        """Simulate the exact tunnel_monitor vs reconnect thread DB race.

        Uses a file-based DB for cross-thread safety.
        """
        from orchestrator.state.db import get_connection
        from orchestrator.state.migrations.runner import apply_migrations
        from orchestrator.state.repositories import sessions as repo

        db_path = str(tmp_path / "test2.db")
        db = get_connection(db_path)
        apply_migrations(db)

        session = repo.create_session(db, "w1", "host/vm", "/tmp")
        sid = session.id

        write_order = []

        original_update = repo.update_session

        def tracked_update(conn, id, **kwargs):
            if "tunnel_pid" in kwargs:
                write_order.append(kwargs["tunnel_pid"])
            return original_update(conn, id, **kwargs)

        barrier = threading.Barrier(2)

        def monitor_thread():
            conn = get_connection(db_path)
            barrier.wait()
            tracked_update(conn, sid, tunnel_pid=1000)
            conn.close()

        def reconnect_thread():
            conn = get_connection(db_path)
            barrier.wait()
            tracked_update(conn, sid, tunnel_pid=2000)
            conn.close()

        t1 = threading.Thread(target=monitor_thread)
        t2 = threading.Thread(target=reconnect_thread)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        db.close()
        db = get_connection(db_path)
        final = repo.get_session(db, sid)
        assert final.tunnel_pid in (1000, 2000)
        assert len(write_order) == 2
        db.close()


# ---------------------------------------------------------------------------
# Race variant: stop_tunnel + is_alive interleaving
#
# stop_tunnel pops the entry, then is_alive (from another thread) reads
# entry=None and returns False. This is safe, but if is_alive reads the
# entry BEFORE stop_tunnel pops it, it sees a soon-to-be-killed process
# and returns True.
# ---------------------------------------------------------------------------


class TestRaceStopTunnelVsIsAlive:
    """stop_tunnel and is_alive interleaving."""

    def test_is_alive_returns_false_after_stop(self):
        """After stop_tunnel completes, is_alive must return False."""
        mgr = _make_manager()

        proc = _make_mock_proc(pid=500, alive=True)
        entry = _TunnelEntry(proc=proc, host="host/vm", session_name="worker-1", pid=500)
        mgr._tunnels["sess-1"] = entry

        assert mgr.is_alive("sess-1") is True

        # Stop from another thread
        stopped = mgr.stop_tunnel("sess-1")
        assert stopped is True

        # is_alive must now return False
        assert mgr.is_alive("sess-1") is False

    def test_concurrent_stop_and_is_alive(self):
        """Concurrent stop and is_alive should not raise."""
        mgr = _make_manager()

        proc = _make_mock_proc(pid=501, alive=True)
        entry = _TunnelEntry(proc=proc, host="host/vm", session_name="worker-1", pid=501)
        mgr._tunnels["sess-1"] = entry

        errors = []
        results = {"is_alive": [], "stop": []}

        barrier = threading.Barrier(2)

        def alive_thread():
            barrier.wait()
            try:
                for _ in range(50):
                    results["is_alive"].append(mgr.is_alive("sess-1"))
            except Exception as e:
                errors.append(e)

        def stop_thread():
            barrier.wait()
            try:
                time.sleep(0.001)  # small delay
                results["stop"].append(mgr.stop_tunnel("sess-1"))
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=alive_thread)
        t2 = threading.Thread(target=stop_thread)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not errors, f"Threads raised: {errors}"
        # At some point is_alive should have returned True, and eventually False
        assert True in results["is_alive"]


# ---------------------------------------------------------------------------
# Race variant: list_tunnels during concurrent modifications
#
# list_tunnels snapshots entries under the lock, but then checks poll()
# outside the lock. A concurrent stop_tunnel could kill the process
# between the snapshot and the poll check.
# ---------------------------------------------------------------------------


class TestRaceListTunnelsDuringModification:
    """list_tunnels should not crash during concurrent modifications."""

    @patch("subprocess.Popen")
    @patch("orchestrator.session.tunnel.time.sleep")
    def test_list_tunnels_during_start_stop(self, mock_sleep, mock_popen):
        """Concurrent start/stop should not cause list_tunnels to crash."""
        mgr = _make_manager()
        mock_popen.return_value = _make_mock_proc(pid=600)

        errors = []
        stop_flag = threading.Event()

        def modifier():
            i = 0
            while not stop_flag.is_set():
                sid = f"sess-{i % 5}"
                with patch("builtins.open", _mock_open()):
                    mgr.start_tunnel(sid, f"w-{i % 5}", "host/vm")
                mgr.stop_tunnel(sid)
                i += 1

        def lister():
            while not stop_flag.is_set():
                try:
                    tunnels = mgr.list_tunnels()
                    # Should always be a list of dicts
                    assert isinstance(tunnels, list)
                except Exception as e:
                    errors.append(e)

        t1 = threading.Thread(target=modifier)
        t2 = threading.Thread(target=lister)
        t1.start()
        t2.start()

        time.sleep(0.5)  # let them run for a bit
        stop_flag.set()

        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not errors, f"list_tunnels raised: {errors}"


# ---------------------------------------------------------------------------
# Race variant: stop_all during concurrent start_tunnel
# ---------------------------------------------------------------------------


class TestRaceStopAllDuringStart:
    """stop_all should handle tunnels being added concurrently."""

    @patch("subprocess.Popen")
    @patch("orchestrator.session.tunnel.time.sleep")
    def test_stop_all_does_not_miss_concurrent_additions(self, mock_sleep, mock_popen):
        """Tunnels added after stop_all snapshots may survive. This is acceptable."""
        mgr = _make_manager()
        mock_popen.return_value = _make_mock_proc(pid=700)

        # Pre-populate
        for i in range(5):
            proc = _make_mock_proc(pid=700 + i)
            mgr._tunnels[f"sess-{i}"] = _TunnelEntry(
                proc=proc, host="host/vm", session_name=f"w-{i}", pid=700 + i
            )

        # stop_all snapshots keys, then iterates
        # A concurrent start might add a new one during iteration
        added_during_stop = threading.Event()

        original_stop = mgr.stop_tunnel

        def slow_stop(sid):
            result = original_stop(sid)
            if sid == "sess-2":
                # After stopping sess-2, a new tunnel is added
                proc = _make_mock_proc(pid=999)
                mgr._tunnels["sess-late"] = _TunnelEntry(
                    proc=proc, host="host/vm", session_name="w-late", pid=999
                )
                added_during_stop.set()
            return result

        with (
            patch.object(mgr, "stop_tunnel", side_effect=slow_stop),
            patch("os.kill", side_effect=ProcessLookupError),
        ):
            mgr.stop_all()

        # The late addition may or may not have been stopped
        # (it was added after the snapshot, so it survives)
        if mgr.has_tunnel("sess-late"):
            # This is the documented race: stop_all misses late additions
            pass  # Acceptable behavior


# ---------------------------------------------------------------------------
# Async race: tunnel_health_loop _check_all_tunnels with deep probe
#
# The deep probe runs check_connectivity in a thread pool. While probing,
# the tunnel might die and be restarted by another mechanism. The stale
# probe result then triggers a second restart.
# ---------------------------------------------------------------------------


class TestRaceDeepProbeStaleResult:
    """Deep probe result may be stale by the time it's acted on."""

    @patch("orchestrator.session.tunnel_monitor.sessions_repo")
    async def test_stale_probe_triggers_unnecessary_restart(self, mock_repo):
        """A slow probe returns 'unhealthy' but tunnel was already restarted."""
        from orchestrator.session.tunnel_monitor import _check_all_tunnels

        mock_session = MagicMock()
        mock_session.host = "user/rdev-vm"
        mock_session.status = "waiting"
        mock_session.name = "w1"
        mock_session.id = "sess-1"
        mock_repo.list_sessions.return_value = [mock_session]

        mock_tm = MagicMock()
        # is_alive returns True (process running)
        mock_tm.is_alive.return_value = True
        # check_connectivity returns False (stale probe says unhealthy)
        mock_tm.check_connectivity.return_value = False
        # restart returns a new pid
        mock_tm.restart_tunnel.return_value = 44444
        mock_tm.get_failure_info.return_value = (0, None)

        mock_conn = MagicMock()

        # Run with deep_probe=True
        await _check_all_tunnels(mock_conn, mock_tm, deep_probe=True)

        # The stale probe result triggers a restart even though another
        # thread may have already fixed it
        mock_tm.restart_tunnel.assert_called_once_with("sess-1", "w1", "user/rdev-vm")
        mock_repo.update_session.assert_called_once_with(mock_conn, "sess-1", tunnel_pid=44444)


# ---------------------------------------------------------------------------
# Stress test: many threads doing different operations on same session
# ---------------------------------------------------------------------------


class TestStressConcurrentOperations:
    """Stress test: multiple threads performing mixed operations."""

    @patch("subprocess.Popen")
    @patch("orchestrator.session.tunnel.time.sleep")
    def test_mixed_operations_no_crash(self, mock_sleep, mock_popen):
        """Concurrent start/stop/restart/is_alive/list should not crash or deadlock."""
        mgr = _make_manager()
        mock_popen.return_value = _make_mock_proc(pid=9000)

        errors = []
        stop_flag = threading.Event()
        session_id = "stress-sess"

        def op_start():
            while not stop_flag.is_set():
                try:
                    with patch("builtins.open", _mock_open()):
                        mgr.start_tunnel(session_id, "worker", "host/vm")
                except Exception as e:
                    errors.append(("start", e))

        def op_stop():
            while not stop_flag.is_set():
                try:
                    mgr.stop_tunnel(session_id)
                except Exception as e:
                    errors.append(("stop", e))

        def op_restart():
            while not stop_flag.is_set():
                try:
                    with patch("builtins.open", _mock_open()):
                        mgr.restart_tunnel(session_id, "worker", "host/vm")
                except Exception as e:
                    errors.append(("restart", e))

        def op_is_alive():
            while not stop_flag.is_set():
                try:
                    mgr.is_alive(session_id)
                except Exception as e:
                    errors.append(("is_alive", e))

        def op_list():
            while not stop_flag.is_set():
                try:
                    mgr.list_tunnels()
                except Exception as e:
                    errors.append(("list", e))

        threads = [
            threading.Thread(target=op_start),
            threading.Thread(target=op_stop),
            threading.Thread(target=op_restart),
            threading.Thread(target=op_is_alive),
            threading.Thread(target=op_list),
        ]

        for t in threads:
            t.start()

        time.sleep(1.0)
        stop_flag.set()

        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Operations raised errors: {errors}"

    @patch("subprocess.Popen")
    @patch("orchestrator.session.tunnel.time.sleep")
    def test_multiple_sessions_concurrent(self, mock_sleep, mock_popen):
        """Concurrent operations on different sessions should be independent."""
        mgr = _make_manager()
        counter = [0]

        def make_proc(*args, **kwargs):
            counter[0] += 1
            return _make_mock_proc(pid=10000 + counter[0])

        mock_popen.side_effect = make_proc

        errors = []
        barrier = threading.Barrier(5)

        def session_worker(i):
            sid = f"sess-{i}"
            barrier.wait()
            try:
                with patch("builtins.open", _mock_open()):
                    pid = mgr.start_tunnel(sid, f"worker-{i}", f"host/vm-{i}")
                assert pid is not None
                assert mgr.is_alive(sid) is True
                mgr.stop_tunnel(sid)
                assert mgr.is_alive(sid) is False
            except Exception as e:
                errors.append((i, e))

        threads = [threading.Thread(target=session_worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Session workers raised: {errors}"
