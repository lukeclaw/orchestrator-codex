"""Unit tests for orchestrator/terminal/interactive.py."""

from unittest.mock import patch

import pytest

from orchestrator.terminal.interactive import (
    _active_clis,
    _restore_remote_iclis,
    capture_interactive_cli,
    check_interactive_cli_alive,
    close_interactive_cli,
    get_active_cli,
    open_interactive_cli,
    recover_cli,
    restore_icli_windows,
    send_to_interactive_cli,
)


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear in-memory registry before each test."""
    _active_clis.clear()
    yield
    _active_clis.clear()


class TestOpenInteractiveCLI:
    """Tests for open_interactive_cli (local)."""

    @patch("orchestrator.terminal.interactive.tmux.create_window")
    @patch("orchestrator.terminal.interactive.tmux.send_keys")
    def test_creates_tmux_window(self, mock_send, mock_create):
        cli = open_interactive_cli("orchestrator", "worker1", "sess-1")

        mock_create.assert_called_once_with("orchestrator", "worker1-icli", cwd=None)
        assert cli.session_id == "sess-1"
        assert cli.window_name == "worker1-icli"
        assert cli.status == "active"
        assert cli.initial_command is None
        mock_send.assert_not_called()

    @patch("orchestrator.terminal.interactive.tmux.create_window")
    @patch("orchestrator.terminal.interactive.tmux.send_keys")
    def test_sends_command(self, mock_send, mock_create):
        cli = open_interactive_cli(
            "orchestrator", "worker1", "sess-1", command="sudo yum install screen"
        )

        mock_send.assert_called_once_with(
            "orchestrator", "worker1-icli", "sudo yum install screen", enter=True
        )
        assert cli.initial_command == "sudo yum install screen"

    @patch("orchestrator.terminal.interactive.tmux.create_window")
    @patch("orchestrator.terminal.interactive.tmux.send_keys")
    def test_passes_cwd(self, mock_send, mock_create):
        open_interactive_cli("orchestrator", "worker1", "sess-1", cwd="/home/user")

        mock_create.assert_called_once_with("orchestrator", "worker1-icli", cwd="/home/user")

    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_rejects_duplicate(self, mock_create):
        open_interactive_cli("orchestrator", "worker1", "sess-1")

        with pytest.raises(ValueError, match="already active"):
            open_interactive_cli("orchestrator", "worker1", "sess-1")

    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_registers_in_memory(self, mock_create):
        cli = open_interactive_cli("orchestrator", "worker1", "sess-1")

        assert get_active_cli("sess-1") is cli
        assert get_active_cli("nonexistent") is None


class TestCloseInteractiveCLI:
    """Tests for close_interactive_cli."""

    @patch("orchestrator.terminal.interactive.tmux.kill_window")
    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_closes_and_removes(self, mock_create, mock_kill):
        open_interactive_cli("orchestrator", "worker1", "sess-1")

        result = close_interactive_cli("sess-1", "orchestrator")

        assert result is True
        mock_kill.assert_called_once_with("orchestrator", "worker1-icli")
        assert get_active_cli("sess-1") is None

    def test_returns_false_for_nonexistent(self):
        result = close_interactive_cli("nonexistent")
        assert result is False


class TestCaptureInteractiveCLI:
    """Tests for capture_interactive_cli."""

    @patch("orchestrator.terminal.interactive.tmux.capture_output", return_value="$ hello\n")
    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_captures_output(self, mock_create, mock_capture):
        open_interactive_cli("orchestrator", "worker1", "sess-1")

        output = capture_interactive_cli("sess-1", "orchestrator", lines=20)

        mock_capture.assert_called_once_with("orchestrator", "worker1-icli", lines=20)
        assert output == "$ hello\n"

    def test_returns_none_for_nonexistent(self):
        assert capture_interactive_cli("nonexistent") is None


class TestSendToInteractiveCLI:
    """Tests for send_to_interactive_cli."""

    @patch("orchestrator.terminal.interactive.tmux.send_keys", return_value=True)
    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_sends_with_enter(self, mock_create, mock_send):
        open_interactive_cli("orchestrator", "worker1", "sess-1")

        result = send_to_interactive_cli("sess-1", "orchestrator", "yes", enter=True)

        assert result is True
        mock_send.assert_called_once_with("orchestrator", "worker1-icli", "yes", enter=True)

    @patch("orchestrator.terminal.interactive.tmux.send_keys", return_value=True)
    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_sends_without_enter(self, mock_create, mock_send):
        open_interactive_cli("orchestrator", "worker1", "sess-1")

        result = send_to_interactive_cli("sess-1", "orchestrator", "C-c", enter=False)

        assert result is True
        mock_send.assert_called_once_with("orchestrator", "worker1-icli", "C-c", enter=False)

    def test_returns_false_for_nonexistent(self):
        result = send_to_interactive_cli("nonexistent", "orchestrator", "test")
        assert result is False


class TestCheckInteractiveCLIAlive:
    """Tests for check_interactive_cli_alive."""

    @patch("orchestrator.terminal.interactive.tmux.window_exists", return_value=True)
    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_alive_returns_true(self, mock_create, mock_exists):
        open_interactive_cli("orchestrator", "worker1", "sess-1")

        assert check_interactive_cli_alive("sess-1", "orchestrator") is True

    @patch("orchestrator.terminal.interactive.tmux.window_exists", return_value=False)
    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_dead_removes_from_registry(self, mock_create, mock_exists):
        open_interactive_cli("orchestrator", "worker1", "sess-1")

        result = check_interactive_cli_alive("sess-1", "orchestrator")

        assert result is False
        assert get_active_cli("sess-1") is None

    def test_nonexistent_returns_false(self):
        assert check_interactive_cli_alive("nonexistent") is False


class TestRestoreICLIWindows:
    """Tests for restore_icli_windows."""

    @patch("orchestrator.terminal.interactive.tmux.kill_window")
    @patch("orchestrator.terminal.interactive.tmux.list_windows")
    def test_restores_matching_sessions(self, mock_list, mock_kill):
        from unittest.mock import MagicMock

        from orchestrator.terminal.manager import TmuxWindow

        mock_list.return_value = [
            TmuxWindow(index=0, name="worker1", active=True),
            TmuxWindow(index=1, name="worker1-icli", active=False),
            TmuxWindow(index=2, name="worker2", active=False),
            TmuxWindow(index=3, name="worker2-icli", active=False),
        ]

        # Mock DB: worker1 exists, worker2 exists
        mock_conn = MagicMock()
        mock_session1 = MagicMock(id="sess-1", name="worker1")
        mock_session2 = MagicMock(id="sess-2", name="worker2")

        with patch(
            "orchestrator.state.repositories.sessions.get_session_by_name",
            side_effect=lambda conn, name: {"worker1": mock_session1, "worker2": mock_session2}.get(
                name
            ),
        ):
            restored = restore_icli_windows(mock_conn, "orchestrator")

        assert restored == 2
        assert get_active_cli("sess-1") is not None
        assert get_active_cli("sess-1").window_name == "worker1-icli"
        assert get_active_cli("sess-2") is not None
        assert get_active_cli("sess-2").window_name == "worker2-icli"
        mock_kill.assert_not_called()

    @patch("orchestrator.terminal.interactive.tmux.kill_window")
    @patch("orchestrator.terminal.interactive.tmux.list_windows")
    def test_kills_orphaned_windows(self, mock_list, mock_kill):
        from unittest.mock import MagicMock

        from orchestrator.terminal.manager import TmuxWindow

        mock_list.return_value = [
            TmuxWindow(index=0, name="worker1", active=True),
            TmuxWindow(index=1, name="worker1-icli", active=False),
        ]

        # Mock DB: worker1 does NOT exist
        mock_conn = MagicMock()
        with patch(
            "orchestrator.state.repositories.sessions.get_session_by_name",
            return_value=None,
        ):
            restored = restore_icli_windows(mock_conn, "orchestrator")

        assert restored == 0
        mock_kill.assert_called_once_with("orchestrator", "worker1-icli")

    @patch("orchestrator.terminal.interactive.tmux.kill_window")
    @patch("orchestrator.terminal.interactive.tmux.list_windows")
    def test_no_icli_windows(self, mock_list, mock_kill):
        from unittest.mock import MagicMock

        from orchestrator.terminal.manager import TmuxWindow

        mock_list.return_value = [
            TmuxWindow(index=0, name="worker1", active=True),
            TmuxWindow(index=1, name="worker2", active=False),
        ]

        mock_conn = MagicMock()
        restored = restore_icli_windows(mock_conn, "orchestrator")

        assert restored == 0
        mock_kill.assert_not_called()

    @patch("orchestrator.terminal.interactive.tmux.kill_window")
    @patch("orchestrator.terminal.interactive.tmux.list_windows")
    def test_skips_already_registered(self, mock_list, mock_kill):
        from unittest.mock import MagicMock

        from orchestrator.terminal.manager import TmuxWindow

        # Pre-register worker1
        _active_clis["sess-1"] = MagicMock(session_id="sess-1")

        mock_list.return_value = [
            TmuxWindow(index=1, name="worker1-icli", active=False),
        ]
        mock_conn = MagicMock()
        mock_session = MagicMock(id="sess-1", name="worker1")

        with patch(
            "orchestrator.state.repositories.sessions.get_session_by_name",
            return_value=mock_session,
        ):
            restored = restore_icli_windows(mock_conn, "orchestrator")

        assert restored == 0
        mock_kill.assert_not_called()


class TestRestoreRemoteICLIs:
    """Tests for _restore_remote_iclis."""

    @patch("orchestrator.terminal.remote_worker_server.get_remote_worker_server")
    def test_restores_pty_by_session_id(self, mock_get_rws):
        from unittest.mock import MagicMock

        mock_rws = MagicMock()
        mock_rws.list_ptys.return_value = [
            {
                "pty_id": "pty-abc",
                "alive": True,
                "session_id": "sess-1",
                "created_at": "2026-01-01T00:00:00",
            }
        ]
        mock_get_rws.return_value = mock_rws

        sessions = [MagicMock(id="sess-1", host="remote-host")]
        _restore_remote_iclis(sessions)

        cli = get_active_cli("sess-1")
        assert cli is not None
        assert cli.remote_pty_id == "pty-abc"
        assert cli.rws_host == "remote-host"
        assert cli.window_name == "rws-pty-abc"
        mock_rws.destroy_pty.assert_not_called()

    @patch("orchestrator.terminal.remote_worker_server.get_remote_worker_server")
    def test_destroys_orphaned_ptys(self, mock_get_rws):
        from unittest.mock import MagicMock

        mock_rws = MagicMock()
        mock_rws.list_ptys.return_value = [
            {
                "pty_id": "pty-abc",
                "alive": True,
                "session_id": "sess-1",
                "created_at": "2026-01-01T00:00:00",
            },
            {
                "pty_id": "pty-orphan",
                "alive": True,
                "session_id": None,
                "created_at": "2026-01-01T00:00:00",
            },
        ]
        mock_get_rws.return_value = mock_rws

        sessions = [MagicMock(id="sess-1", host="remote-host")]
        _restore_remote_iclis(sessions)

        assert get_active_cli("sess-1") is not None
        mock_rws.destroy_pty.assert_called_once_with("pty-orphan")

    @patch("orchestrator.terminal.remote_worker_server.get_remote_worker_server")
    def test_skips_already_registered(self, mock_get_rws):
        from unittest.mock import MagicMock

        _active_clis["sess-1"] = MagicMock(session_id="sess-1")

        mock_rws = MagicMock()
        mock_rws.list_ptys.return_value = [
            {
                "pty_id": "pty-abc",
                "alive": True,
                "session_id": "sess-1",
                "created_at": "2026-01-01T00:00:00",
            }
        ]
        mock_get_rws.return_value = mock_rws

        sessions = [MagicMock(id="sess-1", host="remote-host")]
        _restore_remote_iclis(sessions)

        # Should not overwrite existing entry
        assert _active_clis["sess-1"].session_id == "sess-1"
        mock_rws.destroy_pty.assert_not_called()

    @patch("orchestrator.terminal.remote_worker_server.get_remote_worker_server")
    def test_skips_dead_ptys(self, mock_get_rws):
        from unittest.mock import MagicMock

        mock_rws = MagicMock()
        mock_rws.list_ptys.return_value = [
            {
                "pty_id": "pty-dead",
                "alive": False,
                "session_id": "sess-1",
                "created_at": "2026-01-01T00:00:00",
            }
        ]
        mock_get_rws.return_value = mock_rws

        sessions = [MagicMock(id="sess-1", host="remote-host")]
        _restore_remote_iclis(sessions)

        assert get_active_cli("sess-1") is None

    @patch("orchestrator.terminal.remote_worker_server.get_remote_worker_server")
    def test_handles_daemon_not_ready(self, mock_get_rws):
        from unittest.mock import MagicMock

        mock_get_rws.side_effect = RuntimeError("not ready")

        sessions = [MagicMock(id="sess-1", host="remote-host")]
        _restore_remote_iclis(sessions)

        assert get_active_cli("sess-1") is None

    @patch("orchestrator.terminal.remote_worker_server.get_remote_worker_server")
    def test_groups_sessions_by_host(self, mock_get_rws):
        """Multiple sessions on the same host should only query daemon once."""
        from unittest.mock import MagicMock

        mock_rws = MagicMock()
        mock_rws.list_ptys.return_value = [
            {
                "pty_id": "pty-1",
                "alive": True,
                "session_id": "sess-1",
                "created_at": "2026-01-01T00:00:00",
            },
            {
                "pty_id": "pty-2",
                "alive": True,
                "session_id": "sess-2",
                "created_at": "2026-01-01T00:00:00",
            },
        ]
        mock_get_rws.return_value = mock_rws

        sessions = [
            MagicMock(id="sess-1", host="remote-host"),
            MagicMock(id="sess-2", host="remote-host"),
        ]
        _restore_remote_iclis(sessions)

        # Only one call to get_remote_worker_server for the shared host
        mock_get_rws.assert_called_once_with("remote-host")
        assert get_active_cli("sess-1") is not None
        assert get_active_cli("sess-2") is not None


class TestRecoverCLI:
    """Tests for recover_cli (single-session inline recovery)."""

    def test_already_registered(self):
        """If session is already in the registry, return it immediately."""
        from unittest.mock import MagicMock

        existing = MagicMock(session_id="sess-1")
        _active_clis["sess-1"] = existing
        mock_conn = MagicMock()

        result = recover_cli("sess-1", mock_conn)

        assert result is existing
        # Should not touch the DB at all
        mock_conn.execute.assert_not_called()

    def test_session_not_found(self):
        """If session doesn't exist in DB, return None."""
        from unittest.mock import MagicMock

        mock_conn = MagicMock()

        with patch(
            "orchestrator.state.repositories.sessions.get_session",
            return_value=None,
        ):
            result = recover_cli("sess-missing", mock_conn)

        assert result is None

    @patch("orchestrator.terminal.interactive.tmux.window_exists", return_value=True)
    def test_local_window_exists(self, mock_exists):
        """Local session with surviving tmux window → recovers and registers."""
        from unittest.mock import MagicMock

        mock_conn = MagicMock()
        mock_session = MagicMock(id="sess-1", host="localhost")
        mock_session.name = "worker1"

        with patch(
            "orchestrator.state.repositories.sessions.get_session",
            return_value=mock_session,
        ):
            result = recover_cli("sess-1", mock_conn)

        assert result is not None
        assert result.session_id == "sess-1"
        assert result.window_name == "worker1-icli"
        assert result.status == "active"
        assert result.remote_pty_id is None
        assert get_active_cli("sess-1") is result
        mock_exists.assert_called_once_with("orchestrator", "worker1-icli")

    @patch("orchestrator.terminal.interactive.tmux.window_exists", return_value=False)
    def test_local_window_gone(self, mock_exists):
        """Local session with dead tmux window → returns None."""
        from unittest.mock import MagicMock

        mock_conn = MagicMock()
        mock_session = MagicMock(id="sess-1", host="localhost")
        mock_session.name = "worker1"

        with patch(
            "orchestrator.state.repositories.sessions.get_session",
            return_value=mock_session,
        ):
            result = recover_cli("sess-1", mock_conn)

        assert result is None
        assert get_active_cli("sess-1") is None

    @patch("orchestrator.terminal.remote_worker_server.get_remote_worker_server")
    def test_remote_pty_alive(self, mock_get_rws):
        """Remote session with alive PTY on daemon → recovers and registers."""
        from unittest.mock import MagicMock

        mock_rws = MagicMock()
        mock_rws.list_ptys.return_value = [
            {
                "pty_id": "pty-abc",
                "alive": True,
                "session_id": "sess-1",
                "created_at": "2026-01-01T00:00:00",
            },
            {
                "pty_id": "pty-other",
                "alive": True,
                "session_id": "sess-other",
                "created_at": "2026-01-01T00:00:00",
            },
        ]
        mock_get_rws.return_value = mock_rws
        mock_conn = MagicMock()
        mock_session = MagicMock(id="sess-1", host="remote-host")
        mock_session.name = "worker1"

        with patch(
            "orchestrator.state.repositories.sessions.get_session",
            return_value=mock_session,
        ):
            result = recover_cli("sess-1", mock_conn)

        assert result is not None
        assert result.session_id == "sess-1"
        assert result.remote_pty_id == "pty-abc"
        assert result.rws_host == "remote-host"
        assert result.window_name == "rws-pty-abc"
        assert get_active_cli("sess-1") is result

    @patch("orchestrator.terminal.remote_worker_server.get_remote_worker_server")
    def test_remote_daemon_unavailable(self, mock_get_rws):
        """Remote session but daemon unreachable → returns None."""
        from unittest.mock import MagicMock

        mock_get_rws.side_effect = RuntimeError("connection refused")
        mock_conn = MagicMock()
        mock_session = MagicMock(id="sess-1", host="remote-host")
        mock_session.name = "worker1"

        with patch(
            "orchestrator.state.repositories.sessions.get_session",
            return_value=mock_session,
        ):
            result = recover_cli("sess-1", mock_conn)

        assert result is None
        assert get_active_cli("sess-1") is None
