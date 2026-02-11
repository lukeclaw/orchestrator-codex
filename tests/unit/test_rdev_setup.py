"""Tests for rdev worker setup orchestration."""

from unittest.mock import patch, MagicMock, mock_open

from scripts.seed_db import seed_all
from orchestrator.terminal.session import setup_rdev_worker


class TestSetupRdevWorker:
    @patch("random.randint", return_value=12345)  # Predictable markers
    @patch("orchestrator.terminal.markers.time.sleep")  # Mock markers module sleep
    @patch("orchestrator.terminal.session.time.sleep")  # Mock session module sleep
    @patch("orchestrator.terminal.session.tmux")
    @patch("orchestrator.terminal.session.ssh")
    @patch("orchestrator.terminal.session.deploy_worker_scripts")
    @patch("orchestrator.terminal.session.generate_worker_hooks")
    @patch("builtins.open", mock_open(read_data="Worker template SESSION_ID"))
    @patch("orchestrator.terminal.session.os.path.exists", return_value=True)
    @patch("orchestrator.terminal.session.os.makedirs")
    def test_successful_setup(self, _makedirs, _exists, mock_hooks, mock_scripts, mock_ssh, mock_tmux, _sleep_session, _sleep_markers, _randint, db):
        seed_all(db)
        mock_ssh.wait_for_prompt.return_value = True
        mock_tmux.create_window.return_value = "orchestrator:w1-tunnel"
        mock_tmux.send_keys.return_value = True
        # Return markers for both _install_screen_if_needed (SCREEN_CHK) and wait_for_completion (WAIT)
        # With predictable random=12345, all markers use this ID
        mock_tmux.capture_output.return_value = (
            "__SCREEN_CHK_START_12345__\nYES\n__SCREEN_CHK_END_12345__\n"
            "__WAIT_START_12345__\nDONE\n__WAIT_END_12345__"
        )
        mock_ssh.setup_rdev_tunnel.return_value = True
        mock_ssh.rdev_connect.return_value = True
        mock_scripts.return_value = "/tmp/orchestrator/workers/w1/bin"
        mock_hooks.return_value = "/tmp/orchestrator/workers/w1/configs"

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

    @patch("orchestrator.terminal.session.time.sleep")  # Mock sleep to speed up tests
    @patch("orchestrator.terminal.session.tmux")
    @patch("orchestrator.terminal.session.ssh")
    def test_ssh_prompt_timeout(self, mock_ssh, mock_tmux, _sleep, db):
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

    @patch("orchestrator.terminal.session.time.sleep")  # Mock sleep to speed up tests
    @patch("orchestrator.terminal.session.tmux")
    @patch("orchestrator.terminal.session.ssh")
    def test_tunnel_creation_failure(self, mock_ssh, mock_tmux, _sleep, db):
        seed_all(db)
        mock_tmux.create_window.side_effect = RuntimeError("tmux not available")
        mock_tmux.kill_window.return_value = True

        result = setup_rdev_worker(
            db, "session-id-789", "w3", "subs-mt/test",
            "orchestrator", 8093,
        )

        assert result["ok"] is False
        assert "error" in result
