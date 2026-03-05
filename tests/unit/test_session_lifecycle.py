"""Unit tests for session lifecycle operations (create/delete/stop)."""

from unittest.mock import MagicMock, patch

import pytest


class TestSessionCreate:
    """Test session creation for local and rdev workers."""

    @patch("orchestrator.api.routes.sessions.threading")
    @patch("orchestrator.api.routes.sessions.ensure_window")
    @patch("orchestrator.api.routes.sessions.repo")
    @patch("orchestrator.api.routes.sessions.is_remote_host")
    def test_create_local_session_success(
        self, mock_is_remote, mock_repo, mock_ensure_window, mock_threading, db
    ):
        """Creating a local session should create tmux window and return session."""
        from orchestrator.api.routes.sessions import SessionCreate, create_session

        mock_is_remote.return_value = False
        mock_ensure_window.return_value = "orchestrator:test-worker"

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "idle"

        mock_session.work_dir = None
        mock_session.takeover_mode = False
        mock_session.created_at = "2024-01-01"
        mock_session.last_status_changed_at = "2024-01-01"
        mock_session.session_type = "local"
        mock_repo.create_session.return_value = mock_session

        body = SessionCreate(name="test-worker", host="localhost")
        mock_request = MagicMock()
        mock_request.app.state.config = {"tmux_session_name": "orchestrator", "api_port": 8093}

        with patch("orchestrator.api.routes.sessions.send_keys"):
            result = create_session(body, mock_request, db=db)

        assert result["name"] == "test-worker"
        mock_ensure_window.assert_called_once()
        mock_repo.create_session.assert_called_once()

    @patch("orchestrator.terminal.session.setup_local_worker")
    @patch("orchestrator.api.routes.sessions.threading")
    @patch("orchestrator.api.routes.sessions.ensure_window")
    @patch("orchestrator.api.routes.sessions.repo")
    @patch("orchestrator.api.routes.sessions.is_remote_host")
    def test_create_local_session_tmux_failure_graceful(
        self, mock_is_remote, mock_repo, mock_ensure_window, mock_threading, mock_setup, db
    ):
        """If tmux window creation fails, session should still be created."""
        from orchestrator.api.routes.sessions import SessionCreate, create_session

        mock_is_remote.return_value = False
        mock_ensure_window.side_effect = Exception("tmux error")
        mock_setup.return_value = {"ok": True}

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "idle"

        mock_repo.create_session.return_value = mock_session

        body = SessionCreate(name="test-worker", host="localhost")
        mock_request = MagicMock()
        mock_request.app.state.config = {"server": {"port": 8093}}

        result = create_session(body, mock_request, db=db)

        # Session should still be created even if tmux fails
        assert result is not None
        mock_repo.create_session.assert_called_once()

    @patch("orchestrator.api.routes.sessions.threading")
    @patch("orchestrator.api.routes.sessions.ensure_window")
    @patch("orchestrator.api.routes.sessions.repo")
    @patch("orchestrator.api.routes.sessions.is_remote_host")
    def test_create_rdev_session_returns_connecting_status(
        self, mock_is_remote, mock_repo, mock_ensure_window, mock_threading, db
    ):
        """Creating an rdev session should return with 'connecting' status."""
        from orchestrator.api.routes.sessions import SessionCreate, create_session

        mock_is_remote.return_value = True
        mock_ensure_window.return_value = "orchestrator:test-rdev"

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.status = "connecting"

        mock_repo.create_session.return_value = mock_session

        body = SessionCreate(name="test-rdev", host="user/rdev-vm")
        mock_request = MagicMock()
        mock_request.app.state.config = {"tmux_session_name": "orchestrator", "api_port": 8093}

        create_session(body, mock_request, db=db)

        # Status should be updated to connecting before background thread starts
        mock_repo.update_session.assert_called()

    @patch("orchestrator.api.routes.sessions.threading")
    @patch("orchestrator.api.routes.sessions.ensure_window")
    @patch("orchestrator.api.routes.sessions.repo")
    @patch("orchestrator.api.routes.sessions.is_remote_host")
    def test_create_rdev_session_spawns_background_thread(
        self, mock_is_remote, mock_repo, mock_ensure_window, mock_threading, db
    ):
        """Creating an rdev session should spawn a background thread for setup."""
        from orchestrator.api.routes.sessions import SessionCreate, create_session

        mock_is_remote.return_value = True
        mock_ensure_window.return_value = "orchestrator:test-rdev"

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.status = "idle"
        mock_repo.create_session.return_value = mock_session

        body = SessionCreate(name="test-rdev", host="user/rdev-vm")
        mock_request = MagicMock()
        mock_request.app.state.config = {"tmux_session_name": "orchestrator", "api_port": 8093}

        create_session(body, mock_request, db=db)

        # Should have created a thread
        mock_threading.Thread.assert_called_once()
        mock_threading.Thread.return_value.start.assert_called_once()

    @patch("orchestrator.api.routes.sessions.threading")
    @patch("orchestrator.api.routes.sessions.ensure_window")
    @patch("orchestrator.api.routes.sessions.repo")
    @patch("orchestrator.api.routes.sessions.is_remote_host")
    def test_create_session_sanitizes_worker_name(
        self, mock_is_remote, mock_repo, mock_ensure_window, mock_threading, db
    ):
        """Worker names with / or \\ should be sanitized."""
        from orchestrator.api.routes.sessions import SessionCreate, create_session

        mock_is_remote.return_value = False
        mock_ensure_window.return_value = "orchestrator:test_worker"

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test_worker"
        mock_session.host = "localhost"
        mock_session.status = "idle"
        mock_repo.create_session.return_value = mock_session

        # Name with path separator
        body = SessionCreate(name="test/worker", host="localhost")
        mock_request = MagicMock()
        mock_request.app.state.config = {"tmux_session_name": "orchestrator", "api_port": 8093}

        create_session(body, mock_request, db=db)

        # ensure_window should be called with sanitized name
        call_args = mock_ensure_window.call_args
        assert "/" not in call_args[0][1], "Worker name should be sanitized"

    @patch("orchestrator.state.repositories.tasks.update_task")
    @patch("orchestrator.api.routes.sessions.threading")
    @patch("orchestrator.api.routes.sessions.ensure_window")
    @patch("orchestrator.api.routes.sessions.repo")
    @patch("orchestrator.api.routes.sessions.is_remote_host")
    def test_create_local_session_with_task_id_assigns_task(
        self, mock_is_remote, mock_repo, mock_ensure_window, mock_threading, mock_update_task, db
    ):
        """Creating a local session with task_id should assign the task to the worker."""
        from orchestrator.api.routes.sessions import SessionCreate, create_session

        mock_is_remote.return_value = False
        mock_ensure_window.return_value = "orchestrator:test-worker"

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "idle"
        mock_repo.create_session.return_value = mock_session

        body = SessionCreate(name="test-worker", host="localhost", task_id="task-123")
        mock_request = MagicMock()
        mock_request.app.state.config = {"tmux_session_name": "orchestrator", "api_port": 8093}

        with patch("orchestrator.api.routes.sessions.send_keys"):
            create_session(body, mock_request, db=db)

        mock_update_task.assert_called_once_with(
            db, "task-123", assigned_session_id="test-session-id", status="in_progress"
        )

    @patch("orchestrator.state.repositories.tasks.update_task")
    @patch("orchestrator.api.routes.sessions.threading")
    @patch("orchestrator.api.routes.sessions.ensure_window")
    @patch("orchestrator.api.routes.sessions.repo")
    @patch("orchestrator.api.routes.sessions.is_remote_host")
    def test_create_local_session_without_task_id_skips_assignment(
        self, mock_is_remote, mock_repo, mock_ensure_window, mock_threading, mock_update_task, db
    ):
        """Creating a local session without task_id should not assign any task."""
        from orchestrator.api.routes.sessions import SessionCreate, create_session

        mock_is_remote.return_value = False
        mock_ensure_window.return_value = "orchestrator:test-worker"

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "idle"
        mock_repo.create_session.return_value = mock_session

        body = SessionCreate(name="test-worker", host="localhost")
        mock_request = MagicMock()
        mock_request.app.state.config = {"tmux_session_name": "orchestrator", "api_port": 8093}

        with patch("orchestrator.api.routes.sessions.send_keys"):
            create_session(body, mock_request, db=db)

        mock_update_task.assert_not_called()


class TestSessionDelete:
    """Test session deletion for local and rdev workers."""

    @patch("orchestrator.api.routes.sessions.kill_window")
    @patch("orchestrator.api.routes.sessions.repo")
    @patch("orchestrator.api.routes.sessions.is_remote_host")
    def test_delete_local_session_kills_tmux_window(
        self, mock_is_remote, mock_repo, mock_kill_window, db
    ):
        """Deleting a local session should kill its tmux window."""
        from orchestrator.api.routes.sessions import delete_session

        mock_is_remote.return_value = False

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"

        mock_repo.get_session.return_value = mock_session

        mock_request = MagicMock()
        mock_request.app.state.tunnel_manager = None

        delete_session("test-session-id", mock_request, db=db)

        mock_kill_window.assert_called()
        mock_repo.delete_session.assert_called_once_with(db, "test-session-id")

    @patch("orchestrator.api.routes.sessions.shutil.rmtree")
    @patch("orchestrator.api.routes.sessions.os.path.exists")
    @patch("orchestrator.api.routes.sessions.kill_window")
    @patch("orchestrator.api.routes.sessions.repo")
    @patch("orchestrator.api.routes.sessions.is_remote_host")
    def test_delete_local_session_cleans_tmp_dir(
        self, mock_is_remote, mock_repo, mock_kill_window, mock_exists, mock_rmtree, db
    ):
        """Deleting a local session should clean up tmp directory."""
        from orchestrator.api.routes.sessions import delete_session

        mock_is_remote.return_value = False
        mock_exists.return_value = True

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"

        mock_session.work_dir = "/home/user/project"
        mock_repo.get_session.return_value = mock_session

        mock_request = MagicMock()
        mock_request.app.state.tunnel_manager = None

        delete_session("test-session-id", mock_request, db=db)

        # Should clean tmp dir
        mock_rmtree.assert_called()
        # Verify it's the tmp dir, not work_dir
        rmtree_path = mock_rmtree.call_args[0][0]
        assert "workers" in rmtree_path and "test-worker" in rmtree_path

    @pytest.mark.allow_subprocess
    @patch("orchestrator.api.routes.sessions.time.sleep")
    @patch("orchestrator.api.routes.sessions.send_keys")
    @patch("orchestrator.api.routes.sessions.kill_window")
    @patch("orchestrator.api.routes.sessions.repo")
    @patch("orchestrator.api.routes.sessions.is_remote_host")
    def test_delete_rdev_session_exits_claude_and_screen(
        self, mock_is_remote, mock_repo, mock_kill_window, mock_send_keys, mock_sleep, db
    ):
        """Deleting an rdev session should exit Claude and screen before cleanup."""
        from orchestrator.api.routes.sessions import delete_session

        mock_is_remote.return_value = True

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"

        mock_repo.get_session.return_value = mock_session

        mock_request = MagicMock()
        mock_request.app.state.tunnel_manager = MagicMock()

        delete_session("test-session-id", mock_request, db=db)

        # Should have sent exit commands
        exit_calls = [c for c in mock_send_keys.call_args_list if "exit" in str(c)]
        assert len(exit_calls) >= 2, "Should send 'exit' at least twice (Claude + screen)"

    @patch("orchestrator.api.routes.sessions.send_keys")
    @patch("orchestrator.api.routes.sessions.kill_window")
    @patch("orchestrator.api.routes.sessions.repo")
    @patch("orchestrator.api.routes.sessions.is_remote_host")
    def test_delete_rdev_session_kills_tunnel(
        self, mock_is_remote, mock_repo, mock_kill_window, mock_send_keys, db
    ):
        """Deleting an rdev session should stop the tunnel subprocess."""
        from orchestrator.api.routes.sessions import delete_session

        mock_is_remote.return_value = True

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"

        mock_repo.get_session.return_value = mock_session

        mock_tm = MagicMock()
        mock_tm.stop_tunnel.return_value = True
        mock_request = MagicMock()
        mock_request.app.state.tunnel_manager = mock_tm

        delete_session("test-session-id", mock_request, db=db)

        # Should stop tunnel via tunnel_manager
        mock_tm.stop_tunnel.assert_called_once_with("test-session-id")

    @patch("orchestrator.api.routes.sessions.kill_window")
    @patch("orchestrator.api.routes.sessions.repo")
    @patch("orchestrator.api.routes.sessions.is_remote_host")
    def test_delete_session_deletes_from_db(self, mock_is_remote, mock_repo, mock_kill_window, db):
        """Deleting a session should delete it from the database."""
        from orchestrator.api.routes.sessions import delete_session

        mock_is_remote.return_value = False

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"

        mock_repo.get_session.return_value = mock_session

        mock_request = MagicMock()
        mock_request.app.state.tunnel_manager = None

        delete_session("test-session-id", mock_request, db=db)

        # Should delete from database
        mock_repo.delete_session.assert_called_once_with(db, "test-session-id")


class TestSessionStop:
    """Test session stop functionality."""

    @patch("orchestrator.state.repositories.tasks.update_task")
    @patch("orchestrator.state.repositories.tasks.list_tasks")
    @patch("orchestrator.api.routes.sessions.repo")
    def test_stop_session_updates_status_to_idle(
        self, mock_repo, mock_list_tasks, mock_update_task, db
    ):
        """Stopping a session should update status to 'idle'."""
        from orchestrator.api.routes.sessions import stop_session

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "working"

        mock_repo.get_session.return_value = mock_session
        mock_list_tasks.return_value = []

        with patch("orchestrator.api.routes.sessions.send_keys"):
            with patch("orchestrator.terminal.manager.send_keys_literal"):
                stop_session("test-session-id", db=db)

        # Status should be updated to idle
        update_calls = mock_repo.update_session.call_args_list
        assert any("idle" in str(c) for c in update_calls), "Should update status to idle"

    @patch("orchestrator.state.repositories.tasks.update_task")
    @patch("orchestrator.state.repositories.tasks.list_tasks")
    @patch("orchestrator.api.routes.sessions.repo")
    def test_stop_session_unassigns_non_done_tasks(
        self, mock_repo, mock_list_tasks, mock_update_task, db
    ):
        """Stopping a session should unassign tasks that are not done."""
        from orchestrator.api.routes.sessions import stop_session

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "working"

        mock_repo.get_session.return_value = mock_session

        # One in_progress task
        mock_task = MagicMock()
        mock_task.id = "task-1"
        mock_task.status = "in_progress"
        mock_list_tasks.return_value = [mock_task]

        with patch("orchestrator.api.routes.sessions.send_keys"):
            with patch("orchestrator.terminal.manager.send_keys_literal"):
                stop_session("test-session-id", db=db)

        # Task should be unassigned
        mock_update_task.assert_called()

    @patch("orchestrator.state.repositories.tasks.update_task")
    @patch("orchestrator.state.repositories.tasks.list_tasks")
    @patch("orchestrator.api.routes.sessions.repo")
    def test_stop_session_preserves_done_task_status(
        self, mock_repo, mock_list_tasks, mock_update_task, db
    ):
        """Stopping a session should NOT reset tasks that are already done."""
        from orchestrator.api.routes.sessions import stop_session

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "working"

        mock_repo.get_session.return_value = mock_session

        # One done task
        mock_task = MagicMock()
        mock_task.id = "task-1"
        mock_task.status = "done"
        mock_list_tasks.return_value = [mock_task]

        with patch("orchestrator.api.routes.sessions.send_keys"):
            with patch("orchestrator.terminal.manager.send_keys_literal"):
                stop_session("test-session-id", db=db)

        # Task status should remain done (None means don't change)
        if mock_update_task.called:
            call_kwargs = mock_update_task.call_args[1]
            assert call_kwargs.get("status") is None or call_kwargs.get("status") == "done"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
