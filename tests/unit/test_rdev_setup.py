"""Tests for remote worker setup orchestration (RWS PTY architecture)."""

from unittest.mock import MagicMock, patch

from orchestrator.terminal.session import setup_remote_worker
from scripts.seed_db import seed_all

_MOCK_SSH_CONFIG = "orchestrator.terminal.ssh.ensure_rdev_ssh_config"


class TestSetupRemoteWorker:
    @patch(_MOCK_SSH_CONFIG, return_value=True)
    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session._copy_dir_to_remote_ssh", return_value=False)
    @patch("orchestrator.terminal.session.subprocess")
    def test_ssh_copy_failure(self, mock_subprocess, mock_copy, _sleep, _ssh_cfg, db):
        """Setup should fail if SSH file copy fails."""
        seed_all(db)

        mock_tunnel_manager = MagicMock()
        mock_tunnel_manager.start_tunnel.return_value = 99999

        result = setup_remote_worker(
            db,
            "session-id-456",
            "w2",
            "jobs-mt/epic-turing",
            "orchestrator",
            8093,
            tunnel_manager=mock_tunnel_manager,
        )

        assert result["ok"] is False
        assert "copy" in result["error"].lower() or "ssh" in result["error"].lower()
        # Tunnel should be cleaned up via tunnel_manager
        mock_tunnel_manager.stop_tunnel.assert_called_once_with("session-id-456")

    @patch(_MOCK_SSH_CONFIG, return_value=True)
    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session._ensure_rws_ready")
    @patch("orchestrator.terminal.session._copy_dir_to_remote_ssh", return_value=True)
    @patch("orchestrator.terminal.session.subprocess")
    def test_tunnel_start_failure(
        self, mock_subprocess, mock_copy, mock_rws_ready, _sleep, _ssh_cfg, db
    ):
        """Setup should continue even if tunnel start fails."""
        seed_all(db)

        mock_rws = MagicMock()
        mock_rws.create_pty.return_value = "pty-abc123"
        mock_rws.execute.return_value = {"ptys": [{"pty_id": "pty-abc123", "alive": True}]}
        mock_rws_ready.return_value = mock_rws

        mock_tunnel_manager = MagicMock()
        mock_tunnel_manager.start_tunnel.return_value = None  # Tunnel start fails

        result = setup_remote_worker(
            db,
            "session-id-789",
            "w3",
            "subs-mt/test",
            "orchestrator",
            8093,
            tunnel_manager=mock_tunnel_manager,
        )

        # Setup should succeed even without tunnel (tunnel is retried by health monitor)
        assert result["ok"] is True
        assert result["tunnel_pid"] is None

    @patch(_MOCK_SSH_CONFIG, return_value=True)
    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session._copy_dir_to_remote_ssh", return_value=False)
    @patch("orchestrator.terminal.session.subprocess")
    def test_no_tunnel_manager(self, mock_subprocess, mock_copy, _sleep, _ssh_cfg, db):
        """Setup should work without tunnel_manager (tunnel skipped with warning)."""
        seed_all(db)

        result = setup_remote_worker(
            db,
            "session-id-000",
            "w4",
            "subs-mt/test",
            "orchestrator",
            8093,
            # No tunnel_manager provided
        )

        # Should fail on SSH copy (no real host), but tunnel step doesn't crash
        assert result["ok"] is False

    @patch(_MOCK_SSH_CONFIG, return_value=True)
    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session._ensure_rws_ready")
    @patch("orchestrator.terminal.session._copy_dir_to_remote_ssh", return_value=True)
    @patch("orchestrator.terminal.session.subprocess")
    def test_rws_pty_created_on_success(
        self, mock_subprocess, mock_copy, mock_rws_ready, _sleep, _ssh_cfg, db
    ):
        """Successful setup should create an RWS PTY and store the pty_id."""
        seed_all(db)

        mock_rws = MagicMock()
        mock_rws.create_pty.return_value = "pty-xyz789"
        mock_rws.execute.return_value = {"ptys": [{"pty_id": "pty-xyz789", "alive": True}]}
        mock_rws_ready.return_value = mock_rws

        mock_tunnel_manager = MagicMock()
        mock_tunnel_manager.start_tunnel.return_value = 12345

        result = setup_remote_worker(
            db,
            "session-id-success",
            "w5",
            "subs-mt/test",
            "orchestrator",
            8093,
            work_dir="/home/user/code",
            tunnel_manager=mock_tunnel_manager,
        )

        assert result["ok"] is True
        assert result["tunnel_pid"] == 12345

        # Verify create_pty was called with session_id
        mock_rws.create_pty.assert_called_once()
        call_kwargs = mock_rws.create_pty.call_args
        assert call_kwargs.kwargs.get("session_id") == "session-id-success"

    @patch(_MOCK_SSH_CONFIG, return_value=False)
    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session.subprocess")
    def test_ssh_config_bootstrap_failure(self, mock_subprocess, _sleep, _ssh_cfg, db):
        """Setup should fail early if SSH config cannot be bootstrapped."""
        seed_all(db)

        result = setup_remote_worker(
            db,
            "session-id-new",
            "w6",
            "mp/brand-new-rdev",
            "orchestrator",
            8093,
        )

        assert result["ok"] is False
        assert "ssh config" in result["error"].lower()
