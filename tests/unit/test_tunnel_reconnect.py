"""Tests for tunnel-only reconnect and RWS PTY reconnect functionality.

Updated for RWS PTY-based reconnection. When the tunnel disconnects,
we reconnect via RWS PTY instead of the legacy screen/tmux-based flow.
"""

from unittest.mock import MagicMock, patch


class TestReconnectTunnelOnly:
    """Test the reconnect_tunnel_only helper function with tunnel_manager."""

    def test_tunnel_reconnect_success(self, db):
        """Should reconnect tunnel via tunnel_manager and return True on success."""
        from orchestrator.session.reconnect import reconnect_tunnel_only

        mock_session = MagicMock()
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.id = "test-session-id"
        mock_session.rws_pty_id = None

        mock_repo = MagicMock()
        mock_tm = MagicMock()
        mock_tm.restart_tunnel.return_value = 12345

        result = reconnect_tunnel_only(
            db, mock_session, "orchestrator", 8093, mock_repo, tunnel_manager=mock_tm
        )

        assert result is True
        mock_tm.restart_tunnel.assert_called_once_with(
            "test-session-id", "test-worker", "subs-mt/test-vm"
        )
        mock_repo.update_session.assert_called_once_with(db, "test-session-id", tunnel_pid=12345)

    def test_tunnel_reconnect_failure(self, db):
        """Should return False if tunnel_manager.restart_tunnel returns None."""
        from orchestrator.session.reconnect import reconnect_tunnel_only

        mock_session = MagicMock()
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.id = "test-session-id"
        mock_session.rws_pty_id = None

        mock_repo = MagicMock()
        mock_tm = MagicMock()
        mock_tm.restart_tunnel.return_value = None

        result = reconnect_tunnel_only(
            db, mock_session, "orchestrator", 8093, mock_repo, tunnel_manager=mock_tm
        )

        assert result is False
        mock_repo.update_session.assert_not_called()

    def test_tunnel_reconnect_no_manager(self, db):
        """Should return False if no tunnel_manager provided."""
        from orchestrator.session.reconnect import reconnect_tunnel_only

        mock_session = MagicMock()
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.id = "test-session-id"
        mock_session.rws_pty_id = None

        mock_repo = MagicMock()

        result = reconnect_tunnel_only(
            db, mock_session, "orchestrator", 8093, mock_repo, tunnel_manager=None
        )

        assert result is False

    def test_tunnel_reconnect_handles_exception(self, db):
        """Should return False if tunnel_manager raises an exception."""
        from orchestrator.session.reconnect import reconnect_tunnel_only

        mock_session = MagicMock()
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.id = "test-session-id"
        mock_session.rws_pty_id = None

        mock_repo = MagicMock()
        mock_tm = MagicMock()
        mock_tm.restart_tunnel.side_effect = OSError("SSH binary not found")

        result = reconnect_tunnel_only(
            db, mock_session, "orchestrator", 8093, mock_repo, tunnel_manager=mock_tm
        )

        assert result is False


class TestReconnectRemoteWorkerRWSPath:
    """Test that reconnect_remote_worker uses RWS PTY for reconnection.

    The new reconnect_remote_worker always creates an RWS PTY instead of
    the legacy screen/tmux-based reconnect.
    """

    @patch("orchestrator.session.reconnect._copy_configs_to_remote")
    @patch("orchestrator.session.reconnect._ensure_local_configs_exist")
    @patch("orchestrator.session.reconnect.subprocess")
    def test_rws_pty_path_when_pty_id_set(
        self,
        mock_reconnect_subprocess,
        mock_configs,
        mock_copy,
        db,
    ):
        """When session has rws_pty_id, reconnect calls _reconnect_rws_pty_worker."""
        from orchestrator.session.reconnect import reconnect_remote_worker

        mock_session = MagicMock()
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.id = "test-session-id"
        mock_session.work_dir = "/home/user/code"
        mock_session.rws_pty_id = "pty-existing-123"
        mock_session.claude_session_id = None

        mock_repo = MagicMock()
        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = True

        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"ptys": [{"pty_id": "pty-existing-123", "alive": True}]}

        with (
            patch(
                "orchestrator.terminal.session._ensure_rws_ready",
                return_value=mock_rws,
            ),
            patch("orchestrator.session.reconnect._reconnect_rws_for_host"),
            patch("orchestrator.session.reconnect.time.sleep"),
        ):
            reconnect_remote_worker(
                db,
                mock_session,
                "orchestrator",
                "test-worker",
                8093,
                "/tmp",
                mock_repo,
                tunnel_manager=mock_tm,
            )

        # PTY still alive -- should set status to waiting
        mock_repo.update_session.assert_any_call(db, "test-session-id", status="waiting")

    @patch("orchestrator.session.reconnect._copy_configs_to_remote")
    @patch("orchestrator.session.reconnect._ensure_local_configs_exist")
    @patch("orchestrator.session.reconnect.subprocess")
    def test_creates_new_rws_pty_when_no_pty_id(
        self,
        mock_reconnect_subprocess,
        mock_configs,
        mock_copy,
        db,
    ):
        """When session has no rws_pty_id, reconnect creates a new RWS PTY."""
        from orchestrator.session.reconnect import reconnect_remote_worker

        mock_session = MagicMock()
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.id = "test-session-id"
        mock_session.work_dir = "/home/user/code"
        mock_session.rws_pty_id = None
        mock_session.claude_session_id = None

        mock_repo = MagicMock()
        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = False

        mock_rws = MagicMock()
        mock_rws.create_pty.return_value = "pty-new-456"

        with (
            patch(
                "orchestrator.terminal.session._ensure_rws_ready",
                return_value=mock_rws,
            ),
            patch("orchestrator.session.reconnect._ensure_tunnel"),
            patch("orchestrator.session.reconnect._reconnect_rws_for_host"),
            patch(
                "orchestrator.session.reconnect._check_claude_session_exists_remote",
                return_value=False,
            ),
            patch(
                "orchestrator.terminal.session._build_claude_command",
                return_value="claude --session-id test-session-id",
            ),
            patch("orchestrator.session.reconnect.time.sleep"),
        ):
            reconnect_remote_worker(
                db,
                mock_session,
                "orchestrator",
                "test-worker",
                8093,
                "/tmp",
                mock_repo,
                tunnel_manager=mock_tm,
            )

        # Should have created a new PTY
        mock_rws.create_pty.assert_called_once()
        # Should have updated session with pty_id
        update_calls = [str(c) for c in mock_repo.update_session.call_args_list]
        assert any("pty-new-456" in c for c in update_calls)


class TestHealthCheckAutoReconnectTunnel:
    """Test that health check auto-reconnects dead tunnels via RWS PTY path.

    All remote sessions now route through _check_rws_pty_health. In this path,
    a dead tunnel doesn't make the session dead if the PTY is still alive --
    the response includes tunnel_alive=False but alive=True.
    """

    @patch("orchestrator.terminal.remote_worker_server._server_pool")
    @patch("orchestrator.session.health.is_remote_host", return_value=True)
    @patch("orchestrator.session.health.repo")
    @patch("orchestrator.api.routes.sessions.repo")
    def test_health_check_auto_reconnects_tunnel(
        self,
        mock_route_repo,
        mock_health_repo,
        mock_is_remote,
        mock_pool,
        db,
    ):
        """Health check should auto-reconnect tunnel when PTY alive but tunnel dead."""
        from orchestrator.api.routes.sessions import health_check_session

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.rws_pty_id = "pty-123"
        mock_session.status = "waiting"
        mock_session.work_dir = "/tmp/work"
        mock_route_repo.get_session.return_value = mock_session

        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"ptys": [{"pty_id": "pty-123", "alive": True}]}
        mock_pool.get.return_value = mock_rws

        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = False
        mock_tm.restart_tunnel.return_value = 12345

        mock_request = MagicMock()
        mock_request.app.state.tunnel_manager = mock_tm

        result = health_check_session("test-session-id", mock_request, db)

        assert result["alive"] is True
        assert result["tunnel_reconnected"] is True
        mock_tm.restart_tunnel.assert_called_once_with(
            "test-session-id", "test-worker", "subs-mt/test-vm"
        )

    @patch("orchestrator.terminal.remote_worker_server._server_pool")
    @patch("orchestrator.session.health.is_remote_host", return_value=True)
    @patch("orchestrator.session.health.repo")
    @patch("orchestrator.api.routes.sessions.repo")
    def test_health_check_reports_failure_when_tunnel_reconnect_fails(
        self,
        mock_route_repo,
        mock_health_repo,
        mock_is_remote,
        mock_pool,
        db,
    ):
        """Tunnel dead + restart fails but PTY alive → session still alive.

        In the RWS PTY path, a dead tunnel doesn't kill the session. The PTY
        is still running on the remote host; only the API callback tunnel is down.
        """
        from orchestrator.api.routes.sessions import health_check_session

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.rws_pty_id = "pty-123"
        mock_session.status = "waiting"
        mock_session.work_dir = "/tmp/work"
        mock_route_repo.get_session.return_value = mock_session

        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"ptys": [{"pty_id": "pty-123", "alive": True}]}
        mock_pool.get.return_value = mock_rws

        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = False
        mock_tm.restart_tunnel.return_value = None  # Restart fails

        mock_request = MagicMock()
        mock_request.app.state.tunnel_manager = mock_tm

        result = health_check_session("test-session-id", mock_request, db)

        # PTY alive → session alive even though tunnel is dead
        assert result["alive"] is True
        assert result["tunnel_alive"] is False
        assert result["reason"] == "RWS PTY alive"

    @patch("orchestrator.terminal.remote_worker_server._server_pool")
    @patch("orchestrator.session.health.is_remote_host", return_value=True)
    @patch("orchestrator.session.health.repo")
    @patch("orchestrator.api.routes.sessions.repo")
    def test_health_check_includes_tunnel_error_on_failure(
        self,
        mock_route_repo,
        mock_health_repo,
        mock_is_remote,
        mock_pool,
        db,
    ):
        """Tunnel dead + restart fails → PTY still alive with tunnel_alive=False."""
        from orchestrator.api.routes.sessions import health_check_session

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "subs-mt/test-vm"
        mock_session.status = "waiting"
        mock_session.rws_pty_id = "pty-123"
        mock_session.work_dir = "/tmp/work"
        mock_route_repo.get_session.return_value = mock_session

        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"ptys": [{"pty_id": "pty-123", "alive": True}]}
        mock_pool.get.return_value = mock_rws

        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = False
        mock_tm.restart_tunnel.return_value = None

        mock_request = MagicMock()
        mock_request.app.state.tunnel_manager = mock_tm

        result = health_check_session("test-session-id", mock_request, db)

        # PTY alive → session alive but tunnel is down
        assert result["alive"] is True
        assert result["tunnel_alive"] is False
