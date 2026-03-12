"""Unit tests for screen session cleanup and PID-based reattach.

Tests _kill_orphaned_screen (session.py) uses anchored grep and iterates by PID,
and reconnect_remote_worker (reconnect.py) uses PID-based reattach to avoid
the "several suitable screens" ambiguity.
"""

from unittest.mock import MagicMock, patch

import pytest

# Import reconnect first to resolve circular import between
# orchestrator.terminal.session ↔ orchestrator.session.reconnect
import orchestrator.session.reconnect  # noqa: F401


class TestKillOrphanedScreen:
    """Tests for orchestrator.terminal.session._kill_orphaned_screen."""

    @patch("orchestrator.terminal.manager.send_keys")
    def test_sends_kill_command_that_iterates_by_pid(self, mock_send_keys):
        """Should send a command that lists matching PIDs and kills each individually."""
        from orchestrator.terminal.session import _kill_orphaned_screen

        with patch("orchestrator.terminal.session.time.sleep"):
            _kill_orphaned_screen("orch", "worker-1", "claude-abc123")

        mock_send_keys.assert_called_once()
        cmd = mock_send_keys.call_args[0][2]

        assert "screen -ls" in cmd
        assert "claude-abc123" in cmd
        assert "while read" in cmd
        assert "screen -X -S" in cmd
        assert "quit" in cmd

    @patch("orchestrator.terminal.manager.send_keys")
    def test_kills_by_full_session_id_not_bare_name(self, mock_send_keys):
        """The kill command must use the full $sid (pid.name) so it's unambiguous."""
        from orchestrator.terminal.session import _kill_orphaned_screen

        with patch("orchestrator.terminal.session.time.sleep"):
            _kill_orphaned_screen("orch", "w", "claude-xyz")

        cmd = mock_send_keys.call_args[0][2]
        assert "awk" in cmd
        assert "$1" in cmd

    @patch("orchestrator.terminal.manager.send_keys")
    def test_uses_anchored_grep(self, mock_send_keys):
        """grep must use -w to avoid matching session names that are superstrings."""
        from orchestrator.terminal.session import _kill_orphaned_screen

        with patch("orchestrator.terminal.session.time.sleep"):
            _kill_orphaned_screen("orch", "w", "claude-abc")

        cmd = mock_send_keys.call_args[0][2]
        assert "grep -w" in cmd


class TestReconnectStep6PidReattach:
    """Verify reconnect_remote_worker uses PID-based reattach in Step 6."""

    def _make_session(self, session_id="sess-1", name="worker-1", host="user/rdev"):
        s = MagicMock()
        s.id = session_id
        s.name = name
        s.host = host
        s.work_dir = "/home/user/project"
        s.claude_session_id = None
        s.status = "disconnected"
        s.rws_pty_id = None
        return s

    @patch("orchestrator.session.reconnect._launch_claude_in_screen")
    @patch("orchestrator.session.reconnect._kill_orphaned_screen")
    @patch(
        "orchestrator.session.reconnect.check_screen_exists_via_tmux",
        return_value=(True, True, "12345.claude-sess-1"),
    )
    @patch("orchestrator.terminal.session._install_screen_if_needed", return_value=True)
    @patch("orchestrator.session.reconnect._copy_configs_to_remote")
    @patch("orchestrator.session.reconnect._ensure_local_configs_exist")
    @patch("orchestrator.session.reconnect.check_tui_running_in_pane", return_value=False)
    @patch("orchestrator.session.health.check_worker_ssh_alive", return_value=True)
    @patch("orchestrator.session.reconnect.safe_send_keys")
    @patch("orchestrator.session.reconnect.send_keys")
    @patch("orchestrator.session.reconnect.time.sleep")
    @patch("orchestrator.session.reconnect.os.makedirs")
    @patch("orchestrator.terminal.manager.ensure_window")
    @patch("orchestrator.session.reconnect.ensure_rdev_node")
    @patch(
        "orchestrator.session.health.check_screen_and_claude_remote",
        return_value=("alive", "mocked"),
    )
    def test_screen_exists_claude_running_reattaches_by_pid(
        self,
        mock_screen_claude,
        mock_node,
        mock_ensure_win,
        mock_makedirs,
        mock_sleep,
        mock_send_keys,
        mock_safe_send,
        mock_ssh_alive,
        mock_tui,
        mock_ensure_configs,
        mock_copy_configs,
        mock_install_screen,
        mock_check_screen,
        mock_kill_orphaned,
        mock_launch,
    ):
        """When screen+Claude exist, should reattach using pid.name, not bare name."""
        from orchestrator.session.reconnect import reconnect_remote_worker

        session = self._make_session()
        conn = MagicMock()
        repo = MagicMock()

        reconnect_remote_worker(
            conn,
            session,
            "orch",
            "worker-1",
            8093,
            "/tmp/test",
            repo,
            tunnel_manager=MagicMock(is_alive=MagicMock(return_value=True)),
        )

        # Should reattach with the full pid.name
        mock_safe_send.assert_called_once()
        reattach_cmd = mock_safe_send.call_args[0][2]
        assert "screen -rd 12345.claude-sess-1" == reattach_cmd

        # Should NOT call _kill_orphaned_screen (only used in "no screen" branch)
        mock_kill_orphaned.assert_not_called()

    @patch("orchestrator.session.reconnect._launch_claude_in_screen")
    @patch("orchestrator.session.reconnect._kill_orphaned_screen")
    @patch(
        "orchestrator.session.reconnect.check_screen_exists_via_tmux",
        return_value=(True, True, None),
    )
    @patch("orchestrator.terminal.session._install_screen_if_needed", return_value=True)
    @patch("orchestrator.session.reconnect._copy_configs_to_remote")
    @patch("orchestrator.session.reconnect._ensure_local_configs_exist")
    @patch("orchestrator.session.reconnect.check_tui_running_in_pane", return_value=False)
    @patch("orchestrator.session.health.check_worker_ssh_alive", return_value=True)
    @patch("orchestrator.session.reconnect.safe_send_keys")
    @patch("orchestrator.session.reconnect.send_keys")
    @patch("orchestrator.session.reconnect.time.sleep")
    @patch("orchestrator.session.reconnect.os.makedirs")
    @patch("orchestrator.terminal.manager.ensure_window")
    @patch("orchestrator.session.reconnect.ensure_rdev_node")
    @patch(
        "orchestrator.session.health.check_screen_and_claude_remote",
        return_value=("alive", "mocked"),
    )
    def test_screen_exists_pid_none_falls_back_to_name(
        self,
        mock_screen_claude,
        mock_node,
        mock_ensure_win,
        mock_makedirs,
        mock_sleep,
        mock_send_keys,
        mock_safe_send,
        mock_ssh_alive,
        mock_tui,
        mock_ensure_configs,
        mock_copy_configs,
        mock_install_screen,
        mock_check_screen,
        mock_kill_orphaned,
        mock_launch,
    ):
        """When screen exists but PID wasn't captured, fall back to bare name."""
        from orchestrator.session.reconnect import reconnect_remote_worker

        session = self._make_session()
        conn = MagicMock()
        repo = MagicMock()

        reconnect_remote_worker(
            conn,
            session,
            "orch",
            "worker-1",
            8093,
            "/tmp/test",
            repo,
            tunnel_manager=MagicMock(is_alive=MagicMock(return_value=True)),
        )

        mock_safe_send.assert_called_once()
        reattach_cmd = mock_safe_send.call_args[0][2]
        assert reattach_cmd == f"screen -rd claude-{session.id}"

    @patch("orchestrator.session.reconnect._launch_claude_in_screen")
    @patch("orchestrator.session.reconnect._kill_orphaned_screen")
    @patch(
        "orchestrator.session.reconnect.check_screen_exists_via_tmux",
        return_value=(False, False, None),
    )
    @patch("orchestrator.terminal.session._install_screen_if_needed", return_value=True)
    @patch("orchestrator.session.reconnect._copy_configs_to_remote")
    @patch("orchestrator.session.reconnect._ensure_local_configs_exist")
    @patch("orchestrator.session.reconnect.check_tui_running_in_pane", return_value=False)
    @patch("orchestrator.session.health.check_worker_ssh_alive", return_value=True)
    @patch("orchestrator.session.reconnect.safe_send_keys")
    @patch("orchestrator.session.reconnect.send_keys")
    @patch("orchestrator.session.reconnect.time.sleep")
    @patch("orchestrator.session.reconnect.os.makedirs")
    @patch("orchestrator.terminal.manager.ensure_window")
    @patch("orchestrator.session.reconnect.ensure_rdev_node")
    @patch(
        "orchestrator.session.health.check_screen_and_claude_remote",
        return_value=("alive", "mocked"),
    )
    def test_no_screen_kills_orphans_before_creating(
        self,
        mock_screen_claude,
        mock_node,
        mock_ensure_win,
        mock_makedirs,
        mock_sleep,
        mock_send_keys,
        mock_safe_send,
        mock_ssh_alive,
        mock_tui,
        mock_ensure_configs,
        mock_copy_configs,
        mock_install_screen,
        mock_check_screen,
        mock_kill_orphaned,
        mock_launch,
    ):
        """When no screen found, should kill orphans before screen -S."""
        from orchestrator.session.reconnect import reconnect_remote_worker

        session = self._make_session()
        conn = MagicMock()
        repo = MagicMock()

        reconnect_remote_worker(
            conn,
            session,
            "orch",
            "worker-1",
            8093,
            "/tmp/test",
            repo,
            tunnel_manager=MagicMock(is_alive=MagicMock(return_value=True)),
        )

        # _kill_orphaned_screen should be called to clean residuals
        mock_kill_orphaned.assert_called_once_with(
            "orch",
            "worker-1",
            f"claude-{session.id}",
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
