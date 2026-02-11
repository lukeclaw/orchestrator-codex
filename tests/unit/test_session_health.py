"""Unit tests for session health check logic."""

import pytest
from unittest.mock import patch, MagicMock


class TestHealthCheckLocal:
    """Test health checks for local workers."""

    @patch('orchestrator.api.routes.sessions.check_claude_process_local')
    @patch('orchestrator.terminal.manager.window_exists')
    @patch('orchestrator.api.routes.sessions.repo')
    @patch('orchestrator.api.routes.sessions.is_rdev_host')
    def test_health_local_claude_running_returns_alive(
        self, mock_is_rdev, mock_repo, mock_window_exists, mock_check_claude, db
    ):
        """Local worker with Claude running should return alive=True."""
        from orchestrator.api.routes.sessions import health_check_session
        
        mock_is_rdev.return_value = False
        mock_window_exists.return_value = True
        mock_check_claude.return_value = (True, "Claude process found")
        
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "working"
        mock_session.tmux_window = "orchestrator:test-worker"
        mock_repo.get_session.return_value = mock_session
        
        result = health_check_session("test-session-id", db=db)
        
        assert result["alive"] == True
        assert result["status"] in ("working", "waiting", "idle")

    @patch('orchestrator.api.routes.sessions.check_claude_process_local')
    @patch('orchestrator.terminal.manager.window_exists')
    @patch('orchestrator.api.routes.sessions.repo')
    @patch('orchestrator.api.routes.sessions.is_rdev_host')
    def test_health_local_claude_dead_marks_disconnected(
        self, mock_is_rdev, mock_repo, mock_window_exists, mock_check_claude, db
    ):
        """Local worker without Claude process should be marked disconnected."""
        from orchestrator.api.routes.sessions import health_check_session
        
        mock_is_rdev.return_value = False
        mock_window_exists.return_value = True
        mock_check_claude.return_value = (False, "No Claude process")
        
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "working"
        mock_session.tmux_window = "orchestrator:test-worker"
        mock_repo.get_session.return_value = mock_session
        
        result = health_check_session("test-session-id", db=db)
        
        assert result["alive"] == False
        # Should update status to disconnected
        mock_repo.update_session.assert_called()

    @patch('orchestrator.terminal.manager.window_exists')
    @patch('orchestrator.api.routes.sessions.repo')
    @patch('orchestrator.api.routes.sessions.is_rdev_host')
    def test_health_local_tmux_window_missing_marks_disconnected(
        self, mock_is_rdev, mock_repo, mock_window_exists, db
    ):
        """Local worker without tmux window should be marked disconnected."""
        from orchestrator.api.routes.sessions import health_check_session
        
        mock_is_rdev.return_value = False
        mock_window_exists.return_value = False  # Window doesn't exist
        
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "working"
        mock_session.tmux_window = "orchestrator:test-worker"
        mock_repo.get_session.return_value = mock_session
        
        result = health_check_session("test-session-id", db=db)
        
        assert result["alive"] == False


class TestHealthCheckRdev:
    """Test health checks for rdev workers."""

    @patch('orchestrator.api.routes.sessions.check_tunnel_alive')
    @patch('orchestrator.api.routes.sessions.check_screen_and_claude_rdev')
    @patch('orchestrator.api.routes.sessions.repo')
    @patch('orchestrator.api.routes.sessions.is_rdev_host')
    def test_health_rdev_screen_alive_tunnel_alive_returns_working(
        self, mock_is_rdev, mock_repo, mock_screen_check, mock_tunnel_check, db
    ):
        """Rdev worker with screen and tunnel alive should return alive."""
        from orchestrator.api.routes.sessions import health_check_session
        
        mock_is_rdev.return_value = True
        mock_screen_check.return_value = ("alive", "Screen and Claude running")
        mock_tunnel_check.return_value = True
        
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.status = "working"
        mock_session.tmux_window = "orchestrator:test-rdev"
        mock_session.tunnel_pane = "orchestrator:test-rdev-tunnel"
        mock_repo.get_session.return_value = mock_session
        
        result = health_check_session("test-session-id", db=db)
        
        assert result["alive"] == True
        assert result["tunnel_alive"] == True

    @patch('orchestrator.api.routes.sessions.check_tunnel_alive')
    @patch('orchestrator.api.routes.sessions.check_screen_and_claude_rdev')
    @patch('orchestrator.api.routes.sessions.repo')
    @patch('orchestrator.api.routes.sessions.is_rdev_host')
    def test_health_rdev_screen_alive_tunnel_dead_marks_screen_detached(
        self, mock_is_rdev, mock_repo, mock_screen_check, mock_tunnel_check, db
    ):
        """Rdev with screen alive but tunnel dead should be screen_detached."""
        from orchestrator.api.routes.sessions import health_check_session
        
        mock_is_rdev.return_value = True
        mock_screen_check.return_value = ("alive", "Screen and Claude running")
        mock_tunnel_check.return_value = False  # Tunnel dead!
        
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.status = "working"
        mock_session.tmux_window = "orchestrator:test-rdev"
        mock_session.tunnel_pane = "orchestrator:test-rdev-tunnel"
        mock_repo.get_session.return_value = mock_session
        
        result = health_check_session("test-session-id", db=db)
        
        assert result["alive"] == False
        assert result["status"] == "screen_detached"
        assert result["needs_reconnect"] == True
        assert result["tunnel_alive"] == False

    @patch('orchestrator.api.routes.sessions.check_tunnel_alive')
    @patch('orchestrator.api.routes.sessions.check_screen_and_claude_rdev')
    @patch('orchestrator.api.routes.sessions.repo')
    @patch('orchestrator.api.routes.sessions.is_rdev_host')
    def test_health_rdev_screen_dead_marks_disconnected(
        self, mock_is_rdev, mock_repo, mock_screen_check, mock_tunnel_check, db
    ):
        """Rdev with screen dead should be marked disconnected."""
        from orchestrator.api.routes.sessions import health_check_session
        
        mock_is_rdev.return_value = True
        mock_screen_check.return_value = ("dead", "No Claude process")
        mock_tunnel_check.return_value = True
        
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.status = "working"
        mock_session.tmux_window = "orchestrator:test-rdev"
        mock_session.tunnel_pane = "orchestrator:test-rdev-tunnel"
        mock_repo.get_session.return_value = mock_session
        
        result = health_check_session("test-session-id", db=db)
        
        assert result["alive"] == False

    @patch('orchestrator.api.routes.sessions.check_screen_and_claude_rdev')
    @patch('orchestrator.api.routes.sessions.repo')
    @patch('orchestrator.api.routes.sessions.is_rdev_host')
    def test_health_rdev_ssh_connection_failed_marks_disconnected(
        self, mock_is_rdev, mock_repo, mock_screen_check, db
    ):
        """Rdev with SSH connection failure should be marked disconnected."""
        from orchestrator.api.routes.sessions import health_check_session
        
        mock_is_rdev.return_value = True
        mock_screen_check.return_value = ("unknown", "SSH connection failed")
        
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.status = "working"
        mock_session.tmux_window = "orchestrator:test-rdev"
        mock_session.tunnel_pane = "orchestrator:test-rdev-tunnel"
        mock_repo.get_session.return_value = mock_session
        
        result = health_check_session("test-session-id", db=db)
        
        assert result["alive"] == False


class TestHealthCheckAll:
    """Test batch health check functionality."""

    @patch('orchestrator.api.routes.sessions.repo')
    def test_health_all_skips_disconnected_sessions(self, mock_repo, db):
        """Health check all should skip already disconnected sessions."""
        from orchestrator.api.routes.sessions import health_check_all_sessions
        
        # One working, one disconnected
        mock_session1 = MagicMock()
        mock_session1.id = "session-1"
        mock_session1.name = "worker-1"
        mock_session1.host = "localhost"
        mock_session1.status = "working"
        mock_session1.tmux_window = "orchestrator:worker-1"
        
        mock_session2 = MagicMock()
        mock_session2.id = "session-2"
        mock_session2.name = "worker-2"
        mock_session2.host = "localhost"
        mock_session2.status = "disconnected"
        mock_session2.tmux_window = "orchestrator:worker-2"
        
        mock_repo.list_sessions.return_value = [mock_session1, mock_session2]
        mock_repo.get_session.side_effect = lambda db, sid: mock_session1 if sid == "session-1" else mock_session2
        
        with patch('orchestrator.api.routes.sessions.is_rdev_host', return_value=False):
            with patch('orchestrator.api.routes.sessions.check_claude_process_local', return_value=(True, "running")):
                with patch('orchestrator.terminal.manager.window_exists', return_value=True):
                    result = health_check_all_sessions(db=db)
        
        # Disconnected session should be skipped (not in alive list)
        # The working session should be checked
        assert "worker-2" not in result.get("alive", [])

    @patch('orchestrator.api.routes.sessions.repo')
    def test_health_all_handles_check_exception_gracefully(self, mock_repo, db):
        """Health check all should handle exceptions and continue."""
        from orchestrator.api.routes.sessions import health_check_all_sessions
        
        mock_session = MagicMock()
        mock_session.id = "session-1"
        mock_session.name = "worker-1"
        mock_session.host = "localhost"
        mock_session.status = "working"
        mock_session.tmux_window = "orchestrator:worker-1"
        
        mock_repo.list_sessions.return_value = [mock_session]
        mock_repo.get_session.return_value = mock_session
        
        # Make health check throw an exception by having is_rdev_host throw
        with patch('orchestrator.api.routes.sessions.is_rdev_host', side_effect=Exception("Check failed")):
            result = health_check_all_sessions(db=db)
        
        # Session should be assumed alive on error
        assert "worker-1" in result.get("alive", [])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
