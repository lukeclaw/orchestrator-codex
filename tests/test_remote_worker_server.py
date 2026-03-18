"""Tests for the Remote Worker Server (RWS) module.

Tests the client class, pool management, and integration with interactive CLI.
All tests mock SSH/socket operations — no live remote hosts needed.
"""

from __future__ import annotations

import json
import socket
import threading
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.terminal.remote_worker_server import (
    RWS_REMOTE_PORT,
    RemoteWorkerServer,
    _last_start_fail,
    _reconnecting,
    _server_pool,
    _starting,
    ensure_rws_starting,
    get_remote_worker_server,
    shutdown_all_rws_servers,
)

pytestmark = pytest.mark.allow_threading

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_pool():
    """Reset the global server pool between tests."""
    _server_pool.clear()
    _starting.clear()
    _reconnecting.clear()
    _last_start_fail.clear()
    yield
    # Clean up any remaining servers
    for host, server in list(_server_pool.items()):
        try:
            server.stop()
        except Exception:
            pass
    _server_pool.clear()
    _starting.clear()
    _reconnecting.clear()
    _last_start_fail.clear()


# ---------------------------------------------------------------------------
# RemoteWorkerServer client class
# ---------------------------------------------------------------------------


class TestRemoteWorkerServerClient:
    """Test the RemoteWorkerServer client class."""

    def test_init(self):
        rws = RemoteWorkerServer("test-host")
        assert rws.host == "test-host"
        assert rws._local_port is None
        assert rws._remote_pid is None
        assert rws._tunnel_proc is None
        assert rws._cmd_sock is None

    @patch("orchestrator.terminal._rws_client.subprocess.Popen")
    def test_deploy_daemon_success(self, mock_popen):
        """Test successful daemon deployment via SSH."""
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_proc.stdout.read.return_value = json.dumps(
            {"status": "ok", "pid": 12345, "port": RWS_REMOTE_PORT, "reused": False}
        ).encode()
        mock_proc.stderr.read.return_value = b""
        mock_popen.return_value = mock_proc

        rws = RemoteWorkerServer("test-host")
        rws._deploy_daemon(timeout=10.0)

        assert rws._remote_pid == 12345
        # Verify SSH was called
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        assert args[0] == "ssh"
        assert "test-host" in args

    @patch("orchestrator.terminal._rws_client.subprocess.Popen")
    def test_deploy_daemon_reuse(self, mock_popen):
        """Test daemon deployment when an existing daemon is found."""
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_proc.stdout.read.return_value = json.dumps(
            {"status": "ok", "pid": 99999, "port": RWS_REMOTE_PORT, "reused": True}
        ).encode()
        mock_proc.stderr.read.return_value = b""
        mock_popen.return_value = mock_proc

        rws = RemoteWorkerServer("test-host")
        rws._deploy_daemon(timeout=10.0)

        assert rws._remote_pid == 99999

    @patch("orchestrator.terminal._rws_client.subprocess.Popen")
    def test_deploy_daemon_timeout(self, mock_popen):
        """Test daemon deployment timeout."""
        import subprocess

        mock_proc = MagicMock()
        mock_proc.wait.side_effect = subprocess.TimeoutExpired("ssh", 10)
        mock_proc.kill.return_value = None
        mock_popen.return_value = mock_proc

        rws = RemoteWorkerServer("test-host")
        with pytest.raises(RuntimeError, match="timed out"):
            rws._deploy_daemon(timeout=10.0)

    @patch("orchestrator.terminal._rws_client.subprocess.Popen")
    def test_deploy_daemon_no_output(self, mock_popen):
        """Test daemon deployment with no stdout output."""
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 1
        mock_proc.stdout.read.return_value = b""
        mock_proc.stderr.read.return_value = b"Connection refused"
        mock_popen.return_value = mock_proc

        rws = RemoteWorkerServer("test-host")
        with pytest.raises(RuntimeError, match="No output"):
            rws._deploy_daemon(timeout=10.0)

    def test_execute_sends_json_and_reads_response(self):
        """Test that execute() sends JSON and reads a JSON response."""
        rws = RemoteWorkerServer("test-host")

        # Create a mock socket
        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.recv.return_value = json.dumps({"status": "pong"}).encode() + b"\n"
        rws._cmd_sock = mock_sock
        rws._cmd_buffer = bytearray()

        result = rws.execute({"action": "ping"})
        assert result == {"status": "pong"}

        # Verify the command was sent as JSON with newline
        sent_data = mock_sock.sendall.call_args[0][0]
        assert b"\n" in sent_data
        parsed = json.loads(sent_data.decode().strip())
        assert parsed == {"action": "ping"}

    def test_execute_not_connected(self):
        """Test that execute() raises when not connected."""
        rws = RemoteWorkerServer("test-host")
        rws._cmd_sock = None

        with pytest.raises(RuntimeError, match="Remote host not connected"):
            rws.execute({"action": "ping"})

    def test_execute_timeout(self):
        """Test that execute() raises on timeout and clears socket."""
        rws = RemoteWorkerServer("test-host")

        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.recv.side_effect = TimeoutError("timed out")
        rws._cmd_sock = mock_sock
        rws._cmd_buffer = bytearray()

        with pytest.raises(RuntimeError, match="timed out"):
            rws.execute({"action": "ping"})

        # Socket should be cleared so next call can reconnect
        assert rws._cmd_sock is None

    def test_execute_connection_broken(self):
        """Test that execute() handles broken connection and clears socket."""
        rws = RemoteWorkerServer("test-host")
        # No tunnel — so the retry will raise "not connected" immediately
        rws._tunnel_proc = None

        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.recv.side_effect = ConnectionError("Connection reset")
        rws._cmd_sock = mock_sock
        rws._cmd_buffer = bytearray()

        with pytest.raises(RuntimeError, match="Remote host not connected"):
            rws.execute({"action": "ping"})

        # Socket should be cleared
        assert rws._cmd_sock is None

    def test_create_pty(self):
        """Test creating a PTY session."""
        rws = RemoteWorkerServer("test-host")

        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.recv.return_value = (
            json.dumps({"status": "ok", "pty_id": "abc123"}).encode() + b"\n"
        )
        rws._cmd_sock = mock_sock
        rws._cmd_buffer = bytearray()

        pty_id = rws.create_pty(cmd="/bin/bash", cwd="/home/user", cols=120, rows=40)
        assert pty_id == "abc123"

        # Verify the command
        sent_data = mock_sock.sendall.call_args[0][0]
        parsed = json.loads(sent_data.decode().strip())
        assert parsed["action"] == "pty_create"
        assert parsed["cmd"] == "/bin/bash"
        assert parsed["cwd"] == "/home/user"
        assert parsed["cols"] == 120
        assert parsed["rows"] == 40

    def test_create_pty_error(self):
        """Test PTY creation failure."""
        rws = RemoteWorkerServer("test-host")

        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.recv.return_value = json.dumps({"error": "Fork failed"}).encode() + b"\n"
        rws._cmd_sock = mock_sock
        rws._cmd_buffer = bytearray()

        with pytest.raises(RuntimeError, match="Fork failed"):
            rws.create_pty()

    def test_destroy_pty(self):
        """Test destroying a PTY session."""
        rws = RemoteWorkerServer("test-host")

        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.recv.return_value = json.dumps({"status": "ok"}).encode() + b"\n"
        rws._cmd_sock = mock_sock
        rws._cmd_buffer = bytearray()

        rws.destroy_pty("abc123")

        sent_data = mock_sock.sendall.call_args[0][0]
        parsed = json.loads(sent_data.decode().strip())
        assert parsed["action"] == "pty_destroy"
        assert parsed["pty_id"] == "abc123"

    def test_list_ptys(self):
        """Test listing PTY sessions."""
        rws = RemoteWorkerServer("test-host")

        ptys = [
            {"pty_id": "abc", "cmd": "/bin/bash", "alive": True},
            {"pty_id": "def", "cmd": "/bin/zsh", "alive": False},
        ]
        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.recv.return_value = json.dumps({"status": "ok", "ptys": ptys}).encode() + b"\n"
        rws._cmd_sock = mock_sock
        rws._cmd_buffer = bytearray()

        result = rws.list_ptys()
        assert len(result) == 2
        assert result[0]["pty_id"] == "abc"
        assert result[1]["alive"] is False

    def test_is_alive_tunnel_dead(self):
        """Test is_alive when tunnel process has exited and port is closed."""
        rws = RemoteWorkerServer("test-host")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # Process exited
        rws._tunnel_proc = mock_proc
        rws._cmd_sock = MagicMock()

        with patch.object(rws, "_is_tunnel_port_open", return_value=False):
            assert rws.is_alive() is False

    def test_is_alive_no_socket(self):
        """Test is_alive when command socket is None."""
        rws = RemoteWorkerServer("test-host")
        rws._tunnel_proc = MagicMock()
        rws._tunnel_proc.poll.return_value = None
        rws._cmd_sock = None

        assert rws.is_alive() is False

    def test_stop(self):
        """Test stopping the client cleans up resources."""
        rws = RemoteWorkerServer("test-host")

        mock_sock = MagicMock(spec=socket.socket)
        mock_proc = MagicMock()
        rws._cmd_sock = mock_sock
        rws._tunnel_proc = mock_proc
        rws._local_port = 12345

        rws.stop()

        mock_sock.close.assert_called_once()
        mock_proc.kill.assert_called_once()
        assert rws._cmd_sock is None
        assert rws._tunnel_proc is None
        assert rws._local_port is None


# ---------------------------------------------------------------------------
# Server pool management
# ---------------------------------------------------------------------------


class TestServerPool:
    """Test the global server pool functions."""

    def test_get_remote_worker_server_not_started(self):
        """Test that get_remote_worker_server kicks off background start."""
        with patch.object(RemoteWorkerServer, "start"):
            with pytest.raises(RuntimeError, match="Connecting to remote host"):
                get_remote_worker_server("test-host")

        # Wait a moment for the background thread to start
        import time

        time.sleep(0.1)

    def test_get_remote_worker_server_already_starting(self):
        """Test that double-start is prevented."""

        # Simulate a long-running start
        def slow_start():
            import time

            time.sleep(10)

        t = threading.Thread(target=slow_start, daemon=True)
        t.start()
        _starting["test-host"] = t

        with pytest.raises(RuntimeError, match="Connecting to remote host"):
            get_remote_worker_server("test-host")

    def test_get_remote_worker_server_ready(self):
        """Test that a ready server is returned."""
        rws = RemoteWorkerServer("test-host")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Tunnel alive
        rws._tunnel_proc = mock_proc
        rws._cmd_sock = MagicMock(spec=socket.socket)  # Socket alive
        _server_pool["test-host"] = rws

        result = get_remote_worker_server("test-host")
        assert result is rws

    def test_get_remote_worker_server_stale(self):
        """Test that a stale server (dead tunnel + closed port) is removed."""
        rws = RemoteWorkerServer("test-host")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # Tunnel dead
        rws._tunnel_proc = mock_proc
        _server_pool["test-host"] = rws

        with patch.object(rws, "_is_tunnel_port_open", return_value=False):
            with patch.object(RemoteWorkerServer, "start"):
                with pytest.raises(RuntimeError, match="Connecting to remote host"):
                    get_remote_worker_server("test-host")

        assert "test-host" not in _server_pool

    def test_ensure_rws_starting_no_error(self):
        """Test that ensure_rws_starting never raises."""
        with patch.object(RemoteWorkerServer, "start"):
            # Should not raise even though server isn't ready
            ensure_rws_starting("test-host")

    def test_shutdown_all_servers(self):
        """Test that shutdown_all_rws_servers cleans up everything."""
        rws1 = RemoteWorkerServer("host1")
        rws1.stop = MagicMock()
        rws2 = RemoteWorkerServer("host2")
        rws2.stop = MagicMock()

        _server_pool["host1"] = rws1
        _server_pool["host2"] = rws2

        shutdown_all_rws_servers()

        rws1.stop.assert_called_once()
        rws2.stop.assert_called_once()
        assert len(_server_pool) == 0


# ---------------------------------------------------------------------------
# Socket reconnection
# ---------------------------------------------------------------------------


class TestSocketReconnection:
    """Test auto-reconnection when the command socket breaks."""

    def test_get_rws_reconnects_dead_socket(self):
        """Pool returns server after reconnecting a dead socket."""
        rws = RemoteWorkerServer("test-host")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Tunnel alive
        rws._tunnel_proc = mock_proc
        rws._cmd_sock = None  # Socket dead
        rws._local_port = 12345
        _server_pool["test-host"] = rws

        with patch.object(rws, "_connect_command_socket") as mock_connect:
            result = get_remote_worker_server("test-host")

        mock_connect.assert_called_once()
        assert result is rws

    def test_get_rws_restarts_when_reconnect_fails(self):
        """Pool removes server and kicks off background start when reconnect fails."""
        rws = RemoteWorkerServer("test-host")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Tunnel alive
        rws._tunnel_proc = mock_proc
        rws._cmd_sock = None  # Socket dead
        rws._local_port = 12345
        _server_pool["test-host"] = rws

        with patch.object(
            rws, "_connect_command_socket", side_effect=RuntimeError("connect failed")
        ):
            with patch.object(RemoteWorkerServer, "start"):
                with pytest.raises(RuntimeError, match="Connecting to remote host"):
                    get_remote_worker_server("test-host")

        assert "test-host" not in _server_pool

    def test_execute_reconnects_dead_socket(self):
        """execute() reconnects and succeeds when socket is dead but tunnel alive."""
        rws = RemoteWorkerServer("test-host")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Tunnel alive
        rws._tunnel_proc = mock_proc
        rws._cmd_sock = None  # Socket dead
        rws._local_port = 12345

        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.recv.return_value = json.dumps({"status": "pong"}).encode() + b"\n"

        def fake_connect(timeout=10.0):
            rws._cmd_sock = mock_sock
            rws._cmd_buffer = bytearray()

        with patch.object(rws, "_connect_command_socket", side_effect=fake_connect):
            result = rws.execute({"action": "ping"})

        assert result == {"status": "pong"}

    def test_execute_raises_when_reconnect_fails(self):
        """execute() raises when socket is dead and reconnect fails."""
        rws = RemoteWorkerServer("test-host")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Tunnel alive
        rws._tunnel_proc = mock_proc
        rws._cmd_sock = None

        with patch.object(
            rws, "_connect_command_socket", side_effect=RuntimeError("connect failed")
        ):
            with pytest.raises(RuntimeError, match="Remote host not connected"):
                rws.execute({"action": "ping"})

    def test_execute_raises_when_no_tunnel(self):
        """execute() raises immediately when tunnel is dead and port closed."""
        rws = RemoteWorkerServer("test-host")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # Tunnel dead
        rws._tunnel_proc = mock_proc
        rws._cmd_sock = None

        with patch.object(rws, "_is_tunnel_port_open", return_value=False):
            with pytest.raises(RuntimeError, match="Remote host not connected"):
                rws.execute({"action": "ping"})

    def test_execute_raises_when_no_tunnel_proc(self):
        """execute() raises when tunnel_proc is None."""
        rws = RemoteWorkerServer("test-host")
        rws._tunnel_proc = None
        rws._cmd_sock = None

        with pytest.raises(RuntimeError, match="Remote host not connected"):
            rws.execute({"action": "ping"})

    def test_execute_retries_on_closed_connection(self):
        """execute() retries once: first attempt gets EOF, retry reconnects and succeeds."""
        rws = RemoteWorkerServer("test-host")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Tunnel alive
        rws._tunnel_proc = mock_proc

        dead_sock = MagicMock(spec=socket.socket)
        dead_sock.recv.return_value = b""  # EOF — remote closed
        rws._cmd_sock = dead_sock
        rws._cmd_buffer = bytearray()

        new_sock = MagicMock(spec=socket.socket)
        new_sock.recv.return_value = json.dumps({"status": "pong"}).encode() + b"\n"

        def fake_connect(timeout=10.0):
            rws._cmd_sock = new_sock
            rws._cmd_buffer = bytearray()

        with patch.object(rws, "_connect_command_socket", side_effect=fake_connect):
            result = rws.execute({"action": "ping"})

        # Should succeed on the retry without the caller ever seeing an error
        assert result == {"status": "pong"}

    def test_execute_retries_on_broken_pipe(self):
        """execute() retries once on ConnectionError (e.g. BrokenPipeError)."""
        rws = RemoteWorkerServer("test-host")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Tunnel alive
        rws._tunnel_proc = mock_proc

        broken_sock = MagicMock(spec=socket.socket)
        broken_sock.sendall.side_effect = BrokenPipeError("Broken pipe")
        rws._cmd_sock = broken_sock
        rws._cmd_buffer = bytearray()

        new_sock = MagicMock(spec=socket.socket)
        new_sock.recv.return_value = json.dumps({"status": "pong"}).encode() + b"\n"

        def fake_connect(timeout=10.0):
            rws._cmd_sock = new_sock
            rws._cmd_buffer = bytearray()

        with patch.object(rws, "_connect_command_socket", side_effect=fake_connect):
            result = rws.execute({"action": "ping"})

        assert result == {"status": "pong"}

    def test_execute_gives_up_after_two_failures(self):
        """execute() raises after both attempts fail (no tunnel for retry)."""
        rws = RemoteWorkerServer("test-host")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Tunnel alive
        rws._tunnel_proc = mock_proc

        # _connect_command_socket creates a socket that also returns EOF
        def make_dead_sock(timeout=10.0):
            dead = MagicMock(spec=socket.socket)
            dead.recv.return_value = b""
            rws._cmd_sock = dead
            rws._cmd_buffer = bytearray()

        rws._cmd_sock = MagicMock(spec=socket.socket)
        rws._cmd_sock.recv.return_value = b""  # First attempt EOF
        rws._cmd_buffer = bytearray()

        with patch.object(rws, "_connect_command_socket", side_effect=make_dead_sock):
            with pytest.raises(RuntimeError, match="Remote connection closed"):
                rws.execute({"action": "ping"})

    def test_execute_timeout_clears_socket(self):
        """Timeout clears socket so the next call can reconnect."""
        rws = RemoteWorkerServer("test-host")

        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.recv.side_effect = TimeoutError("timed out")
        rws._cmd_sock = mock_sock
        rws._cmd_buffer = bytearray()

        with pytest.raises(RuntimeError, match="timed out"):
            rws.execute({"action": "ping"})

        assert rws._cmd_sock is None
        assert rws._cmd_buffer == bytearray()


class TestDaemonKillRestart:
    """Test 'final resort' daemon kill+restart logic."""

    def test_kill_remote_daemon_runs_ssh(self):
        """kill_remote_daemon() SSHes to kill the remote PID."""
        rws = RemoteWorkerServer("test-host")
        with patch("orchestrator.terminal._rws_client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="killed 12345", returncode=0)
            rws.kill_remote_daemon()
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert "ssh" in args[0][0][0]
        assert "test-host" in args[0][0]
        assert "kill" in " ".join(args[0][0])

    def test_kill_remote_daemon_handles_failure(self):
        """kill_remote_daemon() does not raise on SSH failure."""
        rws = RemoteWorkerServer("test-host")
        with patch(
            "orchestrator.terminal._rws_client.subprocess.run",
            side_effect=OSError("ssh not found"),
        ):
            # Should not raise
            rws.kill_remote_daemon()

    def test_background_start_retries_without_killing_daemon(self, _reset_pool):
        """_start_in_background retries without killing daemon when first start fails."""
        start_calls = []

        def mock_start(self_rws, timeout=30.0):
            start_calls.append(len(start_calls))
            if len(start_calls) == 1:
                raise RuntimeError("tunnel failed")
            # Second call succeeds (daemon is reused)

        with (
            patch.object(RemoteWorkerServer, "start", mock_start),
            patch.object(RemoteWorkerServer, "kill_remote_daemon") as mock_kill,
        ):
            with pytest.raises(RuntimeError, match="Connecting to remote host"):
                get_remote_worker_server("retry-host")

            # Wait for background thread to finish
            import time

            for _ in range(50):
                if "retry-host" in _server_pool:
                    break
                time.sleep(0.1)

        assert "retry-host" in _server_pool
        assert len(start_calls) == 2
        # Daemon should NOT be killed — it may have active PTYs
        mock_kill.assert_not_called()

    def test_background_start_gives_up_without_killing_daemon(self, _reset_pool):
        """_start_in_background gives up when retry also fails, without killing daemon."""
        with (
            patch.object(RemoteWorkerServer, "start", side_effect=RuntimeError("broken")),
            patch.object(RemoteWorkerServer, "kill_remote_daemon") as mock_kill,
        ):
            with pytest.raises(RuntimeError, match="Connecting to remote host"):
                get_remote_worker_server("fail-host")

            import time

            for _ in range(50):
                if "fail-host" not in _starting:
                    break
                time.sleep(0.1)

        # Server never made it to the pool
        assert "fail-host" not in _server_pool
        # Daemon should NOT be killed
        mock_kill.assert_not_called()

    def test_background_start_cleans_up_tunnel_on_failure(self, _reset_pool):
        """Failed start() must call stop() to kill the orphaned SSH tunnel process."""
        stop_calls = []

        def mock_start(self_rws, timeout=30.0):
            # Simulate: tunnel started, then socket connect fails
            self_rws._tunnel_proc = MagicMock()
            raise RuntimeError("socket connect failed")

        def mock_stop(self_rws):
            stop_calls.append(self_rws.host)
            # Don't actually call stop — just track the call

        with (
            patch.object(RemoteWorkerServer, "start", mock_start),
            patch.object(RemoteWorkerServer, "stop", mock_stop),
        ):
            with pytest.raises(RuntimeError, match="Connecting to remote host"):
                get_remote_worker_server("leak-host")

            import time

            for _ in range(50):
                if "leak-host" not in _starting:
                    break
                time.sleep(0.1)

        # stop() should have been called for both the first attempt and the retry
        assert stop_calls.count("leak-host") == 2, (
            f"Expected 2 stop() calls (first + retry), got {stop_calls}"
        )

    def test_backoff_after_failure(self, _reset_pool):
        """After both attempts fail, further starts are suppressed for _BACKOFF_SECS."""
        with patch.object(RemoteWorkerServer, "start", side_effect=RuntimeError("broken")):
            with pytest.raises(RuntimeError, match="Connecting to remote host"):
                get_remote_worker_server("backoff-host")

            import time

            for _ in range(50):
                if "backoff-host" not in _starting:
                    break
                time.sleep(0.1)

        # Now the backoff timer should be set — next call should raise immediately
        # without starting a background thread
        with pytest.raises(RuntimeError, match="retry in"):
            get_remote_worker_server("backoff-host")

        # No new background thread should have been started
        assert "backoff-host" not in _starting

    def test_backoff_expires(self, _reset_pool):
        """After _BACKOFF_SECS, the backoff expires and a new start is allowed."""
        import time as time_mod

        # Set a backoff that's already expired
        _last_start_fail["expired-host"] = time_mod.monotonic() - 60

        with (
            patch.object(RemoteWorkerServer, "start", side_effect=RuntimeError("still broken")),
        ):
            with pytest.raises(RuntimeError, match="Connecting to remote host"):
                get_remote_worker_server("expired-host")

        # A background thread should have been started (backoff expired)
        assert "expired-host" in _starting

    def test_phase2_concurrency_guard(self, _reset_pool):
        """Only one thread should enter Phase 2 reconnect per host."""
        from orchestrator.terminal._rws_pool import _reconnecting

        server = RemoteWorkerServer.__new__(RemoteWorkerServer)
        server.host = "guard-host"
        server._tunnel_proc = MagicMock()
        server._tunnel_proc.poll.return_value = None  # tunnel alive
        server._cmd_sock = None  # socket dead → needs reconnect

        # Pre-mark host as reconnecting
        _reconnecting.add("guard-host")
        _server_pool["guard-host"] = server

        # Should skip Phase 2 and go to Phase 3 (background start)
        with patch("threading.Thread") as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread.is_alive.return_value = True
            mock_thread_cls.return_value = mock_thread

            with pytest.raises(RuntimeError, match="Connecting"):
                get_remote_worker_server("guard-host")

        # Reconnect should NOT have been attempted (guard blocked it)
        # Instead it should have gone to Phase 3 (background start)
        mock_thread.start.assert_called_once()


# ---------------------------------------------------------------------------
# InteractiveCLI integration
# ---------------------------------------------------------------------------


class TestInteractiveCLIIntegration:
    """Test interactive CLI open/close/send/capture via RWS."""

    def test_open_via_rws(self):
        """Test opening interactive CLI via RWS creates PTY."""
        mock_rws = MagicMock(spec=RemoteWorkerServer)
        mock_rws.create_pty.return_value = "pty123"
        mock_rws.execute.return_value = {"status": "ok"}

        with patch(
            "orchestrator.terminal.remote_worker_server.get_remote_worker_server",
            return_value=mock_rws,
        ):
            from orchestrator.terminal.interactive import (
                _active_clis,
                open_interactive_cli_via_rws,
            )

            # Clean up any existing entry
            _active_clis.pop("session-1", None)

            cli = open_interactive_cli_via_rws(
                session_id="session-1",
                host="test-host",
                command="ls -la",
                cwd="/home/user",
                cols=120,
                rows=40,
            )

            assert cli.remote_pty_id == "pty123"
            assert cli.rws_host == "test-host"
            assert cli.session_id == "session-1"
            assert cli.status == "active"
            assert cli.window_name == "rws-pty123"

            mock_rws.create_pty.assert_called_once_with(
                cmd="/bin/bash",
                cwd="/home/user",
                cols=120,
                rows=40,
                session_id="session-1",
                role="interactive-cli",
            )
            # Verify command was sent
            mock_rws.execute.assert_called_once()
            call_args = mock_rws.execute.call_args[0][0]
            assert call_args["action"] == "pty_input"
            assert call_args["data"] == "ls -la\n"

            # Clean up
            _active_clis.pop("session-1", None)

    def test_close_rws_cli(self):
        """Test closing an RWS-backed interactive CLI destroys PTY."""
        from orchestrator.state.models import InteractiveCLI
        from orchestrator.terminal.interactive import _active_clis, close_interactive_cli

        mock_rws = MagicMock(spec=RemoteWorkerServer)

        cli = InteractiveCLI(
            session_id="session-2",
            window_name="rws-pty456",
            status="active",
            created_at="2025-01-01T00:00:00",
            remote_pty_id="pty456",
            rws_host="test-host",
        )
        _active_clis["session-2"] = cli

        with patch(
            "orchestrator.terminal.remote_worker_server.get_remote_worker_server",
            return_value=mock_rws,
        ):
            result = close_interactive_cli("session-2")

        assert result is True
        mock_rws.destroy_pty.assert_called_once_with("pty456")
        assert "session-2" not in _active_clis

    def test_capture_rws_cli(self):
        """Test capturing output from an RWS-backed interactive CLI."""
        from orchestrator.state.models import InteractiveCLI
        from orchestrator.terminal.interactive import _active_clis, capture_interactive_cli

        mock_rws = MagicMock(spec=RemoteWorkerServer)
        mock_rws.capture_pty.return_value = "$ ls\nfoo.txt\nbar.txt"

        cli = InteractiveCLI(
            session_id="session-3",
            window_name="rws-pty789",
            status="active",
            created_at="2025-01-01T00:00:00",
            remote_pty_id="pty789",
            rws_host="test-host",
        )
        _active_clis["session-3"] = cli

        with patch(
            "orchestrator.terminal.remote_worker_server.get_remote_worker_server",
            return_value=mock_rws,
        ):
            output = capture_interactive_cli("session-3", lines=30)

        assert output == "$ ls\nfoo.txt\nbar.txt"
        mock_rws.capture_pty.assert_called_once_with("pty789", lines=30)

        # Clean up
        _active_clis.pop("session-3", None)

    def test_send_to_rws_cli(self):
        """Test sending input to an RWS-backed interactive CLI."""
        from orchestrator.state.models import InteractiveCLI
        from orchestrator.terminal.interactive import _active_clis, send_to_interactive_cli

        mock_rws = MagicMock(spec=RemoteWorkerServer)
        mock_rws.execute.return_value = {"status": "ok"}

        cli = InteractiveCLI(
            session_id="session-4",
            window_name="rws-ptyabc",
            status="active",
            created_at="2025-01-01T00:00:00",
            remote_pty_id="ptyabc",
            rws_host="test-host",
        )
        _active_clis["session-4"] = cli

        with patch(
            "orchestrator.terminal.remote_worker_server.get_remote_worker_server",
            return_value=mock_rws,
        ):
            result = send_to_interactive_cli("session-4", text="echo hello", enter=True)

        assert result is True
        call_args = mock_rws.execute.call_args[0][0]
        assert call_args["action"] == "pty_input"
        assert call_args["data"] == "echo hello\n"

        # Clean up
        _active_clis.pop("session-4", None)

    def test_send_to_rws_cli_no_enter(self):
        """Test sending input without Enter key."""
        from orchestrator.state.models import InteractiveCLI
        from orchestrator.terminal.interactive import _active_clis, send_to_interactive_cli

        mock_rws = MagicMock(spec=RemoteWorkerServer)
        mock_rws.execute.return_value = {"status": "ok"}

        cli = InteractiveCLI(
            session_id="session-5",
            window_name="rws-ptydef",
            status="active",
            created_at="2025-01-01T00:00:00",
            remote_pty_id="ptydef",
            rws_host="test-host",
        )
        _active_clis["session-5"] = cli

        with patch(
            "orchestrator.terminal.remote_worker_server.get_remote_worker_server",
            return_value=mock_rws,
        ):
            result = send_to_interactive_cli("session-5", text="partial", enter=False)

        assert result is True
        call_args = mock_rws.execute.call_args[0][0]
        assert call_args["data"] == "partial"  # No \n

        # Clean up
        _active_clis.pop("session-5", None)

    def test_check_alive_rws_cli(self):
        """Test checking if an RWS-backed CLI is alive."""
        from orchestrator.state.models import InteractiveCLI
        from orchestrator.terminal.interactive import (
            _active_clis,
            check_interactive_cli_alive,
        )

        mock_rws = MagicMock(spec=RemoteWorkerServer)
        mock_rws.execute.return_value = {
            "status": "ok",
            "ptys": [{"pty_id": "ptyghi", "alive": True}],
        }

        cli = InteractiveCLI(
            session_id="session-6",
            window_name="rws-ptyghi",
            status="active",
            created_at="2025-01-01T00:00:00",
            remote_pty_id="ptyghi",
            rws_host="test-host",
        )
        _active_clis["session-6"] = cli

        with patch(
            "orchestrator.terminal.remote_worker_server.get_remote_worker_server",
            return_value=mock_rws,
        ):
            assert check_interactive_cli_alive("session-6") is True

        # Clean up
        _active_clis.pop("session-6", None)

    def test_check_alive_rws_cli_dead(self):
        """Test that dead PTY is cleaned up from registry."""
        from orchestrator.state.models import InteractiveCLI
        from orchestrator.terminal.interactive import (
            _active_clis,
            check_interactive_cli_alive,
        )

        mock_rws = MagicMock(spec=RemoteWorkerServer)
        mock_rws.execute.return_value = {"status": "ok", "ptys": []}

        cli = InteractiveCLI(
            session_id="session-7",
            window_name="rws-ptyjkl",
            status="active",
            created_at="2025-01-01T00:00:00",
            remote_pty_id="ptyjkl",
            rws_host="test-host",
        )
        _active_clis["session-7"] = cli

        with patch(
            "orchestrator.terminal.remote_worker_server.get_remote_worker_server",
            return_value=mock_rws,
        ):
            assert check_interactive_cli_alive("session-7") is False

        # Should be removed from registry
        assert "session-7" not in _active_clis


# ---------------------------------------------------------------------------
# Daemon script protocol tests
# ---------------------------------------------------------------------------


class TestDaemonProtocol:
    """Test the daemon script's expected protocol behavior."""

    def test_script_is_valid_python(self):
        """Verify the embedded script is syntactically valid Python."""
        from orchestrator.terminal.remote_worker_server import _REMOTE_WORKER_SERVER_SCRIPT

        # Should compile without errors
        compile(_REMOTE_WORKER_SERVER_SCRIPT, "<rws_daemon>", "exec")

    def test_bootstrap_command(self):
        """Verify the bootstrap command template is valid Python."""
        from orchestrator.terminal.remote_worker_server import (
            _BOOTSTRAP_TMPL,
            _SCRIPT_HASH,
        )

        # Should produce valid Python when formatted with a version
        bootstrap = _BOOTSTRAP_TMPL.format(version=_SCRIPT_HASH)
        compile(bootstrap, "<bootstrap>", "exec")

    def test_script_contains_required_handlers(self):
        """Verify the daemon script defines all required file operation handlers."""
        from orchestrator.terminal.remote_worker_server import _REMOTE_WORKER_SERVER_SCRIPT

        required_handlers = [
            "handle_ping",
            "handle_server_info",
            "handle_list_dir",
            "handle_read_file",
            "handle_write_file",
            "handle_delete",
            "handle_move",
            "handle_mkdir",
            "handle_pty_create",
            "handle_pty_destroy",
            "handle_pty_list",
            "handle_pty_capture",
            "handle_pty_resize",
            "handle_pty_input",
        ]
        for handler in required_handlers:
            assert f"def {handler}" in _REMOTE_WORKER_SERVER_SCRIPT, f"Missing handler: {handler}"

    def test_script_contains_daemonization(self):
        """Verify the daemon script has proper daemonization code."""
        from orchestrator.terminal.remote_worker_server import _REMOTE_WORKER_SERVER_SCRIPT

        assert "os.fork()" in _REMOTE_WORKER_SERVER_SCRIPT
        assert "os.setsid()" in _REMOTE_WORKER_SERVER_SCRIPT
        assert "os.devnull" in _REMOTE_WORKER_SERVER_SCRIPT

    def test_script_contains_ringbuffer(self):
        """Verify the daemon script has ringbuffer support."""
        from orchestrator.terminal.remote_worker_server import _REMOTE_WORKER_SERVER_SCRIPT

        assert "ringbuffer" in _REMOTE_WORKER_SERVER_SCRIPT
        assert "RINGBUFFER_MAX" in _REMOTE_WORKER_SERVER_SCRIPT

    def test_script_contains_pty_session_class(self):
        """Verify the daemon script has the PtySession class."""
        from orchestrator.terminal.remote_worker_server import _REMOTE_WORKER_SERVER_SCRIPT

        assert "class PtySession" in _REMOTE_WORKER_SERVER_SCRIPT
        assert "master_fd" in _REMOTE_WORKER_SERVER_SCRIPT
        assert "stream_conns" in _REMOTE_WORKER_SERVER_SCRIPT


# ---------------------------------------------------------------------------
# Tunnel SSH Options & TCP Port Probe
# ---------------------------------------------------------------------------


class TestTunnelSSHOpts:
    """Verify _TUNNEL_SSH_OPTS disables ControlMaster for the forward tunnel."""

    def test_tunnel_ssh_opts_disables_control_master(self):
        """_TUNNEL_SSH_OPTS must have ControlMaster=no before ControlMaster=auto."""
        from orchestrator.terminal.remote_worker_server import _TUNNEL_SSH_OPTS

        # Find indices of both ControlMaster values
        opts_str = " ".join(_TUNNEL_SSH_OPTS)
        idx_no = opts_str.index("ControlMaster=no")
        idx_auto = opts_str.index("ControlMaster=auto")
        assert idx_no < idx_auto, "ControlMaster=no must come before auto (SSH first-match-wins)"

    def test_tunnel_ssh_opts_has_control_path_none(self):
        """_TUNNEL_SSH_OPTS must contain ControlPath=none."""
        from orchestrator.terminal.remote_worker_server import _TUNNEL_SSH_OPTS

        assert "ControlPath=none" in _TUNNEL_SSH_OPTS

    def test_start_tunnel_uses_tunnel_opts(self):
        """_start_tunnel() must use _TUNNEL_SSH_OPTS, not _SSH_OPTS."""
        rws = RemoteWorkerServer("test-host")
        rws._local_port = 12345

        with patch("orchestrator.terminal._rws_client.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            with (
                patch(
                    "orchestrator.terminal._rws_client.socket.socket",
                ),
                patch.object(rws, "_is_tunnel_port_open", return_value=True),
            ):
                rws._start_tunnel()

            cmd = mock_popen.call_args[0][0]
            # The command should contain ControlMaster=no (from _TUNNEL_SSH_OPTS)
            assert "ControlMaster=no" in cmd

    def test_start_retries_tunnel_on_failure(self):
        """start() retries tunnel+socket when first attempt fails."""
        rws = RemoteWorkerServer("test-host")
        attempt_count = 0

        def mock_start_tunnel():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count == 1:
                raise RuntimeError("tunnel failed")

        with (
            patch.object(rws, "_deploy_daemon"),
            patch.object(rws, "_start_tunnel", side_effect=mock_start_tunnel),
            patch.object(rws, "_connect_command_socket"),
            patch.object(rws, "_cleanup_tunnel"),
            patch("orchestrator.terminal._rws_client.time.sleep"),
        ):
            rws.start()

        assert attempt_count == 2

    def test_start_cleans_up_tunnel_between_retries(self):
        """start() calls _cleanup_tunnel between retry attempts."""
        rws = RemoteWorkerServer("test-host")
        cleanup_calls = 0

        def mock_cleanup():
            nonlocal cleanup_calls
            cleanup_calls += 1

        attempt = 0

        def mock_start_tunnel():
            nonlocal attempt
            attempt += 1
            if attempt <= 2:
                raise RuntimeError("tunnel failed")

        with (
            patch.object(rws, "_deploy_daemon"),
            patch.object(rws, "_start_tunnel", side_effect=mock_start_tunnel),
            patch.object(rws, "_connect_command_socket"),
            patch.object(rws, "_cleanup_tunnel", side_effect=mock_cleanup),
            patch("orchestrator.terminal._rws_client.time.sleep"),
        ):
            rws.start()

        assert cleanup_calls == 2  # cleaned up after attempt 1 and 2

    def test_start_raises_after_all_tunnel_attempts(self):
        """start() raises after all 3 tunnel attempts fail."""
        rws = RemoteWorkerServer("test-host")

        with (
            patch.object(rws, "_deploy_daemon"),
            patch.object(rws, "_start_tunnel", side_effect=RuntimeError("tunnel failed")),
            patch.object(rws, "_cleanup_tunnel"),
            patch("orchestrator.terminal._rws_client.time.sleep"),
        ):
            with pytest.raises(RuntimeError, match="All 3 tunnel attempts"):
                rws.start()

    def test_start_deploys_daemon_only_once(self):
        """_deploy_daemon is called once even when tunnel retries happen."""
        rws = RemoteWorkerServer("test-host")
        deploy_count = 0

        def mock_deploy(timeout):
            nonlocal deploy_count
            deploy_count += 1

        attempt = 0

        def mock_start_tunnel():
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise RuntimeError("tunnel failed")

        with (
            patch.object(rws, "_deploy_daemon", side_effect=mock_deploy),
            patch.object(rws, "_start_tunnel", side_effect=mock_start_tunnel),
            patch.object(rws, "_connect_command_socket"),
            patch.object(rws, "_cleanup_tunnel"),
            patch("orchestrator.terminal._rws_client.time.sleep"),
        ):
            rws.start()

        assert deploy_count == 1

    def test_tunnel_waits_for_port_open(self):
        """_start_tunnel polls until port becomes open."""
        rws = RemoteWorkerServer("test-host")
        rws._local_port = 12345

        call_count = 0

        def port_open_after_3_calls(timeout=2.0):
            nonlocal call_count
            call_count += 1
            return call_count >= 3

        with (
            patch("orchestrator.terminal._rws_client.subprocess.Popen") as mock_popen,
            patch("orchestrator.terminal._rws_client.socket.socket"),
            patch.object(rws, "_is_tunnel_port_open", side_effect=port_open_after_3_calls),
            patch("orchestrator.terminal._rws_client.time.sleep"),
            patch("orchestrator.terminal._rws_client.time.monotonic") as mock_time,
        ):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc
            # Simulate time passing but staying within deadline
            mock_time.side_effect = [0.0, 1.0, 2.0, 3.0, 4.0]

            rws._start_tunnel()

        assert call_count == 3  # polled 3 times before succeeding

    def test_tunnel_times_out_when_never_ready(self):
        """_start_tunnel raises when port never opens within deadline."""
        rws = RemoteWorkerServer("test-host")
        rws._local_port = 12345

        with (
            patch("orchestrator.terminal._rws_client.subprocess.Popen") as mock_popen,
            patch("orchestrator.terminal._rws_client.socket.socket"),
            patch.object(rws, "_is_tunnel_port_open", return_value=False),
            patch("orchestrator.terminal._rws_client.time.sleep"),
            patch("orchestrator.terminal._rws_client.time.monotonic") as mock_time,
        ):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc
            # Simulate time jumping past deadline immediately
            mock_time.side_effect = [0.0, 16.0]

            with pytest.raises(RuntimeError, match="timed out"):
                rws._start_tunnel()


class TestTunnelPortProbe:
    """Tests for _is_tunnel_port_open() and its integration with health checks."""

    def test_is_tunnel_port_open_success(self):
        """Port probe returns True when TCP connection succeeds."""
        rws = RemoteWorkerServer("test-host")
        rws._local_port = 12345

        target = "orchestrator.terminal._rws_client.socket.create_connection"
        with patch(target) as mock_conn:
            mock_sock = MagicMock()
            mock_conn.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            assert rws._is_tunnel_port_open() is True

    def test_is_tunnel_port_open_failure(self):
        """Port probe returns False when TCP connection is refused."""
        rws = RemoteWorkerServer("test-host")
        rws._local_port = 12345

        with patch(
            "orchestrator.terminal._rws_client.socket.create_connection",
            side_effect=ConnectionRefusedError,
        ):
            assert rws._is_tunnel_port_open() is False

    def test_is_tunnel_port_open_no_port(self):
        """Port probe returns False when no local port is set."""
        rws = RemoteWorkerServer("test-host")
        rws._local_port = None
        assert rws._is_tunnel_port_open() is False

    def test_is_alive_process_dead_but_port_open(self):
        """is_alive() continues to ping when process exited but port is open."""
        rws = RemoteWorkerServer("test-host")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # Process exited
        rws._tunnel_proc = mock_proc
        rws._cmd_sock = MagicMock()

        with patch.object(rws, "_is_tunnel_port_open", return_value=True):
            # Should NOT short-circuit to False — it should proceed to ping
            with patch.object(rws, "execute", return_value={"status": "pong"}):
                assert rws.is_alive() is True

    def test_is_alive_process_dead_and_port_closed(self):
        """is_alive() returns False when process exited and port is closed."""
        rws = RemoteWorkerServer("test-host")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        rws._tunnel_proc = mock_proc
        rws._cmd_sock = MagicMock()

        with patch.object(rws, "_is_tunnel_port_open", return_value=False):
            assert rws.is_alive() is False

    def test_pool_check_process_dead_but_port_open(self):
        """Server stays in pool when process exited but port is still open."""
        rws = RemoteWorkerServer("test-host")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # Process exited
        rws._tunnel_proc = mock_proc
        rws._cmd_sock = MagicMock()  # Socket exists
        _server_pool["test-host"] = rws

        try:
            with patch.object(rws, "_is_tunnel_port_open", return_value=True):
                result = get_remote_worker_server("test-host")
            assert result is rws
            assert "test-host" in _server_pool
        finally:
            _server_pool.pop("test-host", None)

    def test_pool_check_truly_dead(self):
        """Server removed from pool when process exited and port is closed."""
        rws = RemoteWorkerServer("test-host")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        rws._tunnel_proc = mock_proc
        rws._cmd_sock = MagicMock()
        _server_pool["test-host"] = rws

        try:
            with patch.object(rws, "_is_tunnel_port_open", return_value=False):
                with patch.object(RemoteWorkerServer, "start"):
                    with pytest.raises(RuntimeError, match="Connecting to remote host"):
                        get_remote_worker_server("test-host")
            assert "test-host" not in _server_pool
        finally:
            _server_pool.pop("test-host", None)

    def test_ensure_connected_tcp_fallback(self):
        """_ensure_connected() reconnects when process dead but port open."""
        rws = RemoteWorkerServer("test-host")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # Process exited
        rws._tunnel_proc = mock_proc
        rws._cmd_sock = None
        rws._local_port = 12345

        with patch.object(rws, "_is_tunnel_port_open", return_value=True):
            with patch.object(rws, "_connect_command_socket"):
                rws._ensure_connected()
                rws._connect_command_socket.assert_called_once()


# ---------------------------------------------------------------------------
# Browser start polling
# ---------------------------------------------------------------------------


class TestStartBrowserPolling:
    """Test start_browser() polling when daemon returns 'installing' status."""

    def test_immediate_success(self):
        """start_browser() returns immediately on normal success."""
        rws = RemoteWorkerServer("test-host")

        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.recv.return_value = (
            json.dumps({"status": "ok", "pid": 1234, "port": 9222}).encode() + b"\n"
        )
        rws._cmd_sock = mock_sock
        rws._cmd_buffer = bytearray()

        result = rws.start_browser("session-1", port=9222)
        assert result["status"] == "ok"
        assert result["pid"] == 1234

    def test_polls_on_installing_then_succeeds(self):
        """start_browser() polls when daemon says 'installing', then succeeds."""
        rws = RemoteWorkerServer("test-host")
        responses = [
            {"status": "installing"},
            {"status": "installing"},
            {"status": "ok", "pid": 5678, "port": 9222},
        ]

        with (
            patch.object(rws, "execute", side_effect=responses) as mock_exec,
            patch("time.sleep"),
        ):
            result = rws.start_browser("session-1", port=9222, timeout=60.0)

        assert result["status"] == "ok"
        assert result["pid"] == 5678
        assert mock_exec.call_count == 3

    def test_timeout_during_polling(self):
        """start_browser() raises on timeout while polling 'installing'."""
        rws = RemoteWorkerServer("test-host")

        with (
            patch.object(rws, "execute", return_value={"status": "installing"}),
            patch("time.sleep"),
        ):
            with pytest.raises(RuntimeError, match="timed out"):
                rws.start_browser("session-1", port=9222, timeout=0.01)

    def test_error_response_raises(self):
        """start_browser() raises on error response (not 'installing')."""
        rws = RemoteWorkerServer("test-host")

        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.recv.return_value = (
            json.dumps({"error": "Port 9222 is already in use"}).encode() + b"\n"
        )
        rws._cmd_sock = mock_sock
        rws._cmd_buffer = bytearray()

        with pytest.raises(RuntimeError, match="Port 9222 is already in use"):
            rws.start_browser("session-1", port=9222)
