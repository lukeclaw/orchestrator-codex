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
    @patch("orchestrator.terminal.session.get_worker_skills_dir", return_value=None)
    def test_successful_setup(self, _skills_dir, _makedirs, _exists, mock_hooks, mock_scripts, mock_ssh, mock_tmux, _sleep_session, _sleep_markers, _randint, db):
        seed_all(db)
        mock_ssh.wait_for_prompt.return_value = True
        mock_tmux.send_keys.return_value = True
        # Return markers for both _install_screen_if_needed (SCREEN_CHK) and wait_for_completion (WAIT)
        # With predictable random=12345, all markers use this ID
        mock_tmux.capture_output.return_value = (
            "__SCREEN_CHK_START_12345__\nYES\n__SCREEN_CHK_END_12345__\n"
            "__WAIT_START_12345__\nDONE\n__WAIT_END_12345__"
        )
        mock_ssh.rdev_connect.return_value = True
        mock_scripts.return_value = "/tmp/orchestrator/workers/w1/bin"
        mock_hooks.return_value = "/tmp/orchestrator/workers/w1/configs"

        # Create mock tunnel_manager
        mock_tunnel_manager = MagicMock()
        mock_tunnel_manager.start_tunnel.return_value = 12345  # PID

        result = setup_rdev_worker(
            db, "session-id-123", "w1", "subs-mt/sleepy-franklin",
            "orchestrator", 8093,
            tunnel_manager=mock_tunnel_manager,
        )

        assert result["ok"] is True
        assert result["tunnel_pid"] == 12345
        mock_tunnel_manager.start_tunnel.assert_called_once_with(
            "session-id-123", "w1", "subs-mt/sleepy-franklin",
        )
        # tmux.create_window should NOT be called for tunnel (no tmux tunnel window)
        # It should still be called for... actually it's not called at all since
        # the worker window is created in the API route, not in setup_rdev_worker
        mock_ssh.rdev_connect.assert_called_once_with(
            "orchestrator", "w1", "subs-mt/sleepy-franklin",
        )

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
