"""Tests for rdev worker setup orchestration."""

from unittest.mock import patch, MagicMock, mock_open

from scripts.seed_db import seed_all
from orchestrator.terminal.session import setup_rdev_worker


class TestSetupRdevWorker:
    @patch("orchestrator.terminal.session.tmux")
    @patch("orchestrator.terminal.session.ssh")
    @patch("builtins.open", mock_open(read_data="Worker template SESSION_ID"))
    @patch("orchestrator.terminal.session.os.path.exists", return_value=True)
    def test_successful_setup(self, _exists, mock_ssh, mock_tmux, db):
        seed_all(db)
        mock_ssh.wait_for_prompt.return_value = True
        mock_tmux.create_window.return_value = "orchestrator:w1-tunnel"
        mock_tmux.send_keys.return_value = True
        mock_ssh.setup_rdev_tunnel.return_value = True
        mock_ssh.rdev_connect.return_value = True

        result = setup_rdev_worker(
            db, "session-id-123", "w1", "subs-mt/sleepy-franklin",
            "orchestrator", 8093,
        )

        assert result["ok"] is True
        assert result["tunnel_window"] == "w1-tunnel"
        mock_tmux.create_window.assert_called_once_with("orchestrator", "w1-tunnel")
        mock_ssh.setup_rdev_tunnel.assert_called_once_with(
            "orchestrator", "w1-tunnel", "subs-mt/sleepy-franklin", 8093, 8093,
        )
        mock_ssh.rdev_connect.assert_called_once_with(
            "orchestrator", "w1", "subs-mt/sleepy-franklin",
        )

    @patch("orchestrator.terminal.session.tmux")
    @patch("orchestrator.terminal.session.ssh")
    def test_ssh_prompt_timeout(self, mock_ssh, mock_tmux, db):
        seed_all(db)
        mock_tmux.create_window.return_value = "orchestrator:w2-tunnel"
        mock_tmux.send_keys.return_value = True
        mock_ssh.setup_rdev_tunnel.return_value = True
        mock_ssh.rdev_connect.return_value = True
        mock_ssh.wait_for_prompt.return_value = False

        result = setup_rdev_worker(
            db, "session-id-456", "w2", "jobs-mt/epic-turing",
            "orchestrator", 8093,
        )

        assert result["ok"] is False
        assert "timeout" in result["error"].lower() or "prompt" in result["error"].lower()
        # Tunnel should be cleaned up
        mock_tmux.kill_window.assert_called_once_with("orchestrator", "w2-tunnel")

    @patch("orchestrator.terminal.session.tmux")
    @patch("orchestrator.terminal.session.ssh")
    def test_tunnel_creation_failure(self, mock_ssh, mock_tmux, db):
        seed_all(db)
        mock_tmux.create_window.side_effect = RuntimeError("tmux not available")
        mock_tmux.kill_window.return_value = True

        result = setup_rdev_worker(
            db, "session-id-789", "w3", "subs-mt/test",
            "orchestrator", 8093,
        )

        assert result["ok"] is False
        assert "error" in result
