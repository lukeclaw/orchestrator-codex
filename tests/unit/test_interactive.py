"""Unit tests for orchestrator/terminal/interactive.py."""

from unittest.mock import patch

import pytest

from orchestrator.terminal.interactive import (
    _active_clis,
    capture_interactive_cli,
    check_interactive_cli_alive,
    cleanup_orphaned_icli_windows,
    close_interactive_cli,
    get_active_cli,
    open_interactive_cli,
    open_interactive_cli_remote,
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


class TestOpenInteractiveCLIRemote:
    """Tests for open_interactive_cli_remote."""

    @patch("orchestrator.terminal.interactive.tmux.send_keys")
    @patch("orchestrator.terminal.interactive.ssh.wait_for_prompt", return_value=True)
    @patch("orchestrator.terminal.interactive.ssh.remote_connect")
    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_creates_window_and_connects(self, mock_create, mock_connect, mock_wait, mock_send):
        cli = open_interactive_cli_remote("orchestrator", "worker1", "sess-1", host="user/rdev-vm")

        mock_create.assert_called_once_with("orchestrator", "worker1-icli")
        mock_connect.assert_called_once_with("orchestrator", "worker1-icli", "user/rdev-vm")
        mock_wait.assert_called_once_with("orchestrator", "worker1-icli", timeout=30)
        assert cli.window_name == "worker1-icli"
        assert cli.status == "active"

    @patch("orchestrator.terminal.interactive.tmux.kill_window")
    @patch("orchestrator.terminal.interactive.ssh.wait_for_prompt", return_value=False)
    @patch("orchestrator.terminal.interactive.ssh.remote_connect")
    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_cleans_up_on_ssh_timeout(self, mock_create, mock_connect, mock_wait, mock_kill):
        with pytest.raises(RuntimeError, match="SSH to .* timed out"):
            open_interactive_cli_remote("orchestrator", "worker1", "sess-1", host="user/rdev-vm")

        mock_kill.assert_called_once_with("orchestrator", "worker1-icli")
        assert get_active_cli("sess-1") is None

    @patch("orchestrator.terminal.interactive.time.sleep")
    @patch("orchestrator.terminal.interactive.tmux.send_keys")
    @patch("orchestrator.terminal.interactive.ssh.wait_for_prompt", return_value=True)
    @patch("orchestrator.terminal.interactive.ssh.remote_connect")
    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_sends_command_and_cwd(
        self, mock_create, mock_connect, mock_wait, mock_send, mock_sleep
    ):
        cli = open_interactive_cli_remote(
            "orchestrator",
            "worker1",
            "sess-1",
            host="user/rdev-vm",
            command="npm login",
            cwd="/home/user/project",
        )

        # Should send cd first, then command
        calls = mock_send.call_args_list
        assert any("cd /home/user/project" in str(c) for c in calls), f"Expected cd call in {calls}"
        assert any("npm login" in str(c) for c in calls), f"Expected command call in {calls}"
        assert cli.initial_command == "npm login"


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


class TestCleanupOrphanedICLIWindows:
    """Tests for cleanup_orphaned_icli_windows."""

    @patch("orchestrator.terminal.interactive.tmux.kill_window")
    @patch("orchestrator.terminal.interactive.tmux.list_windows")
    def test_kills_icli_windows(self, mock_list, mock_kill):
        from orchestrator.terminal.manager import TmuxWindow

        mock_list.return_value = [
            TmuxWindow(index=0, name="worker1", active=True),
            TmuxWindow(index=1, name="worker1-icli", active=False),
            TmuxWindow(index=2, name="worker2", active=False),
            TmuxWindow(index=3, name="worker2-icli", active=False),
        ]

        killed = cleanup_orphaned_icli_windows("orchestrator")

        assert killed == 2
        mock_kill.assert_any_call("orchestrator", "worker1-icli")
        mock_kill.assert_any_call("orchestrator", "worker2-icli")

    @patch("orchestrator.terminal.interactive.tmux.kill_window")
    @patch("orchestrator.terminal.interactive.tmux.list_windows")
    def test_no_orphans(self, mock_list, mock_kill):
        from orchestrator.terminal.manager import TmuxWindow

        mock_list.return_value = [
            TmuxWindow(index=0, name="worker1", active=True),
            TmuxWindow(index=1, name="worker2", active=False),
        ]

        killed = cleanup_orphaned_icli_windows("orchestrator")

        assert killed == 0
        mock_kill.assert_not_called()
