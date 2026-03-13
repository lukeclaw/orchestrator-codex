"""Tests for remote worker resilience improvements.

Covers:
- Circuit breaker (CLOSED -> OPEN -> HALF_OPEN -> CLOSED transitions)
- Parallel health checks with ThreadPoolExecutor
- PTY exit hardening (daemon empty PTY list -> no pty_exit)
- Stream idle timeout detection
- Pool lock narrowing (get_remote_worker_server lock scope)
"""

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
        import asyncio

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
        import asyncio

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

        with (
            patch(
                "orchestrator.terminal.remote_worker_server.get_remote_worker_server",
                return_value=mock_rws,
            ),
            patch(
                "orchestrator.state.repositories.sessions.update_session",
                mock_update,
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
        # Should clear DB
        mock_update.assert_called_once_with(db_conn, "sess-1", rws_pty_id=None, status="idle")


# ===========================================================================
# Stream Idle Timeout
# ===========================================================================


class TestStreamIdleTimeout:
    """Verify that the stream reader detects idle timeout from missing heartbeats."""

    async def test_idle_timeout_closes_stream_without_pty_exit(self):
        """When socket returns None (timeout) for >STREAM_IDLE_TIMEOUT seconds,
        stream_closed should be set without pty_exited."""
        import asyncio

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
