"""Tests for remote worker resilience improvements.

Covers:
- Circuit breaker (CLOSED -> OPEN -> HALF_OPEN -> CLOSED transitions)
- Parallel health checks with ThreadPoolExecutor
- PTY exit hardening (daemon empty PTY list -> no pty_exit)
- Stream idle timeout detection
- Pool lock narrowing (get_remote_worker_server lock scope)
- In-flight guard for health-check-all
- Host-level deduplication
- Fast-fail for dead forward tunnel
- connect_timeout parameter forwarding
"""

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ===========================================================================
# Circuit Breaker
# ===========================================================================


class TestHostCircuitBreaker:
    """Test _HostCircuitBreaker state transitions."""

    def _make_breaker(self, cooldown=30.0):
        from orchestrator.session.health import _HostCircuitBreaker

        cb = _HostCircuitBreaker()
        cb.COOLDOWN_SECONDS = cooldown
        return cb

    def test_starts_closed(self):
        cb = self._make_breaker()
        assert cb.get_state("host-a") == "closed"
        assert cb.should_skip("host-a") is False

    def test_opens_after_threshold_failures(self):
        cb = self._make_breaker()
        cb.record_failure("host-a")
        cb.record_failure("host-a")
        assert cb.get_state("host-a") == "closed"  # 2 < 3

        cb.record_failure("host-a")
        assert cb.get_state("host-a") == "open"  # 3 >= 3
        assert cb.should_skip("host-a") is True

    def test_half_open_after_cooldown(self):
        cb = self._make_breaker(cooldown=0.0)  # immediate cooldown

        for _ in range(3):
            cb.record_failure("host-a")
        assert cb.get_state("host-a") == "open"

        # Cooldown is 0s, so next should_skip transitions to half_open
        assert cb.should_skip("host-a") is False
        assert cb.get_state("host-a") == "half_open"

    def test_success_resets_to_closed(self):
        cb = self._make_breaker(cooldown=0.0)
        for _ in range(3):
            cb.record_failure("host-a")
        assert cb.get_state("host-a") == "open"

        # Transitions to half_open after cooldown
        cb.should_skip("host-a")
        cb.record_success("host-a")
        assert cb.get_state("host-a") == "closed"
        assert cb.should_skip("host-a") is False

    def test_failure_in_half_open_reopens(self):
        cb = self._make_breaker(cooldown=0.0)

        for _ in range(3):
            cb.record_failure("host-a")
        cb.should_skip("host-a")  # transitions to half_open

        cb.record_failure("host-a")
        assert cb.get_state("host-a") == "open"

    def test_independent_hosts(self):
        cb = self._make_breaker()
        for _ in range(3):
            cb.record_failure("host-a")
        assert cb.should_skip("host-a") is True
        assert cb.should_skip("host-b") is False  # unaffected

    def test_open_skips_during_cooldown(self):
        cb = self._make_breaker(cooldown=999.0)  # very long cooldown
        for _ in range(3):
            cb.record_failure("host-a")

        # Should skip because cooldown hasn't expired
        assert cb.should_skip("host-a") is True
        assert cb.get_state("host-a") == "open"


# ===========================================================================
# Parallel Health Checks
# ===========================================================================


def _make_session(
    name="w1",
    host="localhost",
    status="working",
    auto_reconnect=False,
    session_id="sess-1",
    rws_pty_id=None,
):
    return SimpleNamespace(
        id=session_id,
        name=name,
        host=host,
        status=status,
        auto_reconnect=auto_reconnect,
        rws_pty_id=rws_pty_id,
        last_status_changed_at=None,
        claude_session_id=None,
    )


class TestParallelHealthChecks:
    """Verify health checks run concurrently via ThreadPoolExecutor."""

    @patch("orchestrator.session.health._host_breaker")
    @patch("orchestrator.session.health.check_and_update_worker_health")
    def test_concurrent_execution(self, mock_check, mock_breaker):
        """Multiple remote workers should be checked in parallel."""
        from orchestrator.session.health import check_all_workers_health

        mock_breaker.should_skip.return_value = False

        call_times = []

        def slow_check(db, session, tunnel_manager=None):
            call_times.append(time.time())
            time.sleep(0.3)  # simulate slow SSH
            return {"alive": True, "status": "working", "reason": "ok"}

        mock_check.side_effect = slow_check

        sessions = [
            _make_session(name=f"w{i}", host=f"user/rdev-{i}", session_id=f"sess-{i}")
            for i in range(3)
        ]

        db = MagicMock()
        result = check_all_workers_health(db, sessions, db_path=None)

        assert result["checked"] == 3
        assert len(result["alive"]) == 3
        # All 3 should start within ~50ms of each other (parallel, not serial)
        if len(call_times) >= 2:
            max_gap = max(call_times) - min(call_times)
            assert max_gap < 0.2, f"Calls should be parallel, gap was {max_gap:.3f}s"

    @patch("orchestrator.session.health._host_breaker")
    @patch("orchestrator.session.health.check_and_update_worker_health")
    def test_circuit_breaker_skips_open_host(self, mock_check, mock_breaker):
        """Sessions on open-circuit hosts should be deferred."""
        from orchestrator.session.health import check_all_workers_health

        mock_breaker.should_skip.side_effect = lambda h: h == "user/bad-host"
        mock_check.return_value = {"alive": True, "status": "working", "reason": "ok"}

        sessions = [
            _make_session(name="w1", host="user/good-host", session_id="s1"),
            _make_session(name="w2", host="user/bad-host", session_id="s2"),
        ]

        db = MagicMock()
        result = check_all_workers_health(db, sessions, db_path=None)

        assert "w2" in result["deferred"]
        assert "w1" in result["alive"]


# ===========================================================================
# PTY Exit Hardening (daemon empty PTY list)
# ===========================================================================


class TestPtyExitHardeningStreamEOF:
    """Verify stream EOF verification only confirms dead when PTY IS in list."""

    async def test_pty_not_in_list_not_confirmed_dead(self):
        """When daemon's PTY list doesn't contain our PTY ID (daemon restarted),
        we should NOT confirm dead — health check will sort it out."""

        from orchestrator.api.ws_terminal import stream_remote_pty
        from tests.test_pty_stream import FakeWebSocket

        ws = FakeWebSocket()
        ws.accepted = True

        fake_sock = MagicMock()
        mock_rws = MagicMock()
        mock_rws.connect_pty_stream.return_value = (fake_sock, b"")
        # Daemon returns empty PTY list (daemon restarted, lost state)
        mock_rws.execute.return_value = {"ptys": []}

        mock_update = MagicMock()

        with (
            patch(
                "orchestrator.terminal.remote_worker_server.get_remote_worker_server",
                return_value=mock_rws,
            ),
            patch(
                "orchestrator.state.repositories.sessions.update_session",
                mock_update,
            ),
            patch(
                "orchestrator.api.ws_terminal._blocking_recv",
                return_value=b"",  # EOF
            ),
        ):
            # Use asyncio.wait_for to prevent hanging
            await asyncio.wait_for(
                stream_remote_pty(ws, "sess-1", "pty-abc", "user/rdev-vm"),
                timeout=5.0,
            )

        # Should NOT send pty_exit — PTY not in list means uncertain
        assert not any(m.get("type") == "pty_exit" for m in ws.sent_json), (
            f"Should not send pty_exit when PTY not in daemon list, got: {ws.sent_json}"
        )
        # Should NOT clear DB
        mock_update.assert_not_called()

    async def test_pty_in_list_dead_confirms_dead(self):
        """When daemon's PTY list has our PTY with alive=False, confirm dead."""

        from orchestrator.api.ws_terminal import stream_remote_pty
        from tests.test_pty_stream import FakeWebSocket

        ws = FakeWebSocket()
        ws.accepted = True

        fake_sock = MagicMock()
        mock_rws = MagicMock()
        mock_rws.connect_pty_stream.return_value = (fake_sock, b"")
        # Daemon says PTY is dead (in list with alive=False)
        mock_rws.execute.return_value = {"ptys": [{"pty_id": "pty-abc", "alive": False}]}

        mock_update = MagicMock()
        mock_get_session = MagicMock()
        # get_session returns session still referencing this PTY
        mock_get_session.return_value = MagicMock(rws_pty_id="pty-abc")

        with (
            patch(
                "orchestrator.terminal.remote_worker_server.get_remote_worker_server",
                return_value=mock_rws,
            ),
            patch(
                "orchestrator.state.repositories.sessions.update_session",
                mock_update,
            ),
            patch(
                "orchestrator.state.repositories.sessions.get_session",
                mock_get_session,
            ),
            patch(
                "orchestrator.session.reconnect._recovery_status",
                return_value="idle",
            ),
            patch("orchestrator.api.ws_terminal._get_conn") as mock_get_conn,
            patch(
                "orchestrator.api.ws_terminal._blocking_recv",
                return_value=b"",
            ),
        ):
            db_conn = MagicMock()
            mock_get_conn.return_value = db_conn
            ws.app.state.conn_factory = None
            ws.app.state.conn = db_conn

            await asyncio.wait_for(
                stream_remote_pty(ws, "sess-1", "pty-abc", "user/rdev-vm"),
                timeout=5.0,
            )

        # Should send pty_exit — PTY confirmed dead
        assert any(m.get("type") == "pty_exit" for m in ws.sent_json)
        # Should clear DB with recovery status
        mock_update.assert_any_call(db_conn, "sess-1", rws_pty_id=None, status="idle")


# ===========================================================================
# Stream Idle Timeout
# ===========================================================================


class TestStreamIdleTimeout:
    """Verify that the stream reader detects idle timeout from missing heartbeats."""

    async def test_idle_timeout_closes_stream_without_pty_exit(self):
        """When socket returns None (timeout) for >STREAM_IDLE_TIMEOUT seconds,
        stream_closed should be set without pty_exited."""

        from orchestrator.api.ws_terminal import stream_remote_pty
        from tests.test_pty_stream import FakeWebSocket

        ws = FakeWebSocket()
        ws.accepted = True

        fake_sock = MagicMock()
        mock_rws = MagicMock()
        mock_rws.connect_pty_stream.return_value = (fake_sock, b"initial data")

        # _blocking_recv returns None (timeout) repeatedly — simulates dead stream
        call_count = 0

        def always_timeout(sock, bufsize=65536, timeout=1.0):
            nonlocal call_count
            call_count += 1
            return None

        with (
            patch(
                "orchestrator.terminal.remote_worker_server.get_remote_worker_server",
                return_value=mock_rws,
            ),
            patch(
                "orchestrator.api.ws_terminal._blocking_recv",
                side_effect=always_timeout,
            ),
            patch("orchestrator.api.ws_terminal.STREAM_IDLE_TIMEOUT", 0.1),
        ):
            await asyncio.wait_for(
                stream_remote_pty(ws, "sess-1", "pty-abc", "user/rdev-vm"),
                timeout=5.0,
            )

        # Should have tried receiving multiple times
        assert call_count > 0

        # Should NOT have sent pty_exit (idle timeout, not process death)
        assert not any(m.get("type") == "pty_exit" for m in ws.sent_json), (
            f"Should not send pty_exit on idle timeout, got: {ws.sent_json}"
        )

        # Should have sent an error about stream timeout
        error_msgs = [m for m in ws.sent_json if m.get("type") == "error"]
        assert any("timed out" in m.get("message", "") for m in error_msgs), (
            f"Expected timeout error message, got: {ws.sent_json}"
        )


# ===========================================================================
# Pool Lock Narrowing
# ===========================================================================


class TestPoolLockNarrowing:
    """Verify get_remote_worker_server doesn't hold the lock during socket reconnect."""

    def test_socket_reconnect_called_outside_lock(self):
        """The pool lock should be released before _connect_command_socket is called."""
        from orchestrator.terminal.remote_worker_server import RemoteWorkerServer

        # Create a mock server with alive tunnel but dead socket
        server = RemoteWorkerServer.__new__(RemoteWorkerServer)
        server.host = "test-host"
        server._tunnel_proc = MagicMock()
        server._tunnel_proc.poll.return_value = None  # tunnel alive
        server._cmd_sock = None  # socket dead

        # Track that reconnect succeeds (returns before hitting lock code)
        reconnect_called = False

        def mock_reconnect():
            nonlocal reconnect_called
            reconnect_called = True
            # Simulate successful reconnect
            server._cmd_sock = MagicMock()

        server._connect_command_socket = mock_reconnect

        with (
            patch("orchestrator.terminal.remote_worker_server._server_pool", {"test-host": server}),
            patch("orchestrator.terminal.remote_worker_server._starting", {}),
        ):
            from orchestrator.terminal.remote_worker_server import get_remote_worker_server

            result = get_remote_worker_server("test-host")

        assert reconnect_called, "_connect_command_socket should have been called"
        assert result is server

    def test_failed_reconnect_removes_server_and_starts_new(self):
        """If socket reconnect fails, the stale server should be removed."""
        from orchestrator.terminal.remote_worker_server import RemoteWorkerServer

        server = RemoteWorkerServer.__new__(RemoteWorkerServer)
        server.host = "test-host"
        server._tunnel_proc = MagicMock()
        server._tunnel_proc.poll.return_value = None  # tunnel alive
        server._cmd_sock = None  # socket dead
        server.stop = MagicMock()

        def mock_reconnect():
            raise RuntimeError("reconnect failed")

        server._connect_command_socket = mock_reconnect

        pool = {"test-host": server}
        starting = {}

        with (
            patch("orchestrator.terminal.remote_worker_server._server_pool", pool),
            patch("orchestrator.terminal.remote_worker_server._starting", starting),
            patch("threading.Thread") as mock_thread_cls,
        ):
            mock_thread = MagicMock()
            mock_thread.is_alive.return_value = True
            mock_thread_cls.return_value = mock_thread

            from orchestrator.terminal.remote_worker_server import get_remote_worker_server

            with pytest.raises(RuntimeError, match="Connecting"):
                get_remote_worker_server("test-host")

        # Server should have been removed from pool after failed reconnect
        assert "test-host" not in pool
        server.stop.assert_called()
        # A new background start should have been kicked off
        mock_thread.start.assert_called_once()


# ===========================================================================
# In-Flight Guard
# ===========================================================================


class TestInFlightGuard:
    """Verify _health_check_all_lock prevents concurrent health-check-all."""

    async def test_rejects_concurrent(self):
        """Acquiring the lock first causes async wrapper to return in_progress."""
        from orchestrator.session.health import (
            _health_check_all_lock,
            check_all_workers_health_async,
        )

        _health_check_all_lock.acquire()
        try:
            result = await check_all_workers_health_async(sessions=[], db_path="/fake/db.sqlite")
            assert result["status"] == "in_progress"
            assert "already running" in result["message"]
        finally:
            _health_check_all_lock.release()

    async def test_lock_released_on_error(self):
        """Lock is released even when check_all_workers_health raises."""
        from orchestrator.session.health import (
            _health_check_all_lock,
            check_all_workers_health_async,
        )

        with (
            patch(
                "orchestrator.session.health.check_all_workers_health",
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "orchestrator.state.db.get_connection",
                return_value=MagicMock(),
            ),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                await check_all_workers_health_async(sessions=[], db_path="/fake/db.sqlite")

        # Lock should be released — we can acquire it again
        assert _health_check_all_lock.acquire(blocking=False)
        _health_check_all_lock.release()


# ===========================================================================
# Host-Level Deduplication
# ===========================================================================


class TestHostDeduplication:
    """Verify host-level dedup in check_all_workers_health."""

    @patch("orchestrator.session.health.repo.update_session")
    @patch("orchestrator.session.health._host_breaker")
    @patch("orchestrator.session.health.check_and_update_worker_health")
    def test_single_probe_per_host_on_failure(self, mock_check, mock_breaker, mock_update):
        """3 sessions on same host, all fail. Only 1 probe call, all 3 disconnected."""
        from orchestrator.session.health import check_all_workers_health

        mock_breaker.should_skip.return_value = False

        mock_check.return_value = {
            "alive": False,
            "status": "disconnected",
            "reason": "RWS unavailable",
        }

        sessions = [
            _make_session(name=f"w{i}", host="user/shared-rdev", session_id=f"sess-{i}")
            for i in range(3)
        ]

        db = MagicMock()
        result = check_all_workers_health(db, sessions, db_path=None)

        # Only 1 probe call (not 3)
        assert mock_check.call_count == 1
        # All 3 should be in disconnected list
        assert len(result["disconnected"]) == 3

    @patch("orchestrator.session.health._host_breaker")
    @patch("orchestrator.session.health.check_and_update_worker_health")
    def test_success_checks_peers_individually(self, mock_check, mock_breaker):
        """3 sessions on same host, probe succeeds -> all 3 get individual checks."""
        from orchestrator.session.health import check_all_workers_health

        mock_breaker.should_skip.return_value = False

        mock_check.return_value = {
            "alive": True,
            "status": "working",
            "reason": "ok",
        }

        sessions = [
            _make_session(name=f"w{i}", host="user/shared-rdev", session_id=f"sess-{i}")
            for i in range(3)
        ]

        db = MagicMock()
        result = check_all_workers_health(db, sessions, db_path=None)

        # All 3 should be checked individually (probe + 2 peers)
        assert mock_check.call_count == 3
        assert len(result["alive"]) == 3


# ===========================================================================
# Fast-Fail Dead Tunnel
# ===========================================================================


class TestFastFailDeadTunnel:
    """Verify dead forward tunnel skips execute() and falls to SSH fallback."""

    @patch("orchestrator.session.health.repo.update_session")
    @patch("orchestrator.session.health.subprocess.run")
    def test_dead_tunnel_skips_execute(self, mock_subprocess, mock_update):
        """When tunnel proc is dead, execute() should never be called."""
        import subprocess as _subprocess

        from orchestrator.session.health import _check_rws_pty_health

        mock_rws = MagicMock()
        mock_rws._tunnel_proc = MagicMock()
        mock_rws._tunnel_proc.poll.return_value = 1  # dead
        mock_rws._is_tunnel_port_open.return_value = False  # port also closed

        session = _make_session(
            name="w1", host="user/rdev-1", session_id="sess-1", rws_pty_id="pty-1"
        )
        db = MagicMock()

        # SSH fallback will fail too
        mock_subprocess.side_effect = _subprocess.TimeoutExpired(cmd="ssh", timeout=5)

        with patch(
            "orchestrator.terminal.remote_worker_server._server_pool",
            {"user/rdev-1": mock_rws},
        ):
            result = _check_rws_pty_health(db, session, tunnel_manager=MagicMock())

        # execute() should NOT have been called (fast-fail skipped it)
        mock_rws.execute.assert_not_called()
        assert result["alive"] is False


# ===========================================================================
# connect_timeout Forwarding
# ===========================================================================


class TestConnectTimeoutForwarding:
    """Verify connect_timeout is forwarded to _connect_command_socket."""

    def test_execute_forwards_connect_timeout(self):
        """execute(connect_timeout=3) passes 3 to _connect_command_socket."""
        from orchestrator.terminal.remote_worker_server import RemoteWorkerServer

        server = RemoteWorkerServer.__new__(RemoteWorkerServer)
        server.host = "test-host"
        server._tunnel_proc = MagicMock()
        server._tunnel_proc.poll.return_value = None  # alive
        server._cmd_sock = None
        server._cmd_buffer = bytearray()
        server._local_port = 12345
        server._lock = threading.Lock()

        connect_timeouts = []

        def mock_connect(timeout=10.0):
            connect_timeouts.append(timeout)
            # Simulate successful connect
            server._cmd_sock = MagicMock()
            server._cmd_sock.recv.return_value = b'{"ok": true}\n'
            server._cmd_sock.settimeout = MagicMock()
            server._cmd_buffer = bytearray(b'{"ok": true}\n')

        server._connect_command_socket = mock_connect

        result = server.execute({"action": "test"}, timeout=2, connect_timeout=3)

        assert connect_timeouts[0] == 3
        assert result == {"ok": True}


# ===========================================================================
# Reduced Timeouts in Bulk Check
# ===========================================================================


class TestReducedTimeouts:
    """Verify SSH fallback uses reduced timeouts."""

    @patch("orchestrator.session.health.subprocess.run")
    def test_ssh_fallback_reduced_timeouts(self, mock_subprocess):
        """SSH fallback should use ConnectTimeout=3 and timeout=5."""
        from orchestrator.session.health import _check_rws_pty_health

        session = _make_session(
            name="w1", host="user/rdev-1", session_id="sess-1", rws_pty_id="pty-1"
        )
        db = MagicMock()

        mock_subprocess.return_value = MagicMock(stdout="ALIVE", returncode=0)

        with patch(
            "orchestrator.terminal.remote_worker_server._server_pool",
            {},  # no RWS available -> goes to SSH fallback
        ):
            _check_rws_pty_health(db, session, tunnel_manager=MagicMock())

        # Verify the SSH call used reduced timeouts
        call_args = mock_subprocess.call_args
        cmd_list = call_args[0][0]
        assert "ConnectTimeout=3" in cmd_list
        assert call_args[1]["timeout"] == 5


# ===========================================================================
# Auto-Reconnect from DB
# ===========================================================================


class TestAutoReconnectFromDb:
    """Verify auto-reconnect queries the DB instead of using in-memory candidates."""

    @patch("orchestrator.session.health._host_breaker")
    @patch("orchestrator.session.health.check_and_update_worker_health")
    def test_exception_defaults_to_not_alive(self, mock_check, mock_breaker):
        """When check_and_update_worker_health raises, result should report alive=False."""
        from orchestrator.session.health import check_all_workers_health

        mock_breaker.should_skip.return_value = False
        mock_check.side_effect = RuntimeError("SSH exploded")

        session = _make_session(
            name="w1", host="user/rdev-1", session_id="sess-1", auto_reconnect=False
        )
        db = MagicMock()
        db.execute = MagicMock()
        # list_sessions returns empty — no reconnect candidates
        with patch("orchestrator.session.health.repo") as mock_repo:
            mock_repo.list_sessions.return_value = []
            result = check_all_workers_health(db, [session], db_path=None)

        # The session should be in disconnected, not alive
        assert "w1" not in result["alive"]
        assert "w1" in result["disconnected"]

    @patch("orchestrator.session.health._host_breaker")
    @patch("orchestrator.session.health.check_and_update_worker_health")
    def test_auto_reconnect_queries_db_after_checks(self, mock_check, mock_breaker):
        """After health checks, auto-reconnect should query DB for disconnected workers."""
        from orchestrator.session.health import check_all_workers_health

        mock_breaker.should_skip.return_value = False
        mock_check.return_value = {
            "alive": False,
            "status": "disconnected",
            "reason": "dead",
        }

        session = _make_session(
            name="w1",
            host="user/rdev-1",
            session_id="sess-1",
            auto_reconnect=True,
        )
        # DB returns disconnected session with auto_reconnect=True
        db_session = _make_session(
            name="w1",
            host="user/rdev-1",
            session_id="sess-1",
            auto_reconnect=True,
            status="disconnected",
        )

        db = MagicMock()
        with (
            patch("orchestrator.session.health.repo") as mock_repo,
            patch("orchestrator.session.reconnect.trigger_reconnect") as mock_trigger,
        ):
            mock_repo.list_sessions.return_value = [db_session]
            mock_repo.get_session.return_value = db_session
            mock_trigger.return_value = {"ok": True}

            result = check_all_workers_health(db, [session], db_path=None)

        mock_trigger.assert_called_once()
        assert "w1" in result["auto_reconnected"]
        # list_sessions should have been called for both "disconnected" and "error"
        assert mock_repo.list_sessions.call_count == 2

    @patch("orchestrator.session.health._host_breaker")
    @patch("orchestrator.session.health.check_and_update_worker_health")
    def test_circuit_breaker_skipped_not_auto_reconnected(self, mock_check, mock_breaker):
        """Sessions skipped by circuit breaker should not be auto-reconnected."""
        from orchestrator.session.health import check_all_workers_health

        # Circuit breaker skips this host
        mock_breaker.should_skip.return_value = True
        mock_check.return_value = {"alive": False, "status": "disconnected"}

        session = _make_session(
            name="w1",
            host="user/rdev-1",
            session_id="sess-1",
            auto_reconnect=True,
            status="disconnected",
        )

        # DB returns the session as disconnected (it IS disconnected in DB)
        db_session = _make_session(
            name="w1",
            host="user/rdev-1",
            session_id="sess-1",
            auto_reconnect=True,
            status="disconnected",
        )

        db = MagicMock()
        with (
            patch("orchestrator.session.health.repo") as mock_repo,
            patch("orchestrator.session.reconnect.trigger_reconnect") as mock_trigger,
        ):
            mock_repo.list_sessions.return_value = [db_session]
            mock_repo.get_session.return_value = db_session

            result = check_all_workers_health(db, [session], db_path=None)

        # Should be deferred (circuit breaker), NOT auto-reconnected
        assert "w1" in result["deferred"]
        mock_trigger.assert_not_called()

    @patch("orchestrator.session.health._host_breaker")
    @patch("orchestrator.session.health.check_and_update_worker_health")
    def test_as_completed_timeout_still_runs_auto_reconnect(self, mock_check, mock_breaker):
        """Even if as_completed times out, auto-reconnect loop should still run."""

        from orchestrator.session.health import check_all_workers_health

        mock_breaker.should_skip.return_value = False
        mock_check.return_value = {
            "alive": False,
            "status": "disconnected",
            "reason": "dead",
        }

        session = _make_session(
            name="w1",
            host="user/rdev-1",
            session_id="sess-1",
            auto_reconnect=True,
        )

        db_session = _make_session(
            name="w1",
            host="user/rdev-1",
            session_id="sess-1",
            auto_reconnect=True,
            status="disconnected",
        )

        # Mock as_completed to raise TimeoutError immediately
        def fake_as_completed(futures, timeout=None):
            raise TimeoutError("simulated timeout")

        db = MagicMock()
        with (
            patch("orchestrator.session.health.repo") as mock_repo,
            patch("orchestrator.session.reconnect.trigger_reconnect") as mock_trigger,
            patch("orchestrator.session.health.as_completed", fake_as_completed),
        ):
            mock_repo.list_sessions.return_value = [db_session]
            mock_repo.get_session.return_value = db_session
            mock_trigger.return_value = {"ok": True}

            result = check_all_workers_health(db, [session], db_path=None)

        # Auto-reconnect should still have fired using DB query
        mock_trigger.assert_called_once()
        assert "w1" in result["auto_reconnected"]
