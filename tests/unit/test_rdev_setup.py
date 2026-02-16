"""Tests for rdev worker setup orchestration."""

from unittest.mock import patch, MagicMock

from scripts.seed_db import seed_all
from orchestrator.terminal.session import setup_rdev_worker


class TestSetupRdevWorker:
    @patch("orchestrator.terminal.session.time.sleep")  # Mock sleep to speed up tests
    @patch("orchestrator.terminal.session.tmux")
    @patch("orchestrator.terminal.session.ssh")
    def test_ssh_prompt_timeout(self, mock_ssh, mock_tmux, _sleep, db):
        seed_all(db)
        mock_tmux.send_keys.return_value = True
        mock_ssh.rdev_connect.return_value = True
        mock_ssh.wait_for_prompt.return_value = False

        mock_tunnel_manager = MagicMock()
        mock_tunnel_manager.start_tunnel.return_value = 99999

        result = setup_rdev_worker(
            db, "session-id-456", "w2", "jobs-mt/epic-turing",
            "orchestrator", 8093,
            tunnel_manager=mock_tunnel_manager,
        )

        assert result["ok"] is False
        assert "timeout" in result["error"].lower() or "prompt" in result["error"].lower()
        # Tunnel should be cleaned up via tunnel_manager
        mock_tunnel_manager.stop_tunnel.assert_called_once_with("session-id-456")

    @patch("orchestrator.terminal.session.time.sleep")  # Mock sleep to speed up tests
    @patch("orchestrator.terminal.session.tmux")
    @patch("orchestrator.terminal.session.ssh")
    def test_tunnel_start_failure(self, mock_ssh, mock_tmux, _sleep, db):
        seed_all(db)

        mock_tunnel_manager = MagicMock()
        mock_tunnel_manager.start_tunnel.return_value = None  # Tunnel start fails

        result = setup_rdev_worker(
            db, "session-id-789", "w3", "subs-mt/test",
            "orchestrator", 8093,
            tunnel_manager=mock_tunnel_manager,
        )

        # Setup should continue even if tunnel fails (tunnel is retried by monitor)
        # The SSH connection attempt should still happen
        mock_ssh.rdev_connect.assert_called_once()

    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session.tmux")
    @patch("orchestrator.terminal.session.ssh")
    def test_no_tunnel_manager(self, mock_ssh, mock_tmux, _sleep, db):
        """Setup should work without tunnel_manager (tunnel skipped with warning)."""
        seed_all(db)
        mock_ssh.rdev_connect.return_value = True
        mock_ssh.wait_for_prompt.return_value = False  # Let it fail early

        result = setup_rdev_worker(
            db, "session-id-000", "w4", "subs-mt/test",
            "orchestrator", 8093,
            # No tunnel_manager provided
        )

        # Should still attempt setup (tunnel_pid will be None)
        assert result["ok"] is False  # Fails on SSH prompt timeout
