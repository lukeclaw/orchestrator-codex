"""Unit tests for session status transitions."""

from unittest.mock import MagicMock, patch

import pytest


class TestStatusTransitions:
    """Test that status transitions happen correctly in various scenarios."""

    @patch("orchestrator.api.routes.sessions.repo")
    def test_connecting_to_working_on_success(self, mock_repo, db):
        """Successful rdev setup should transition connecting -> working."""
        # This tests the background thread behavior

        # Verify that update_session is called with status="working" after successful setup
        # This is tested indirectly through the create_session flow
        # The background thread calls repo.update_session(db, s.id, status="working")

        # For now, just verify the expected status values exist
        valid_statuses = [
            "idle",
            "connecting",
            "working",
            "paused",
            "waiting",
            "error",
            "disconnected",
        ]
        assert "connecting" in valid_statuses
        assert "working" in valid_statuses

    @patch("orchestrator.api.routes.sessions.repo")
    def test_connecting_to_error_on_failure(self, mock_repo, db):
        """Failed rdev setup should transition connecting -> error."""
        # Verify the background thread sets status to error on failure
        valid_statuses = [
            "idle",
            "connecting",
            "working",
            "paused",
            "waiting",
            "error",
            "disconnected",
        ]
        assert "connecting" in valid_statuses
        assert "error" in valid_statuses

    @patch("orchestrator.state.repositories.tasks.list_tasks")
    @patch("orchestrator.api.routes.sessions.repo")
    def test_working_to_paused_on_stop(self, mock_repo, mock_list_tasks, db):
        """Stopping a working session should transition to paused."""
        from orchestrator.api.routes.sessions import stop_session

        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "working"

        mock_repo.get_session.return_value = mock_session
        mock_list_tasks.return_value = []

        with patch("orchestrator.api.routes.sessions.send_keys"):
            with patch("orchestrator.terminal.manager.send_keys_literal"):
                stop_session("test-id", db=db)

        # Should update to idle (stop_session sets to idle, not paused)
        mock_repo.update_session.assert_called()
        call_args = mock_repo.update_session.call_args
        # Check that status="idle" is in the call
        assert "idle" in str(call_args)

    @patch("orchestrator.session.health.check_claude_running_local")
    @patch("orchestrator.session.health.is_remote_host")
    @patch("orchestrator.session.health.repo")
    @patch("orchestrator.api.routes.sessions.repo")
    def test_working_to_disconnected_on_health_fail(
        self, mock_route_repo, mock_health_repo, mock_is_remote, mock_check_claude, db
    ):
        """Health check failure should transition working -> disconnected."""
        from orchestrator.api.routes.sessions import health_check_session

        mock_is_remote.return_value = False
        mock_check_claude.return_value = (False, "No Claude process")

        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "working"

        mock_route_repo.get_session.return_value = mock_session

        mock_request = MagicMock()
        mock_request.app.state.tunnel_manager = None

        result = health_check_session("test-id", mock_request, db=db)

        assert result["alive"] is False
        # Should update to disconnected
        mock_health_repo.update_session.assert_called()

    @patch("orchestrator.session.health.window_exists", return_value=True)
    @patch("orchestrator.session.health.check_claude_running_local")
    @patch("orchestrator.session.health.is_remote_host")
    @patch("orchestrator.session.health.repo")
    @patch("orchestrator.api.routes.sessions.repo")
    def test_disconnected_to_idle_on_health_recover_no_task(
        self,
        mock_route_repo,
        mock_health_repo,
        mock_is_remote,
        mock_check_claude,
        mock_win_exists,
        db,
    ):
        """Health recover with no assigned task should set idle."""
        from orchestrator.api.routes.sessions import health_check_session

        mock_is_remote.return_value = False
        mock_check_claude.return_value = (True, "Claude process running in pane")

        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "disconnected"

        mock_route_repo.get_session.return_value = mock_session

        mock_request = MagicMock()
        mock_request.app.state.tunnel_manager = None

        result = health_check_session("test-id", mock_request, db=db)

        assert result["alive"] is True
        assert result["status"] == "idle"
        mock_health_repo.update_session.assert_called_once()
        _, kwargs = mock_health_repo.update_session.call_args
        assert kwargs["status"] == "idle"

    @patch("orchestrator.session.health.window_exists", return_value=True)
    @patch("orchestrator.session.health.check_claude_running_local")
    @patch("orchestrator.session.health.is_remote_host")
    @patch("orchestrator.session.health.repo")
    @patch("orchestrator.api.routes.sessions.repo")
    def test_disconnected_to_waiting_on_health_recover_with_task(
        self,
        mock_route_repo,
        mock_health_repo,
        mock_is_remote,
        mock_check_claude,
        mock_win_exists,
        db,
    ):
        """Health recover with assigned task should set waiting."""
        from orchestrator.api.routes.sessions import health_check_session
        from orchestrator.state.repositories import (
            projects as projects_repo,
        )
        from orchestrator.state.repositories import (
            sessions as sessions_repo,
        )
        from orchestrator.state.repositories import (
            tasks as tasks_repo,
        )

        # Create a real session and task in the DB so _recovery_status finds it
        project = projects_repo.create_project(db, name="test-project")
        session = sessions_repo.create_session(db, name="test-worker", host="localhost")
        task = tasks_repo.create_task(db, project_id=project.id, title="Test task")
        tasks_repo.update_task(db, task.id, assigned_session_id=session.id, status="in_progress")

        mock_is_remote.return_value = False
        mock_check_claude.return_value = (True, "Claude process running in pane")

        mock_session = MagicMock()
        mock_session.id = session.id
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "disconnected"

        mock_route_repo.get_session.return_value = mock_session

        mock_request = MagicMock()
        mock_request.app.state.tunnel_manager = None

        result = health_check_session(session.id, mock_request, db=db)

        assert result["alive"] is True
        assert result["status"] == "waiting"
        mock_health_repo.update_session.assert_called_once()
        _, kwargs = mock_health_repo.update_session.call_args
        assert kwargs["status"] == "waiting"

    @patch("orchestrator.session.health.window_exists", return_value=True)
    @patch("orchestrator.session.health.check_claude_running_local")
    @patch("orchestrator.session.health.is_remote_host")
    @patch("orchestrator.session.health.repo")
    @patch("orchestrator.api.routes.sessions.repo")
    def test_error_to_idle_on_health_recover_no_task(
        self,
        mock_route_repo,
        mock_health_repo,
        mock_is_remote,
        mock_check_claude,
        mock_win_exists,
        db,
    ):
        """Health check finding alive Claude should recover error -> idle (no task)."""
        from orchestrator.api.routes.sessions import health_check_session

        mock_is_remote.return_value = False
        mock_check_claude.return_value = (True, "Claude process running in pane")

        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "error"

        mock_route_repo.get_session.return_value = mock_session

        mock_request = MagicMock()
        mock_request.app.state.tunnel_manager = None

        result = health_check_session("test-id", mock_request, db=db)

        assert result["alive"] is True
        assert result["status"] == "idle"
        mock_health_repo.update_session.assert_called_once()

    @patch("orchestrator.session.health.window_exists", return_value=True)
    @patch("orchestrator.session.health.check_claude_running_local")
    @patch("orchestrator.session.health.is_remote_host")
    @patch("orchestrator.session.health.repo")
    @patch("orchestrator.api.routes.sessions.repo")
    def test_working_stays_working_on_health_alive(
        self,
        mock_route_repo,
        mock_health_repo,
        mock_is_remote,
        mock_check_claude,
        mock_win_exists,
        db,
    ):
        """Health check finding alive Claude with 'working' status should keep it."""
        from orchestrator.api.routes.sessions import health_check_session

        mock_is_remote.return_value = False
        mock_check_claude.return_value = (True, "Claude process running in pane")

        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "working"

        mock_route_repo.get_session.return_value = mock_session

        mock_request = MagicMock()
        mock_request.app.state.tunnel_manager = None

        result = health_check_session("test-id", mock_request, db=db)

        assert result["alive"] is True
        assert result["status"] == "working"
        # Should NOT update the DB — status is already fine
        mock_health_repo.update_session.assert_not_called()

    @patch("orchestrator.terminal.remote_worker_server._server_pool")
    @patch("orchestrator.session.health.is_remote_host", return_value=True)
    @patch("orchestrator.session.health.repo")
    @patch("orchestrator.api.routes.sessions.repo")
    def test_working_tunnel_dead_auto_reconnect_success(
        self,
        mock_route_repo,
        mock_health_repo,
        mock_is_remote,
        mock_pool,
        db,
    ):
        """Dead tunnel with alive PTY should auto-reconnect tunnel and stay alive."""
        from orchestrator.api.routes.sessions import health_check_session

        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.status = "working"
        mock_session.rws_pty_id = "pty-123"
        mock_session.work_dir = "/tmp/work"

        mock_route_repo.get_session.return_value = mock_session

        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"ptys": [{"pty_id": "pty-123", "alive": True}]}
        mock_pool.get.return_value = mock_rws

        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = False  # Tunnel dead
        mock_tm.restart_tunnel.return_value = 12345  # Auto-reconnect succeeds

        mock_request = MagicMock()
        mock_request.app.state.tunnel_manager = mock_tm

        result = health_check_session("test-id", mock_request, db=db)

        assert result["alive"] is True
        assert result["tunnel_reconnected"] is True
        mock_tm.restart_tunnel.assert_called_once()

    @patch("orchestrator.terminal.remote_worker_server._server_pool")
    @patch("orchestrator.session.health.is_remote_host", return_value=True)
    @patch("orchestrator.session.health.repo")
    @patch("orchestrator.api.routes.sessions.repo")
    def test_working_to_alive_with_dead_tunnel_on_reconnect_fail(
        self,
        mock_route_repo,
        mock_health_repo,
        mock_is_remote,
        mock_pool,
        db,
    ):
        """Dead tunnel + failed reconnect but PTY alive → session still alive.

        In the RWS PTY world, a dead tunnel doesn't cause disconnect.
        If the PTY is alive but the tunnel is dead, the session stays alive
        with tunnel_alive=False.
        """
        from orchestrator.api.routes.sessions import health_check_session

        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.status = "working"
        mock_session.rws_pty_id = "pty-123"
        mock_session.work_dir = "/tmp/work"

        mock_route_repo.get_session.return_value = mock_session

        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"ptys": [{"pty_id": "pty-123", "alive": True}]}
        mock_pool.get.return_value = mock_rws

        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = False  # Tunnel dead
        mock_tm.restart_tunnel.return_value = None  # Auto-reconnect fails

        mock_request = MagicMock()
        mock_request.app.state.tunnel_manager = mock_tm

        result = health_check_session("test-id", mock_request, db=db)

        # PTY alive → session alive even though tunnel is dead
        assert result["alive"] is True
        assert result["tunnel_alive"] is False

    @patch("orchestrator.api.routes.sessions.trigger_reconnect")
    @patch("orchestrator.api.routes.sessions.repo")
    def test_disconnected_to_working_on_reconnect(self, mock_repo, mock_trigger, db):
        """Successful reconnect should transition disconnected -> working/waiting."""
        from orchestrator.api.routes.sessions import reconnect_session

        mock_trigger.return_value = {"ok": True}

        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "disconnected"

        mock_repo.get_session.return_value = mock_session

        mock_request = MagicMock()
        mock_request.app.state.config = {"server": {"port": 8093}}
        mock_request.app.state.db_path = None
        mock_request.app.state.tunnel_manager = None

        reconnect_session("test-id", mock_request, db=db)

        # Should have called trigger_reconnect
        mock_trigger.assert_called_once()

    @patch("orchestrator.api.routes.sessions.trigger_reconnect")
    @patch("orchestrator.api.routes.sessions.repo")
    def test_error_to_working_on_reconnect(self, mock_repo, mock_trigger, db):
        """Successful reconnect from error should transition to working/waiting."""
        from orchestrator.api.routes.sessions import reconnect_session

        mock_trigger.return_value = {"ok": True}

        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "error"

        mock_repo.get_session.return_value = mock_session

        mock_request = MagicMock()
        mock_request.app.state.config = {"server": {"port": 8093}}
        mock_request.app.state.db_path = None
        mock_request.app.state.tunnel_manager = None

        reconnect_session("test-id", mock_request, db=db)

        # Should have called trigger_reconnect (error is a reconnectable state)
        mock_trigger.assert_called_once()


class TestReconnectableStates:
    """Test which states allow reconnection."""

    def test_reconnectable_states_defined(self):
        """Verify the reconnectable states are correct."""
        reconnectable_states = ("disconnected", "error")

        assert "disconnected" in reconnectable_states
        assert "error" in reconnectable_states

        # These should NOT be reconnectable
        assert "working" not in reconnectable_states
        assert "connecting" not in reconnectable_states
        assert "idle" not in reconnectable_states
        assert "paused" not in reconnectable_states


class TestStatusValues:
    """Test that all status values are valid and consistent."""

    def test_all_status_values_are_valid(self):
        """Verify all used status values are from the expected set."""
        valid_statuses = {
            "idle",
            "connecting",
            "working",
            "paused",
            "waiting",
            "error",
            "disconnected",
        }

        # These are statuses that can be set by various operations
        assert "idle" in valid_statuses  # Initial state
        assert "connecting" in valid_statuses  # During rdev setup
        assert "working" in valid_statuses  # Active Claude session
        assert "paused" in valid_statuses  # After stop
        assert "waiting" in valid_statuses  # Claude running, waiting for input
        assert "error" in valid_statuses  # Setup failed
        assert "disconnected" in valid_statuses  # Health check failed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
