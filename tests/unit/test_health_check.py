"""Unit tests for health check logic with tunnel verification."""

import pytest
from unittest.mock import patch, MagicMock


class TestHealthCheckLogic:
    """Test the health check logic for rdev workers."""

    @patch('orchestrator.api.routes.sessions.check_tunnel_alive')
    @patch('orchestrator.api.routes.sessions.check_screen_and_claude_rdev')
    @patch('orchestrator.api.routes.sessions.repo')
    @patch('orchestrator.api.routes.sessions.is_rdev_host')
    def test_tunnel_dead_but_claude_alive_returns_needs_reconnect(
        self,
        mock_is_rdev,
        mock_repo,
        mock_screen_check,
        mock_tunnel_check,
    ):
        """When tunnel is dead but Claude is running, should return needs_reconnect=True."""
        from orchestrator.api.routes.sessions import health_check_session
        
        # Setup mocks
        mock_is_rdev.return_value = True
        mock_screen_check.return_value = ("alive", "Screen session exists and Claude is running")
        mock_tunnel_check.return_value = False  # Tunnel is dead!
        
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "user/rdev-test"
        mock_session.status = "working"
        mock_session.tmux_window = "orchestrator:test-worker"
        mock_session.tunnel_pane = "orchestrator:test-worker-tunnel"
        mock_repo.get_session.return_value = mock_session
        
        mock_db = MagicMock()
        
        # Call the function
        result = health_check_session("test-session-id", db=mock_db)
        
        # Assertions - should indicate worker needs reconnect due to dead tunnel
        assert result["alive"] == False, f"Expected alive=False when tunnel dead, got {result}"
        assert result["status"] == "screen_detached", f"Expected status=screen_detached, got {result['status']}"
        assert result["needs_reconnect"] == True, f"Expected needs_reconnect=True, got {result}"
        assert result["tunnel_alive"] == False, f"Expected tunnel_alive=False, got {result}"
        assert "tunnel" in result["reason"].lower(), f"Reason should mention tunnel: {result['reason']}"

    @patch('orchestrator.api.routes.sessions.check_tunnel_alive')
    @patch('orchestrator.api.routes.sessions.check_screen_and_claude_rdev')
    @patch('orchestrator.api.routes.sessions.repo')
    @patch('orchestrator.api.routes.sessions.is_rdev_host')
    def test_tunnel_alive_and_claude_alive_returns_healthy(
        self,
        mock_is_rdev,
        mock_repo,
        mock_screen_check,
        mock_tunnel_check,
    ):
        """When both tunnel and Claude are alive, should return alive=True."""
        from orchestrator.api.routes.sessions import health_check_session
        
        # Setup mocks
        mock_is_rdev.return_value = True
        mock_screen_check.return_value = ("alive", "Screen session exists and Claude is running")
        mock_tunnel_check.return_value = True  # Tunnel is alive!
        
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "user/rdev-test"
        mock_session.status = "working"
        mock_session.tmux_window = "orchestrator:test-worker"
        mock_session.tunnel_pane = "orchestrator:test-worker-tunnel"
        mock_repo.get_session.return_value = mock_session
        
        mock_db = MagicMock()
        
        # Call the function
        result = health_check_session("test-session-id", db=mock_db)
        
        # Assertions - should indicate worker is healthy
        assert result["alive"] == True, f"Expected alive=True when all healthy, got {result}"
        assert result["tunnel_alive"] == True, f"Expected tunnel_alive=True, got {result}"
        assert result.get("needs_reconnect") is None or result.get("needs_reconnect") == False

    @patch('orchestrator.api.routes.sessions.check_tunnel_alive')
    @patch('orchestrator.api.routes.sessions.check_screen_and_claude_rdev')
    @patch('orchestrator.api.routes.sessions.repo')
    @patch('orchestrator.api.routes.sessions.is_rdev_host')
    def test_recovery_from_screen_detached_when_all_healthy(
        self,
        mock_is_rdev,
        mock_repo,
        mock_screen_check,
        mock_tunnel_check,
    ):
        """When status was screen_detached but now all healthy, should update to waiting."""
        from orchestrator.api.routes.sessions import health_check_session
        
        # Setup mocks
        mock_is_rdev.return_value = True
        mock_screen_check.return_value = ("alive", "Screen session exists and Claude is running")
        mock_tunnel_check.return_value = True  # Tunnel is alive!
        
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "user/rdev-test"
        mock_session.status = "screen_detached"  # Was detached
        mock_session.tmux_window = "orchestrator:test-worker"
        mock_session.tunnel_pane = "orchestrator:test-worker-tunnel"
        mock_repo.get_session.return_value = mock_session
        
        mock_db = MagicMock()
        
        # Call the function
        result = health_check_session("test-session-id", db=mock_db)
        
        # Assertions - should recover to waiting
        assert result["alive"] == True
        assert result["status"] == "waiting", f"Expected status=waiting after recovery, got {result['status']}"
        
        # Should have called update_session to change status
        mock_repo.update_session.assert_called_once()
        call_args = mock_repo.update_session.call_args
        assert call_args[1]["status"] == "waiting" or call_args[0][2] == "waiting"


class TestTunnelAliveCheck:
    """Test the _check_tunnel_alive function."""

    @patch('orchestrator.session.health.capture_output')
    def test_tunnel_dead_shows_shell_prompt(self, mock_capture):
        """When tunnel window shows shell prompt, tunnel is dead."""
        from orchestrator.session.health import check_tunnel_alive
        
        # Shell prompt indicates tunnel exited
        mock_capture.return_value = "yuqiu@macbook ~ % "
        
        result = check_tunnel_alive("orchestrator", "test-tunnel")
        assert result == False, "Shell prompt should indicate dead tunnel"

    @patch('orchestrator.session.health.capture_output')
    def test_tunnel_dead_shows_connection_closed(self, mock_capture):
        """When tunnel shows connection closed error, tunnel is dead."""
        from orchestrator.session.health import check_tunnel_alive
        
        mock_capture.return_value = "Connection closed by remote host.\nyuqiu@macbook ~ % "
        
        result = check_tunnel_alive("orchestrator", "test-tunnel")
        assert result == False, "Connection closed should indicate dead tunnel"

    @patch('orchestrator.session.health.capture_output')
    def test_tunnel_alive_shows_ssh_command(self, mock_capture):
        """When tunnel shows SSH command running, tunnel is alive."""
        from orchestrator.session.health import check_tunnel_alive
        
        # SSH tunnel command visible, no prompt
        mock_capture.return_value = "ssh -L 8093:localhost:8093 user@rdev-host"
        
        result = check_tunnel_alive("orchestrator", "test-tunnel")
        assert result == True, "SSH command without prompt should indicate alive tunnel"

    @patch('orchestrator.session.health.capture_output')
    def test_tunnel_dead_no_output(self, mock_capture):
        """When tunnel window has no output, assume dead."""
        from orchestrator.session.health import check_tunnel_alive
        
        mock_capture.return_value = ""
        
        result = check_tunnel_alive("orchestrator", "test-tunnel")
        assert result == False, "Empty output should indicate dead tunnel"

    @patch('orchestrator.session.health.capture_output')
    def test_tunnel_dead_dollar_prompt(self, mock_capture):
        """When tunnel shows $ prompt, tunnel is dead."""
        from orchestrator.session.health import check_tunnel_alive
        
        mock_capture.return_value = "some previous output\n$ "
        
        result = check_tunnel_alive("orchestrator", "test-tunnel")
        assert result == False, "$ prompt should indicate dead tunnel"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
