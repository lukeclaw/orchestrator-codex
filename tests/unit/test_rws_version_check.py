"""Tests for RWS daemon version checking during reconnect and health checks."""

from unittest.mock import MagicMock, patch

import pytest


class TestForceRestartServer:
    """Test the force_restart_server function."""

    @patch("orchestrator.terminal._rws_pool.RemoteWorkerServer")
    def test_kills_old_daemon_and_starts_new(self, mock_rws_cls):
        from orchestrator.terminal.remote_worker_server import (
            _pool_lock,
            _server_pool,
            force_restart_server,
        )

        old_rws = MagicMock()
        with _pool_lock:
            _server_pool["testhost"] = old_rws

        new_rws = MagicMock()
        mock_rws_cls.return_value = new_rws

        try:
            result = force_restart_server("testhost")

            old_rws.kill_remote_daemon.assert_called_once()
            old_rws.stop.assert_called_once()
            mock_rws_cls.assert_called_once_with("testhost")
            new_rws.start.assert_called_once_with(timeout=30.0)
            assert result is new_rws
            with _pool_lock:
                assert _server_pool["testhost"] is new_rws
        finally:
            with _pool_lock:
                _server_pool.pop("testhost", None)

    @patch("orchestrator.terminal._rws_pool.RemoteWorkerServer")
    def test_starts_fresh_when_no_old_server(self, mock_rws_cls):
        from orchestrator.terminal.remote_worker_server import (
            _pool_lock,
            _server_pool,
            force_restart_server,
        )

        with _pool_lock:
            _server_pool.pop("newhost", None)

        new_rws = MagicMock()
        mock_rws_cls.return_value = new_rws

        try:
            result = force_restart_server("newhost")

            mock_rws_cls.assert_called_once_with("newhost")
            new_rws.start.assert_called_once_with(timeout=30.0)
            assert result is new_rws
        finally:
            with _pool_lock:
                _server_pool.pop("newhost", None)

    @patch("orchestrator.terminal._rws_pool.RemoteWorkerServer")
    def test_raises_on_start_failure(self, mock_rws_cls):
        from orchestrator.terminal.remote_worker_server import (
            _pool_lock,
            _server_pool,
            force_restart_server,
        )

        with _pool_lock:
            _server_pool.pop("badhost", None)

        new_rws = MagicMock()
        new_rws.start.side_effect = RuntimeError("SSH failed")
        mock_rws_cls.return_value = new_rws

        with pytest.raises(RuntimeError, match="SSH failed"):
            force_restart_server("badhost")

        with _pool_lock:
            assert "badhost" not in _server_pool


class TestReconnectRWSVersionCheck:
    """Test that _reconnect_rws_for_host checks daemon version after tunnel reconnect."""

    def _make_session(self, host="remotehost", name="worker1", sid="sess-1"):
        session = MagicMock()
        session.host = host
        session.name = name
        session.id = sid
        return session

    @patch("orchestrator.terminal.interactive.get_active_cli", return_value=None)
    def test_redeploys_when_version_mismatches(self, _mock_cli):
        from orchestrator.session.reconnect import _reconnect_rws_for_host
        from orchestrator.terminal.remote_worker_server import (
            _pool_lock,
            _server_pool,
        )

        rws = MagicMock()
        rws.reconnect_tunnel.return_value = None
        rws.execute.return_value = {"version": "old_version_1", "status": "ok"}

        session = self._make_session()

        with _pool_lock:
            _server_pool[session.host] = rws

        try:
            with patch(
                "orchestrator.terminal.remote_worker_server.force_restart_server"
            ) as mock_force_restart:
                _reconnect_rws_for_host(session)

            rws.reconnect_tunnel.assert_called_once()
            rws.execute.assert_called_once_with({"action": "server_info"}, timeout=5)
            mock_force_restart.assert_called_once_with(session.host)
        finally:
            with _pool_lock:
                _server_pool.pop(session.host, None)

    @patch("orchestrator.terminal.interactive.get_active_cli", return_value=None)
    def test_no_redeploy_when_version_matches(self, _mock_cli):
        import orchestrator.terminal.remote_worker_server as rws_mod
        from orchestrator.session.reconnect import _reconnect_rws_for_host
        from orchestrator.terminal.remote_worker_server import (
            _pool_lock,
            _server_pool,
        )

        rws = MagicMock()
        rws.reconnect_tunnel.return_value = None
        rws.execute.return_value = {
            "version": rws_mod._SCRIPT_HASH,
            "status": "ok",
        }

        session = self._make_session()

        with _pool_lock:
            _server_pool[session.host] = rws

        try:
            with patch(
                "orchestrator.terminal.remote_worker_server.force_restart_server"
            ) as mock_force_restart:
                _reconnect_rws_for_host(session)

            rws.reconnect_tunnel.assert_called_once()
            mock_force_restart.assert_not_called()
        finally:
            with _pool_lock:
                _server_pool.pop(session.host, None)

    @patch("orchestrator.terminal.interactive.get_active_cli", return_value=None)
    def test_version_check_failure_is_non_fatal(self, _mock_cli):
        """If version check fails (e.g. network error), don't crash reconnect."""
        from orchestrator.session.reconnect import _reconnect_rws_for_host
        from orchestrator.terminal.remote_worker_server import (
            _pool_lock,
            _server_pool,
        )

        rws = MagicMock()
        rws.reconnect_tunnel.return_value = None
        rws.execute.side_effect = RuntimeError("socket closed")

        session = self._make_session()

        with _pool_lock:
            _server_pool[session.host] = rws

        try:
            with patch(
                "orchestrator.terminal.remote_worker_server.force_restart_server"
            ) as mock_force_restart:
                _reconnect_rws_for_host(session)

            mock_force_restart.assert_not_called()
        finally:
            with _pool_lock:
                _server_pool.pop(session.host, None)
