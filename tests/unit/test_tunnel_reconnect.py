"""Tests for tunnel-only reconnect functionality.

When SSH/screen/Claude are all running fine but the tunnel disconnects,
we should only reconnect the tunnel without typing into the Claude console.
"""

import pytest
from unittest.mock import patch, MagicMock


class TestReconnectTunnelOnly:
    """Test the reconnect_tunnel_only helper function."""

    @patch("orchestrator.session.health.kill_tunnel_processes", return_value=0)
    @patch("orchestrator.session.reconnect.check_tunnel_alive")
    @patch("orchestrator.session.reconnect.kill_window")
    @patch("orchestrator.terminal.manager.create_window")
    @patch("orchestrator.terminal.ssh.setup_rdev_tunnel")
    @patch("orchestrator.session.reconnect.time.sleep")
    def test_tunnel_reconnect_success(
        self, mock_sleep, mock_setup_tunnel, mock_create_window, mock_kill_window, mock_check_tunnel, mock_kill_procs, db
    ):
        """Should reconnect tunnel and return True on success."""
        from orchestrator.session.reconnect import reconnect_tunnel_only

        mock_check_tunnel.return_value = True  # New tunnel is alive

        mock_session = MagicMock()
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.tunnel_pane = "orchestrator:test-worker-tunnel"
        mock_session.id = "test-session-id"

        mock_repo = MagicMock()

        result = reconnect_tunnel_only(db, mock_session, "orchestrator", 8093, mock_repo)

        assert result is True
        mock_kill_procs.assert_called_once_with("subs-mt/test-vm")
        mock_kill_window.assert_called_once_with("orchestrator", "test-worker-tunnel")
        mock_create_window.assert_called_once_with("orchestrator", "test-worker-tunnel")
        mock_setup_tunnel.assert_called_once_with(
            "orchestrator", "test-worker-tunnel", "subs-mt/test-vm", 8093, 8093
        )
        mock_repo.update_session.assert_called_once()

    @patch("orchestrator.session.health.kill_tunnel_processes", return_value=0)
    @patch("orchestrator.session.reconnect.check_tunnel_alive")
    @patch("orchestrator.session.reconnect.kill_window")
    @patch("orchestrator.terminal.manager.create_window")
    @patch("orchestrator.terminal.ssh.setup_rdev_tunnel")
    @patch("orchestrator.session.reconnect.time.sleep")
    def test_tunnel_reconnect_failure(
        self, mock_sleep, mock_setup_tunnel, mock_create_window, mock_kill_window, mock_check_tunnel, mock_kill_procs, db
    ):
        """Should return False if tunnel verification fails."""
        from orchestrator.session.reconnect import reconnect_tunnel_only

        mock_check_tunnel.return_value = False  # New tunnel is not alive

        mock_session = MagicMock()
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.tunnel_pane = None  # No existing tunnel
        mock_session.id = "test-session-id"

        mock_repo = MagicMock()

        result = reconnect_tunnel_only(db, mock_session, "orchestrator", 8093, mock_repo)

        assert result is False
        mock_repo.update_session.assert_not_called()

    @patch("orchestrator.session.health.kill_tunnel_processes", return_value=0)
    @patch("orchestrator.session.reconnect.check_tunnel_alive")
    @patch("orchestrator.session.reconnect.kill_window")
    @patch("orchestrator.terminal.manager.create_window")
    @patch("orchestrator.terminal.ssh.setup_rdev_tunnel")
    @patch("orchestrator.session.reconnect.time.sleep")
    def test_tunnel_reconnect_handles_kill_error(
        self, mock_sleep, mock_setup_tunnel, mock_create_window, mock_kill_window, mock_check_tunnel, mock_kill_procs, db
    ):
        """Should continue even if killing old tunnel fails."""
        from orchestrator.session.reconnect import reconnect_tunnel_only

        mock_kill_window.side_effect = Exception("Window not found")
        mock_check_tunnel.return_value = True

        mock_session = MagicMock()
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.tunnel_pane = "orchestrator:old-tunnel"
        mock_session.id = "test-session-id"

        mock_repo = MagicMock()

        # Should not raise, should continue with creating new tunnel
        result = reconnect_tunnel_only(db, mock_session, "orchestrator", 8093, mock_repo)

        assert result is True
        mock_create_window.assert_called_once()


class TestReconnectRdevWorkerTunnelOnlyPath:
    """Test that reconnect_rdev_worker takes the tunnel-only path when appropriate."""

    @patch("orchestrator.session.reconnect.check_tunnel_alive")
    @patch("orchestrator.session.health.check_screen_and_claude_rdev")
    @patch("orchestrator.session.health.check_worker_ssh_alive")
    @patch("orchestrator.session.reconnect.reconnect_tunnel_only")
    @patch("orchestrator.session.reconnect.time.sleep")
    def test_tunnel_only_path_when_claude_running(
        self, mock_sleep, mock_tunnel_only, mock_ssh_alive, mock_screen_claude, mock_tunnel_alive, db
    ):
        """When tunnel dead but SSH/screen/Claude alive, only reconnect tunnel."""
        from orchestrator.session.reconnect import reconnect_rdev_worker
        
        # Tunnel dead, but everything else running
        mock_tunnel_alive.return_value = False
        mock_screen_claude.return_value = ("alive", "Screen session exists and Claude is running")
        mock_ssh_alive.return_value = True
        mock_tunnel_only.return_value = True
        
        mock_session = MagicMock()
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.tunnel_pane = "orchestrator:test-worker-tunnel"
        mock_session.id = "test-session-id"
        mock_session.work_dir = "/home/user/code"
        
        mock_repo = MagicMock()
        
        # Should return early after tunnel-only reconnect
        reconnect_rdev_worker(db, mock_session, "orchestrator", "test-worker", 8093, "/tmp", mock_repo)
        
        # Should have called tunnel-only reconnect
        mock_tunnel_only.assert_called_once()
        # Should have updated status to waiting
        mock_repo.update_session.assert_called_with(db, mock_session.id, status="waiting")

    @patch("orchestrator.session.health.kill_tunnel_processes", return_value=0)
    @patch("orchestrator.session.reconnect.kill_window")
    @patch("orchestrator.session.reconnect.send_keys")
    @patch("orchestrator.session.reconnect.capture_output", return_value="")
    @patch("orchestrator.terminal.manager.create_window")
    @patch("orchestrator.terminal.ssh.setup_rdev_tunnel")
    @patch("orchestrator.terminal.ssh.rdev_connect")
    @patch("orchestrator.terminal.ssh.wait_for_prompt", return_value=False)
    @patch("orchestrator.session.reconnect.check_tunnel_alive")
    @patch("orchestrator.session.health.check_screen_and_claude_rdev")
    @patch("orchestrator.session.health.check_worker_ssh_alive")
    @patch("orchestrator.session.reconnect.reconnect_tunnel_only")
    @patch("orchestrator.session.reconnect.time.sleep")
    def test_full_reconnect_when_ssh_dead(
        self, mock_sleep, mock_tunnel_only, mock_ssh_alive, mock_screen_claude,
        mock_tunnel_alive, mock_wait_prompt, mock_rdev_connect, mock_setup_tunnel,
        mock_create_window, mock_capture, mock_send_keys, mock_kill_window, mock_kill_procs, db
    ):
        """When SSH process is dead, should NOT take tunnel-only path."""
        from orchestrator.session.reconnect import reconnect_rdev_worker

        # Tunnel dead AND SSH process dead
        mock_tunnel_alive.return_value = False
        mock_screen_claude.return_value = ("dead", "No screen session found")
        mock_ssh_alive.return_value = False  # SSH process not running

        mock_session = MagicMock()
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.tunnel_pane = "orchestrator:test-worker-tunnel"
        mock_session.id = "test-session-id"
        mock_session.work_dir = "/home/user/code"

        mock_repo = MagicMock()

        # Full reconnect will fail at SSH prompt wait (mocked to return False),
        # raising RuntimeError. That's expected — we only care that it didn't
        # take the tunnel-only path.
        with pytest.raises(RuntimeError, match="Timed out waiting for shell prompt"):
            reconnect_rdev_worker(db, mock_session, "orchestrator", "test-worker", 8093, "/tmp", mock_repo)

        # Should NOT have called tunnel-only reconnect (SSH is dead)
        mock_tunnel_only.assert_not_called()


class TestHealthCheckAutoReconnectTunnel:
    """Test that health check auto-reconnects dead tunnels."""

    @patch("orchestrator.session.reconnect.reconnect_tunnel_only")
    @patch("orchestrator.api.routes.sessions.repo")
    @patch("orchestrator.api.routes.sessions.check_screen_and_claude_rdev")
    @patch("orchestrator.api.routes.sessions.check_tunnel_alive")
    @patch("orchestrator.api.routes.sessions.is_rdev_host")
    def test_health_check_auto_reconnects_tunnel(
        self, mock_is_rdev, mock_tunnel_alive, mock_screen_claude, mock_repo, mock_tunnel_only, db
    ):
        """Health check should auto-reconnect tunnel when Claude is running but tunnel dead."""
        from orchestrator.api.routes.sessions import health_check_session
        
        mock_is_rdev.return_value = True
        mock_screen_claude.return_value = ("alive", "Screen session exists and Claude is running")
        mock_tunnel_alive.return_value = False  # Tunnel dead
        mock_tunnel_only.return_value = True  # Auto-reconnect succeeds
        
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.tmux_window = "orchestrator:test-worker"
        mock_session.tunnel_pane = "orchestrator:test-worker-tunnel"
        mock_session.status = "waiting"
        mock_repo.get_session.return_value = mock_session
        
        result = health_check_session("test-session-id", db)
        
        assert result["alive"] is True
        assert result["tunnel_reconnected"] is True
        mock_tunnel_only.assert_called_once()

    @patch("orchestrator.session.reconnect.reconnect_tunnel_only")
    @patch("orchestrator.api.routes.sessions.repo")
    @patch("orchestrator.api.routes.sessions.check_screen_and_claude_rdev")
    @patch("orchestrator.api.routes.sessions.check_tunnel_alive")
    @patch("orchestrator.api.routes.sessions.is_rdev_host")
    def test_health_check_reports_failure_when_tunnel_reconnect_fails(
        self, mock_is_rdev, mock_tunnel_alive, mock_screen_claude, mock_repo, mock_tunnel_only, db
    ):
        """Health check should report failure if tunnel auto-reconnect fails."""
        from orchestrator.api.routes.sessions import health_check_session
        
        mock_is_rdev.return_value = True
        mock_screen_claude.return_value = ("alive", "Screen session exists and Claude is running")
        mock_tunnel_alive.return_value = False  # Tunnel dead
        mock_tunnel_only.return_value = False  # Auto-reconnect fails
        
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.tmux_window = "orchestrator:test-worker"
        mock_session.tunnel_pane = "orchestrator:test-worker-tunnel"
        mock_session.status = "waiting"
        mock_repo.get_session.return_value = mock_session
        
        result = health_check_session("test-session-id", db)
        
        assert result["alive"] is False
        assert result["needs_reconnect"] is True
        assert "auto-reconnect failed" in result["reason"]
