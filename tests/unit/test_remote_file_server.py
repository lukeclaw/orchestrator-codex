"""Unit tests for the persistent remote file server module."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.terminal.remote_file_server import (
    _BOOTSTRAP,
    _REMOTE_FILE_SERVER_SCRIPT,
    RemoteFileServer,
    _pool_lock,
    _server_pool,
    _starting,
    get_remote_file_server,
    shutdown_all_servers,
)


@pytest.mark.allow_subprocess
class TestRemoteFileServerScript:
    """Test the server script by running it in a local subprocess."""

    def _start_local_server(self):
        """Start the server script locally (no SSH) for testing."""
        import base64
        import sys

        proc = subprocess.Popen(
            [sys.executable, "-u", "-c", _BOOTSTRAP],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Send the base64-encoded script
        encoded = base64.b64encode(_REMOTE_FILE_SERVER_SCRIPT.encode()).decode() + "\n"
        proc.stdin.write(encoded.encode())
        proc.stdin.flush()
        return proc

    def _send_command(self, proc, cmd: dict) -> dict:
        line = json.dumps(cmd) + "\n"
        proc.stdin.write(line.encode())
        proc.stdin.flush()
        resp_line = proc.stdout.readline().decode().strip()
        return json.loads(resp_line)

    def test_ping(self):
        proc = self._start_local_server()
        try:
            resp = self._send_command(proc, {"action": "ping"})
            assert resp == {"status": "pong"}
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_list_dir(self, tmp_path):
        (tmp_path / "hello.py").write_text("print('hi')")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "inner.txt").write_text("nested")

        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "list_dir",
                    "work_dir": str(tmp_path),
                    "path": ".",
                    "show_ignored": False,
                    "depth": 1,
                },
            )
            assert "entries" in resp
            names = [e["name"] for e in resp["entries"]]
            assert "subdir" in names
            assert "hello.py" in names
            # Dirs should come first
            dir_idx = names.index("subdir")
            file_idx = names.index("hello.py")
            assert dir_idx < file_idx
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_list_dir_not_found(self, tmp_path):
        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "list_dir",
                    "work_dir": str(tmp_path),
                    "path": "nonexistent",
                    "show_ignored": False,
                },
            )
            assert "error" in resp
            assert "not found" in resp["error"].lower()
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_read_file(self, tmp_path):
        (tmp_path / "test.py").write_text("line1\nline2\nline3\n")

        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "read_file",
                    "work_dir": str(tmp_path),
                    "path": "test.py",
                    "max_lines": 500,
                },
            )
            assert resp["content"] == "line1\nline2\nline3\n"
            assert resp["binary"] is False
            assert resp["truncated"] is False
            assert resp["total_lines"] == 3
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_read_file_truncated(self, tmp_path):
        (tmp_path / "big.txt").write_text("\n".join(f"line {i}" for i in range(100)))

        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "read_file",
                    "work_dir": str(tmp_path),
                    "path": "big.txt",
                    "max_lines": 10,
                },
            )
            assert resp["truncated"] is True
            assert resp["total_lines"] == 100
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_read_binary_file(self, tmp_path):
        (tmp_path / "binary.bin").write_bytes(b"\x00\x01\x02\xff")

        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "read_file",
                    "work_dir": str(tmp_path),
                    "path": "binary.bin",
                },
            )
            assert resp["binary"] is True
            assert resp["content"] == ""
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_read_file_not_found(self, tmp_path):
        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "read_file",
                    "work_dir": str(tmp_path),
                    "path": "missing.txt",
                },
            )
            assert "error" in resp
            assert "not found" in resp["error"].lower()
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_write_file(self, tmp_path):
        import base64

        (tmp_path / "existing.txt").write_text("original")
        content_b64 = base64.b64encode(b"updated content").decode()

        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "write_file",
                    "work_dir": str(tmp_path),
                    "path": "existing.txt",
                    "content_b64": content_b64,
                },
            )
            assert resp["conflict"] is False
            assert (tmp_path / "existing.txt").read_text() == "updated content"
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_write_file_create(self, tmp_path):
        import base64

        content_b64 = base64.b64encode(b"new file content").decode()

        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "write_file",
                    "work_dir": str(tmp_path),
                    "path": "newdir/newfile.txt",
                    "content_b64": content_b64,
                    "create": True,
                },
            )
            assert resp["conflict"] is False
            assert (tmp_path / "newdir" / "newfile.txt").read_text() == "new file content"
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_write_file_conflict(self, tmp_path):
        import base64

        (tmp_path / "conflict.txt").write_text("original")
        content_b64 = base64.b64encode(b"new").decode()

        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "write_file",
                    "work_dir": str(tmp_path),
                    "path": "conflict.txt",
                    "content_b64": content_b64,
                    "expected_mtime": 1000000.0,
                },
            )
            assert resp["conflict"] is True
            assert (tmp_path / "conflict.txt").read_text() == "original"
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_unknown_action(self):
        proc = self._start_local_server()
        try:
            resp = self._send_command(proc, {"action": "nonexistent"})
            assert "error" in resp
            assert "unknown" in resp["error"].lower()
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_invalid_json(self):
        proc = self._start_local_server()
        try:
            proc.stdin.write(b"not json\n")
            proc.stdin.flush()
            resp_line = proc.stdout.readline().decode().strip()
            resp = json.loads(resp_line)
            assert "error" in resp
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_delete_file(self, tmp_path):
        (tmp_path / "doomed.txt").write_text("delete me")

        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "delete",
                    "work_dir": str(tmp_path),
                    "path": "doomed.txt",
                },
            )
            assert resp == {"status": "ok"}
            assert not (tmp_path / "doomed.txt").exists()
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_delete_directory(self, tmp_path):
        d = tmp_path / "mydir"
        d.mkdir()
        (d / "inner.txt").write_text("hi")

        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "delete",
                    "work_dir": str(tmp_path),
                    "path": "mydir",
                },
            )
            assert resp == {"status": "ok"}
            assert not d.exists()
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_delete_not_found(self, tmp_path):
        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "delete",
                    "work_dir": str(tmp_path),
                    "path": "nonexistent.txt",
                },
            )
            assert "error" in resp
            assert "not found" in resp["error"].lower()
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_delete_work_dir_rejected(self, tmp_path):
        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "delete",
                    "work_dir": str(tmp_path),
                    "path": ".",
                },
            )
            assert "error" in resp
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_move_rename_file(self, tmp_path):
        (tmp_path / "old.txt").write_text("content")

        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "move",
                    "work_dir": str(tmp_path),
                    "from_path": "old.txt",
                    "to_path": "new.txt",
                },
            )
            assert resp == {"status": "ok"}
            assert not (tmp_path / "old.txt").exists()
            assert (tmp_path / "new.txt").read_text() == "content"
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_move_into_directory(self, tmp_path):
        (tmp_path / "file.txt").write_text("hello")
        (tmp_path / "subdir").mkdir()

        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "move",
                    "work_dir": str(tmp_path),
                    "from_path": "file.txt",
                    "to_path": "subdir/file.txt",
                },
            )
            assert resp == {"status": "ok"}
            assert (tmp_path / "subdir" / "file.txt").read_text() == "hello"
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_move_creates_parent_dirs(self, tmp_path):
        (tmp_path / "file.txt").write_text("data")

        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "move",
                    "work_dir": str(tmp_path),
                    "from_path": "file.txt",
                    "to_path": "deep/nested/file.txt",
                },
            )
            assert resp == {"status": "ok"}
            assert (tmp_path / "deep" / "nested" / "file.txt").read_text() == "data"
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_move_not_found(self, tmp_path):
        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "move",
                    "work_dir": str(tmp_path),
                    "from_path": "missing.txt",
                    "to_path": "dest.txt",
                },
            )
            assert "error" in resp
            assert "not found" in resp["error"].lower()
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_mkdir_creates_directory(self, tmp_path):
        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "mkdir",
                    "work_dir": str(tmp_path),
                    "path": "newdir",
                },
            )
            assert resp == {"status": "ok"}
            assert (tmp_path / "newdir").is_dir()
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_mkdir_nested(self, tmp_path):
        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "mkdir",
                    "work_dir": str(tmp_path),
                    "path": "a/b/c",
                },
            )
            assert resp == {"status": "ok"}
            assert (tmp_path / "a" / "b" / "c").is_dir()
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_mkdir_work_dir_rejected(self, tmp_path):
        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "mkdir",
                    "work_dir": str(tmp_path),
                    "path": "",
                },
            )
            assert "error" in resp
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_multiple_commands(self, tmp_path):
        """Verify the server handles multiple sequential commands."""
        (tmp_path / "a.py").write_text("file a")
        (tmp_path / "b.py").write_text("file b")

        proc = self._start_local_server()
        try:
            # Ping
            resp = self._send_command(proc, {"action": "ping"})
            assert resp["status"] == "pong"

            # List
            resp = self._send_command(
                proc,
                {
                    "action": "list_dir",
                    "work_dir": str(tmp_path),
                    "path": ".",
                    "show_ignored": False,
                },
            )
            assert len(resp["entries"]) == 2

            # Read a
            resp = self._send_command(
                proc,
                {
                    "action": "read_file",
                    "work_dir": str(tmp_path),
                    "path": "a.py",
                },
            )
            assert resp["content"] == "file a"

            # Read b
            resp = self._send_command(
                proc,
                {
                    "action": "read_file",
                    "work_dir": str(tmp_path),
                    "path": "b.py",
                },
            )
            assert resp["content"] == "file b"
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()

    def test_path_traversal_rejected(self, tmp_path):
        proc = self._start_local_server()
        try:
            resp = self._send_command(
                proc,
                {
                    "action": "list_dir",
                    "work_dir": str(tmp_path),
                    "path": "../../etc",
                    "show_ignored": False,
                },
            )
            assert "error" in resp
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()


class TestRemoteFileServerClass:
    """Test the RemoteFileServer class with mocked subprocess."""

    def test_start_and_ping(self):
        """Test that start() sends script and verifies ping."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline.return_value = b'{"status": "pong"}\n'

        with patch(
            "orchestrator.terminal.remote_file_server.subprocess.Popen", return_value=mock_proc
        ):
            server = RemoteFileServer("testhost")
            server.start()

        assert server.is_alive()
        # Verify the script was sent
        assert mock_proc.stdin.write.called
        assert mock_proc.stdin.flush.called

    def test_execute_sends_json_and_reads_response(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline.return_value = b'{"result": "ok"}\n'

        server = RemoteFileServer("testhost")
        server._process = mock_proc

        resp = server.execute({"action": "ping"})
        assert resp == {"result": "ok"}

    def test_execute_raises_on_dead_process(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # process exited

        server = RemoteFileServer("testhost")
        server._process = mock_proc

        with pytest.raises(RuntimeError, match="not running"):
            server.execute({"action": "ping"})

    def test_execute_raises_on_no_process(self):
        server = RemoteFileServer("testhost")

        with pytest.raises(RuntimeError, match="not running"):
            server.execute({"action": "ping"})

    def test_stop_kills_process(self):
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()

        server = RemoteFileServer("testhost")
        server._process = mock_proc

        server.stop()
        mock_proc.stdin.close.assert_called_once()
        mock_proc.kill.assert_called_once()
        assert server._process is None

    def test_stop_is_idempotent(self):
        server = RemoteFileServer("testhost")
        server.stop()  # no-op, no error

    def test_is_alive_false_when_no_process(self):
        server = RemoteFileServer("testhost")
        assert server.is_alive() is False


class TestServerPool:
    def setup_method(self):
        """Clean up the pool before each test."""
        with _pool_lock:
            for s in _server_pool.values():
                try:
                    s.stop()
                except Exception:
                    pass
            _server_pool.clear()
            _starting.clear()

    def _wait_for_background_start(self, host: str, timeout: float = 2.0) -> None:
        """Wait for a background start thread to finish."""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with _pool_lock:
                t = _starting.get(host)
                if t is None or not t.is_alive():
                    return
            time.sleep(0.01)

    @patch("orchestrator.terminal.remote_file_server.RemoteFileServer")
    def test_first_call_raises_and_starts_background(self, mock_server_cls):
        """First call should raise RuntimeError and start background thread."""
        mock_instance = MagicMock()
        mock_instance.is_alive.return_value = True
        mock_server_cls.return_value = mock_instance

        with pytest.raises(RuntimeError, match="starting in background"):
            get_remote_file_server("new-host")

        # Wait for background thread to finish
        self._wait_for_background_start("new-host")
        mock_instance.start.assert_called_once()

    @patch("orchestrator.terminal.remote_file_server.RemoteFileServer")
    def test_second_call_returns_ready_server(self, mock_server_cls):
        """After background start completes, subsequent calls return the server."""
        mock_instance = MagicMock()
        mock_instance.is_alive.return_value = True
        mock_server_cls.return_value = mock_instance

        # First call triggers background start
        with pytest.raises(RuntimeError):
            get_remote_file_server("host-a")

        self._wait_for_background_start("host-a")

        # Second call returns the ready server
        server = get_remote_file_server("host-a")
        assert server is mock_instance
        assert mock_instance.start.call_count == 1

    @patch("orchestrator.terminal.remote_file_server.RemoteFileServer")
    def test_returns_existing_alive_server(self, mock_server_cls):
        """If a server is already in the pool and alive, return it immediately."""
        mock_instance = MagicMock()
        mock_instance.is_alive.return_value = True

        # Pre-populate the pool directly
        with _pool_lock:
            _server_pool["host-pre"] = mock_instance

        server = get_remote_file_server("host-pre")
        assert server is mock_instance
        mock_server_cls.assert_not_called()

    @patch("orchestrator.terminal.remote_file_server.RemoteFileServer")
    def test_replaces_dead_server(self, mock_server_cls):
        """Dead server in pool triggers a background restart."""
        dead_instance = MagicMock()
        dead_instance.is_alive.return_value = False

        new_instance = MagicMock()
        new_instance.is_alive.return_value = True
        mock_server_cls.return_value = new_instance

        # Pre-populate with dead server
        with _pool_lock:
            _server_pool["host-dead"] = dead_instance

        # First call with dead server: raises and starts background
        with pytest.raises(RuntimeError, match="starting in background"):
            get_remote_file_server("host-dead")

        self._wait_for_background_start("host-dead")

        # Now it should return the new server
        server = get_remote_file_server("host-dead")
        assert server is new_instance

    @patch("orchestrator.terminal.remote_file_server.RemoteFileServer")
    def test_background_start_failure_does_not_pollute_pool(self, mock_server_cls):
        """If background start fails, pool stays empty, next call retries."""
        failing_instance = MagicMock()
        failing_instance.start.side_effect = RuntimeError("SSH connection failed")
        mock_server_cls.return_value = failing_instance

        with pytest.raises(RuntimeError, match="starting in background"):
            get_remote_file_server("bad-host")

        self._wait_for_background_start("bad-host")

        # Pool should be empty — the failed server was never stored
        with _pool_lock:
            assert "bad-host" not in _server_pool

    @patch("orchestrator.terminal.remote_file_server.RemoteFileServer")
    def test_shutdown_all_servers(self, mock_server_cls):
        mock_a = MagicMock()
        mock_a.is_alive.return_value = True
        mock_b = MagicMock()
        mock_b.is_alive.return_value = True

        # Pre-populate the pool directly (bypass background start)
        with _pool_lock:
            _server_pool["host-x"] = mock_a
            _server_pool["host-y"] = mock_b

        shutdown_all_servers()

        mock_a.stop.assert_called_once()
        mock_b.stop.assert_called_once()

        with _pool_lock:
            assert len(_server_pool) == 0

    @patch("orchestrator.terminal.remote_file_server.RemoteFileServer")
    def test_different_hosts_get_different_servers(self, mock_server_cls):
        instances = []

        def make_instance(host):
            m = MagicMock()
            m.is_alive.return_value = True
            instances.append(m)
            return m

        mock_server_cls.side_effect = make_instance

        # Trigger background starts for both hosts
        with pytest.raises(RuntimeError):
            get_remote_file_server("host-1")
        with pytest.raises(RuntimeError):
            get_remote_file_server("host-2")

        self._wait_for_background_start("host-1")
        self._wait_for_background_start("host-2")

        s1 = get_remote_file_server("host-1")
        s2 = get_remote_file_server("host-2")
        assert s1 is not s2
        assert len(instances) == 2

    @patch("orchestrator.terminal.remote_file_server.RemoteFileServer")
    def test_concurrent_calls_only_start_once(self, mock_server_cls):
        """Multiple calls for the same host while starting should not
        launch multiple background threads."""
        mock_instance = MagicMock()
        mock_instance.is_alive.return_value = True

        # Make start() slow so the thread is still alive on second call
        import time

        def slow_start(*args, **kwargs):
            time.sleep(0.1)

        mock_instance.start.side_effect = slow_start
        mock_server_cls.return_value = mock_instance

        with pytest.raises(RuntimeError, match="starting in background"):
            get_remote_file_server("host-dup")

        # Second call while first is still starting
        with pytest.raises(RuntimeError, match="still starting up"):
            get_remote_file_server("host-dup")

        # Only one RemoteFileServer instance created
        assert mock_server_cls.call_count == 1


class TestBootstrapConstant:
    def test_bootstrap_is_valid_python(self):
        """The bootstrap string should be valid Python syntax."""
        compile(_BOOTSTRAP, "<bootstrap>", "exec")
