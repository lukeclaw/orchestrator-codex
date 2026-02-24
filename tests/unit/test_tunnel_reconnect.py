"""Tests for tunnel-only reconnect functionality.

Updated for subprocess-based tunnel management via ReverseTunnelManager.
When SSH/screen/Claude are all running fine but the tunnel disconnects,
we should only reconnect the tunnel without typing into the Claude console.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestReconnectTunnelOnly:
    """Test the reconnect_tunnel_only helper function with tunnel_manager."""

    def test_tunnel_reconnect_success(self, db):
        """Should reconnect tunnel via tunnel_manager and return True on success."""
        from orchestrator.session.reconnect import reconnect_tunnel_only

        mock_session = MagicMock()
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.id = "test-session-id"

        mock_repo = MagicMock()
        mock_tm = MagicMock()
        mock_tm.restart_tunnel.return_value = 12345

        result = reconnect_tunnel_only(db, mock_session, "orchestrator", 8093, mock_repo, tunnel_manager=mock_tm)

        assert result is True
        mock_tm.restart_tunnel.assert_called_once_with("test-session-id", "test-worker", "subs-mt/test-vm")
        mock_repo.update_session.assert_called_once_with(db, "test-session-id", tunnel_pid=12345)

    def test_tunnel_reconnect_failure(self, db):
        """Should return False if tunnel_manager.restart_tunnel returns None."""
        from orchestrator.session.reconnect import reconnect_tunnel_only

        mock_session = MagicMock()
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.id = "test-session-id"

        mock_repo = MagicMock()
        mock_tm = MagicMock()
        mock_tm.restart_tunnel.return_value = None

        result = reconnect_tunnel_only(db, mock_session, "orchestrator", 8093, mock_repo, tunnel_manager=mock_tm)

        assert result is False
        mock_repo.update_session.assert_not_called()

    def test_tunnel_reconnect_no_manager(self, db):
        """Should return False if no tunnel_manager provided."""
        from orchestrator.session.reconnect import reconnect_tunnel_only

        mock_session = MagicMock()
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.id = "test-session-id"

        mock_repo = MagicMock()

        result = reconnect_tunnel_only(db, mock_session, "orchestrator", 8093, mock_repo, tunnel_manager=None)

        assert result is False

    def test_tunnel_reconnect_handles_exception(self, db):
        """Should return False if tunnel_manager raises an exception."""
        from orchestrator.session.reconnect import reconnect_tunnel_only

        mock_session = MagicMock()
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.id = "test-session-id"

        mock_repo = MagicMock()
        mock_tm = MagicMock()
        mock_tm.restart_tunnel.side_effect = OSError("SSH binary not found")

        result = reconnect_tunnel_only(db, mock_session, "orchestrator", 8093, mock_repo, tunnel_manager=mock_tm)

        assert result is False


class TestReconnectRemoteWorkerTunnelOnlyPath:
    """Test that reconnect_remote_worker takes the tunnel-only path when appropriate.

    Updated for the sequential pipeline design where Step 1 checks TUI + SSH
    non-intrusively and returns early if everything is alive.
    """

    @patch("orchestrator.terminal.manager.subprocess")
    @patch("orchestrator.session.reconnect.check_tui_running_in_pane", return_value=True)
    @patch("orchestrator.session.health.check_worker_ssh_alive", return_value=True)
    @patch("orchestrator.session.health.check_screen_and_claude_remote")
    @patch("orchestrator.session.reconnect.time.sleep")
    def test_tunnel_only_path_when_claude_running(
        self, mock_sleep, mock_screen_claude, mock_ssh_alive, mock_tui, mock_tmux_subprocess, db
    ):
        """When TUI active + SSH alive + remote alive, only fix tunnel and return."""
        from orchestrator.session.reconnect import reconnect_remote_worker

        mock_screen_claude.return_value = ("alive", "Screen session exists and Claude is running")

        mock_session = MagicMock()
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.id = "test-session-id"
        mock_session.work_dir = "/home/user/code"

        mock_repo = MagicMock()
        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = False  # Tunnel dead

        reconnect_remote_worker(db, mock_session, "orchestrator", "test-worker", 8093, "/tmp", mock_repo, tunnel_manager=mock_tm)

        # Step 1 detects TUI+SSH alive, subprocess confirms "alive" → _ensure_tunnel called
        mock_tm.restart_tunnel.assert_called_once()
        mock_repo.update_session.assert_any_call(db, "test-session-id", status="waiting")

    @patch("orchestrator.terminal.manager.subprocess")
    @patch("orchestrator.session.reconnect.check_tui_running_in_pane", return_value=False)
    @patch("orchestrator.session.reconnect.send_keys")
    @patch("orchestrator.session.reconnect.kill_window")
    @patch("orchestrator.terminal.ssh.remote_connect")
    @patch("orchestrator.terminal.ssh.wait_for_prompt", return_value=False)
    @patch("orchestrator.session.health.check_worker_ssh_alive", return_value=False)
    @patch("orchestrator.session.reconnect.time.sleep")
    def test_full_reconnect_when_ssh_dead(
        self, mock_sleep, mock_ssh_alive,
        mock_wait_prompt, mock_remote_connect, mock_kill_window, mock_send_keys,
        mock_tui, mock_tmux_subprocess, db
    ):
        """When SSH process is dead (no TUI), should go through full pipeline."""
        from orchestrator.session.reconnect import reconnect_remote_worker

        mock_session = MagicMock()
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.id = "test-session-id"
        mock_session.work_dir = "/home/user/code"

        mock_repo = MagicMock()
        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = False

        with pytest.raises(RuntimeError, match="Timed out waiting for shell prompt"):
            reconnect_remote_worker(db, mock_session, "orchestrator", "test-worker", 8093, "/tmp", mock_repo, tunnel_manager=mock_tm)


class TestHealthCheckAutoReconnectTunnel:
    """Test that health check auto-reconnects dead tunnels via tunnel_manager."""

    @patch("orchestrator.session.health.repo")
    @patch("orchestrator.session.health.check_screen_and_claude_remote")
    @patch("orchestrator.session.health.is_remote_host")
    @patch("orchestrator.api.routes.sessions.repo")
    def test_health_check_auto_reconnects_tunnel(
        self, mock_route_repo, mock_is_remote, mock_screen_claude, mock_health_repo, db
    ):
        """Health check should auto-reconnect tunnel when Claude running but tunnel dead."""
        from orchestrator.api.routes.sessions import health_check_session

        mock_is_remote.return_value = True
        mock_screen_claude.return_value = ("alive", "Screen session exists and Claude is running")

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"

        mock_session.status = "waiting"
        mock_route_repo.get_session.return_value = mock_session

        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = False
        mock_tm.restart_tunnel.return_value = 12345

        mock_request = MagicMock()
        mock_request.app.state.tunnel_manager = mock_tm

        result = health_check_session("test-session-id", mock_request, db)

        assert result["alive"] is True
        assert result["tunnel_reconnected"] is True
        mock_tm.restart_tunnel.assert_called_once_with("test-session-id", "test-worker", "subs-mt/test-vm")

    @patch("orchestrator.session.health.repo")
    @patch("orchestrator.session.health.check_screen_and_claude_remote")
    @patch("orchestrator.session.health.is_remote_host")
    @patch("orchestrator.api.routes.sessions.repo")
    def test_health_check_reports_failure_when_tunnel_reconnect_fails(
        self, mock_route_repo, mock_is_remote, mock_screen_claude, mock_health_repo, db
    ):
        """Health check should report failure if tunnel auto-reconnect fails."""
        from orchestrator.api.routes.sessions import health_check_session

        mock_is_remote.return_value = True
        mock_screen_claude.return_value = ("alive", "Screen session exists and Claude is running")

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"

        mock_session.status = "waiting"
        mock_route_repo.get_session.return_value = mock_session

        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = False
        mock_tm.restart_tunnel.return_value = None  # Restart fails

        mock_request = MagicMock()
        mock_request.app.state.tunnel_manager = mock_tm

        result = health_check_session("test-session-id", mock_request, db)

        assert result["alive"] is False
        assert result["needs_reconnect"] is True
        assert "restart failed" in result["reason"]
