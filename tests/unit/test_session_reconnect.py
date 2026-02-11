"""Unit tests for session reconnect flows.

Tests cover all reconnect scenarios:
1. Tunnel alive, SSH at bash, Screen exists, Claude running → Reattach
2. Tunnel alive, SSH at bash, Screen exists, Claude dead → Kill screen, create new
3. Tunnel alive, SSH at bash, No screen → Create new screen
4. Tunnel alive, SSH inside screen → Detach first, then handle
5. Tunnel alive, SSH on local (not connected) → SSH reconnect
6. Tunnel dead → Recreate tunnel first
"""

import pytest
from unittest.mock import patch, MagicMock, call


class TestReconnectValidation:
    """Test that reconnect only works from valid states."""

    @patch('orchestrator.api.routes.sessions.repo')
    def test_reconnect_only_from_disconnected_state(self, mock_repo, db):
        """Reconnect should be allowed from 'disconnected' status."""
        from orchestrator.api.routes.sessions import reconnect_session
        
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.status = "disconnected"
        mock_session.host = "localhost"
        mock_session.tmux_window = "orchestrator:test-worker"
        mock_repo.get_session.return_value = mock_session
        
        mock_request = MagicMock()
        mock_request.app.state.config = {"tmux_session_name": "orchestrator", "api_port": 8093}
        
        with patch('orchestrator.api.routes.sessions.reconnect_local_worker'):
            with patch('orchestrator.api.routes.sessions.is_rdev_host', return_value=False):
                result = reconnect_session("test-session-id", mock_request, db=db)
        
        assert result.get("ok") is not False or "not in reconnectable state" not in str(result.get("error", ""))

    @patch('orchestrator.api.routes.sessions.repo')
    def test_reconnect_only_from_screen_detached_state(self, mock_repo, db):
        """Reconnect should be allowed from 'screen_detached' status."""
        from orchestrator.api.routes.sessions import reconnect_session
        
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.status = "screen_detached"
        mock_session.host = "localhost"
        mock_session.tmux_window = "orchestrator:test-worker"
        mock_repo.get_session.return_value = mock_session
        
        mock_request = MagicMock()
        mock_request.app.state.config = {"tmux_session_name": "orchestrator", "api_port": 8093}
        
        with patch('orchestrator.api.routes.sessions.reconnect_local_worker'):
            with patch('orchestrator.api.routes.sessions.is_rdev_host', return_value=False):
                result = reconnect_session("test-session-id", mock_request, db=db)
        
        assert result.get("ok") is not False or "not in reconnectable state" not in str(result.get("error", ""))

    @patch('orchestrator.api.routes.sessions.repo')
    def test_reconnect_only_from_error_state(self, mock_repo, db):
        """Reconnect should be allowed from 'error' status."""
        from orchestrator.api.routes.sessions import reconnect_session
        
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.status = "error"
        mock_session.host = "localhost"
        mock_session.tmux_window = "orchestrator:test-worker"
        mock_repo.get_session.return_value = mock_session
        
        mock_request = MagicMock()
        mock_request.app.state.config = {"tmux_session_name": "orchestrator", "api_port": 8093}
        
        with patch('orchestrator.api.routes.sessions.reconnect_local_worker'):
            with patch('orchestrator.api.routes.sessions.is_rdev_host', return_value=False):
                result = reconnect_session("test-session-id", mock_request, db=db)
        
        assert result.get("ok") is not False or "not in reconnectable state" not in str(result.get("error", ""))

    @patch('orchestrator.api.routes.sessions.repo')
    def test_reconnect_rejects_working_session(self, mock_repo, db):
        """Reconnect should be rejected for 'working' status."""
        from orchestrator.api.routes.sessions import reconnect_session
        
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.status = "working"
        mock_session.host = "localhost"
        mock_repo.get_session.return_value = mock_session
        
        mock_request = MagicMock()
        mock_request.app.state.config = {"tmux_session_name": "orchestrator", "api_port": 8093}
        
        result = reconnect_session("test-session-id", mock_request, db=db)
        
        assert result.get("ok") == False
        assert "not in reconnectable state" in result.get("error", "")

    @patch('orchestrator.api.routes.sessions.repo')
    def test_reconnect_rejects_connecting_session(self, mock_repo, db):
        """Reconnect should be rejected for 'connecting' status."""
        from orchestrator.api.routes.sessions import reconnect_session
        
        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.status = "connecting"
        mock_session.host = "localhost"
        mock_repo.get_session.return_value = mock_session
        
        mock_request = MagicMock()
        mock_request.app.state.config = {"tmux_session_name": "orchestrator", "api_port": 8093}
        
        result = reconnect_session("test-session-id", mock_request, db=db)
        
        assert result.get("ok") == False
        assert "not in reconnectable state" in result.get("error", "")


class TestReconnectModuleExports:
    """Test that reconnect module exports all expected functions."""

    def test_reconnect_functions_exported(self):
        """Verify all reconnect functions are exported from session module."""
        from orchestrator.session import (
            reconnect_rdev_worker,
            reconnect_local_worker,
            check_ssh_alive,
            check_inside_screen,
            detach_from_screen,
            check_screen_exists_via_tmux,
            build_system_prompt,
            parse_hostname_from_output,
        )
        assert callable(reconnect_rdev_worker)
        assert callable(reconnect_local_worker)
        assert callable(check_ssh_alive)
        assert callable(check_inside_screen)
        assert callable(detach_from_screen)
        assert callable(check_screen_exists_via_tmux)
        assert callable(build_system_prompt)
        assert callable(parse_hostname_from_output)


# =============================================================================
# Scenario Tests for reconnect_rdev_worker
# Each test mocks all dependencies to run fast (<0.1s each)
# =============================================================================

class TestScenario1_TunnelAlive_SSHOk_ScreenWithClaude:
    """Scenario 1: Best case - everything alive, just reattach."""

    @patch('orchestrator.session.reconnect.send_keys')
    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.check_tunnel_alive', return_value=True)
    @patch('orchestrator.session.reconnect.check_ssh_alive', return_value=True)
    @patch('orchestrator.session.reconnect.check_inside_screen', return_value=False)
    @patch('orchestrator.session.reconnect.check_screen_exists_via_tmux', return_value=(True, True))
    @patch('orchestrator.terminal.session._install_screen_if_needed', return_value=True)
    def test_reattaches_to_existing_screen(
        self, mock_install, mock_screen_check, mock_inside, mock_ssh, mock_tunnel, 
        mock_capture, mock_send, db
    ):
        """Should reattach to screen without creating new one."""
        from orchestrator.session.reconnect import reconnect_rdev_worker
        
        mock_repo = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.tunnel_pane = "orchestrator:test-rdev-tunnel"
        mock_session.work_dir = "/home/user/project"
        mock_capture.return_value = "__SYNC_REATTACH_12345__"
        
        reconnect_rdev_worker(db, mock_session, "orchestrator", "test-rdev", 8093, "/tmp/test", mock_repo)
        
        # Should reattach to screen
        send_calls = [str(c) for c in mock_send.call_args_list]
        assert any('screen -r' in c for c in send_calls), "Should send screen -r to reattach"
        
        # Should NOT create new screen
        assert not any('screen -S' in c for c in send_calls), "Should NOT create new screen"
        
        # Should set status to waiting
        mock_repo.update_session.assert_called()
        update_call = str(mock_repo.update_session.call_args)
        assert "waiting" in update_call


class TestScenario2_TunnelAlive_SSHOk_ScreenWithoutClaude:
    """Scenario 2: Screen exists but Claude crashed - kill and recreate."""

    @patch('orchestrator.session.reconnect.send_keys')
    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.check_tunnel_alive', return_value=True)
    @patch('orchestrator.session.reconnect.check_ssh_alive', return_value=True)
    @patch('orchestrator.session.reconnect.check_inside_screen', return_value=False)
    @patch('orchestrator.session.reconnect.check_screen_exists_via_tmux', return_value=(True, False))
    @patch('orchestrator.terminal.session._install_screen_if_needed', return_value=True)
    @patch('orchestrator.session.reconnect.build_system_prompt', return_value=None)
    def test_kills_stale_screen_and_creates_new(
        self, mock_prompt, mock_install, mock_screen_check, mock_inside, mock_ssh, 
        mock_tunnel, mock_capture, mock_send, db
    ):
        """Should kill stale screen and create new one with Claude."""
        from orchestrator.session.reconnect import reconnect_rdev_worker
        
        mock_repo = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.tunnel_pane = "orchestrator:test-rdev-tunnel"
        mock_session.work_dir = "/home/user/project"
        
        reconnect_rdev_worker(db, mock_session, "orchestrator", "test-rdev", 8093, "/tmp/test", mock_repo)
        
        send_calls = [str(c) for c in mock_send.call_args_list]
        
        # Should kill old screen
        assert any('screen -X' in c and 'quit' in c for c in send_calls), "Should kill stale screen"
        
        # Should create new screen
        assert any('screen -S' in c for c in send_calls), "Should create new screen"
        
        # Should launch Claude
        assert any('claude' in c.lower() for c in send_calls), "Should launch Claude"


class TestScenario3_TunnelAlive_SSHOk_NoScreen:
    """Scenario 3: No screen exists - create new one."""

    @patch('orchestrator.session.reconnect.send_keys')
    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.check_tunnel_alive', return_value=True)
    @patch('orchestrator.session.reconnect.check_ssh_alive', return_value=True)
    @patch('orchestrator.session.reconnect.check_inside_screen', return_value=False)
    @patch('orchestrator.session.reconnect.check_screen_exists_via_tmux', return_value=(False, False))
    @patch('orchestrator.terminal.session._install_screen_if_needed', return_value=True)
    @patch('orchestrator.session.reconnect.build_system_prompt', return_value=None)
    def test_creates_new_screen_and_launches_claude(
        self, mock_prompt, mock_install, mock_screen_check, mock_inside, mock_ssh, 
        mock_tunnel, mock_capture, mock_send, db
    ):
        """Should create new screen and launch Claude."""
        from orchestrator.session.reconnect import reconnect_rdev_worker
        
        mock_repo = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.tunnel_pane = "orchestrator:test-rdev-tunnel"
        mock_session.work_dir = "/home/user/project"
        
        reconnect_rdev_worker(db, mock_session, "orchestrator", "test-rdev", 8093, "/tmp/test", mock_repo)
        
        send_calls = [str(c) for c in mock_send.call_args_list]
        
        # Should NOT try to kill screen (none exists)
        assert not any('screen -X' in c and 'quit' in c for c in send_calls)
        
        # Should create new screen
        assert any('screen -S' in c for c in send_calls), "Should create new screen"
        
        # Should launch Claude
        assert any('claude' in c.lower() for c in send_calls), "Should launch Claude"


class TestScenario4_TunnelAlive_InsideScreen:
    """Scenario 4: Currently inside screen - must detach first."""

    @patch('orchestrator.session.reconnect.send_keys')
    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.check_tunnel_alive', return_value=True)
    @patch('orchestrator.session.reconnect.check_ssh_alive', return_value=True)
    @patch('orchestrator.session.reconnect.check_inside_screen', return_value=True)
    @patch('orchestrator.session.reconnect.detach_from_screen')
    @patch('orchestrator.session.reconnect.check_screen_exists_via_tmux', return_value=(True, True))
    @patch('orchestrator.terminal.session._install_screen_if_needed', return_value=True)
    def test_detaches_before_reattaching(
        self, mock_install, mock_screen_check, mock_detach, mock_inside, mock_ssh,
        mock_tunnel, mock_capture, mock_send, db
    ):
        """Should call detach_from_screen when inside screen."""
        from orchestrator.session.reconnect import reconnect_rdev_worker
        
        mock_repo = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.tunnel_pane = "orchestrator:test-rdev-tunnel"
        mock_session.work_dir = "/home/user/project"
        mock_capture.return_value = "__SYNC_REATTACH_12345__"
        
        reconnect_rdev_worker(db, mock_session, "orchestrator", "test-rdev", 8093, "/tmp/test", mock_repo)
        
        # Should have called detach
        mock_detach.assert_called_once()


class TestScenario5_TunnelAlive_SSHNotConnected:
    """Scenario 5: SSH not connected to rdev - need to reconnect."""

    @patch('orchestrator.session.reconnect.send_keys')
    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.check_tunnel_alive', return_value=True)
    @patch('orchestrator.session.reconnect.check_ssh_alive')
    @patch('orchestrator.session.reconnect.check_inside_screen', return_value=False)
    @patch('orchestrator.session.reconnect.check_screen_exists_via_tmux', return_value=(True, True))
    @patch('orchestrator.terminal.session._install_screen_if_needed', return_value=True)
    @patch('orchestrator.terminal.ssh.rdev_connect')
    @patch('orchestrator.terminal.ssh.wait_for_prompt', return_value=True)
    def test_reconnects_ssh_when_not_connected(
        self, mock_wait, mock_connect, mock_install, mock_screen_check, mock_inside, 
        mock_ssh, mock_tunnel, mock_capture, mock_send, db
    ):
        """Should reconnect SSH when hostname check fails."""
        from orchestrator.session.reconnect import reconnect_rdev_worker
        
        # First call returns False (not on rdev), second returns True (verified)
        mock_ssh.side_effect = [False, True]
        
        mock_repo = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.tunnel_pane = "orchestrator:test-rdev-tunnel"
        mock_session.work_dir = "/home/user/project"
        mock_capture.return_value = "__SYNC_REATTACH_12345__"
        
        reconnect_rdev_worker(db, mock_session, "orchestrator", "test-rdev", 8093, "/tmp/test", mock_repo)
        
        # Should have called rdev_connect to reconnect SSH
        mock_connect.assert_called_once()
        
        # Should have waited for prompt
        mock_wait.assert_called_once()


class TestScenario6_TunnelDead:
    """Scenario 6: Tunnel dead - need to recreate."""

    @patch('orchestrator.session.reconnect.send_keys')
    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.check_tunnel_alive', return_value=False)
    @patch('orchestrator.session.reconnect.kill_window')
    @patch('orchestrator.terminal.manager.create_window')
    @patch('orchestrator.terminal.ssh.setup_rdev_tunnel')
    @patch('orchestrator.session.reconnect.check_ssh_alive', return_value=True)
    @patch('orchestrator.session.reconnect.check_inside_screen', return_value=False)
    @patch('orchestrator.session.reconnect.check_screen_exists_via_tmux', return_value=(True, True))
    @patch('orchestrator.terminal.session._install_screen_if_needed', return_value=True)
    def test_recreates_tunnel_when_dead(
        self, mock_install, mock_screen_check, mock_inside, mock_ssh,
        mock_setup_tunnel, mock_create_window, mock_kill, mock_tunnel,
        mock_capture, mock_send, db
    ):
        """Should recreate tunnel when it's dead."""
        from orchestrator.session.reconnect import reconnect_rdev_worker
        
        mock_repo = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.tunnel_pane = "orchestrator:test-rdev-tunnel"
        mock_session.work_dir = "/home/user/project"
        mock_capture.return_value = "__SYNC_REATTACH_12345__"
        
        reconnect_rdev_worker(db, mock_session, "orchestrator", "test-rdev", 8093, "/tmp/test", mock_repo)
        
        # Should kill old tunnel window
        mock_kill.assert_called()
        
        # Should create new tunnel window
        mock_create_window.assert_called()
        
        # Should setup new tunnel
        mock_setup_tunnel.assert_called_once()
        
        # Should update tunnel_pane in DB
        update_calls = [str(c) for c in mock_repo.update_session.call_args_list]
        assert any('tunnel_pane' in c for c in update_calls)


class TestScenario7_NoTunnelPaneStored:
    """Scenario 7: No tunnel_pane in DB - create new tunnel."""

    @patch('orchestrator.session.reconnect.send_keys')
    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.check_tunnel_alive', return_value=False)
    @patch('orchestrator.session.reconnect.kill_window')
    @patch('orchestrator.terminal.manager.create_window')
    @patch('orchestrator.terminal.ssh.setup_rdev_tunnel')
    @patch('orchestrator.session.reconnect.check_ssh_alive', return_value=True)
    @patch('orchestrator.session.reconnect.check_inside_screen', return_value=False)
    @patch('orchestrator.session.reconnect.check_screen_exists_via_tmux', return_value=(False, False))
    @patch('orchestrator.terminal.session._install_screen_if_needed', return_value=True)
    @patch('orchestrator.session.reconnect.build_system_prompt', return_value=None)
    def test_creates_tunnel_when_none_stored(
        self, mock_prompt, mock_install, mock_screen_check, mock_inside, mock_ssh,
        mock_setup_tunnel, mock_create_window, mock_kill, mock_tunnel,
        mock_capture, mock_send, db
    ):
        """Should create tunnel when tunnel_pane is None."""
        from orchestrator.session.reconnect import reconnect_rdev_worker
        
        mock_repo = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.tunnel_pane = None  # No tunnel stored
        mock_session.work_dir = "/home/user/project"
        
        reconnect_rdev_worker(db, mock_session, "orchestrator", "test-rdev", 8093, "/tmp/test", mock_repo)
        
        # Should NOT try to kill (no old tunnel)
        mock_kill.assert_not_called()
        
        # Should create new tunnel
        mock_create_window.assert_called()
        mock_setup_tunnel.assert_called_once()


class TestScenario8_SSHReconnectFailure:
    """Scenario 8: SSH reconnect fails - should raise error."""

    @patch('orchestrator.session.reconnect.send_keys')
    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.check_tunnel_alive', return_value=True)
    @patch('orchestrator.session.reconnect.check_ssh_alive', return_value=False)
    @patch('orchestrator.terminal.ssh.rdev_connect')
    @patch('orchestrator.terminal.ssh.wait_for_prompt', return_value=True)
    def test_raises_on_ssh_verification_failure(
        self, mock_wait, mock_connect, mock_ssh, mock_tunnel, mock_capture, mock_send, db
    ):
        """Should raise RuntimeError when SSH reconnect fails verification."""
        from orchestrator.session.reconnect import reconnect_rdev_worker
        
        mock_repo = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.tunnel_pane = "orchestrator:test-rdev-tunnel"
        mock_session.work_dir = "/home/user/project"
        
        with pytest.raises(RuntimeError, match="SSH reconnect.*failed verification"):
            reconnect_rdev_worker(db, mock_session, "orchestrator", "test-rdev", 8093, "/tmp/test", mock_repo)


class TestScenario9_WaitForPromptTimeout:
    """Scenario 9: SSH prompt timeout - should raise error."""

    @patch('orchestrator.session.reconnect.send_keys')
    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.check_tunnel_alive', return_value=True)
    @patch('orchestrator.session.reconnect.check_ssh_alive', return_value=False)
    @patch('orchestrator.terminal.ssh.rdev_connect')
    @patch('orchestrator.terminal.ssh.wait_for_prompt', return_value=False)
    def test_raises_on_prompt_timeout(
        self, mock_wait, mock_connect, mock_ssh, mock_tunnel, mock_capture, mock_send, db
    ):
        """Should raise RuntimeError when waiting for prompt times out."""
        from orchestrator.session.reconnect import reconnect_rdev_worker
        
        mock_repo = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.tunnel_pane = "orchestrator:test-rdev-tunnel"
        mock_session.work_dir = "/home/user/project"
        
        with pytest.raises(RuntimeError, match="Timed out waiting for shell prompt"):
            reconnect_rdev_worker(db, mock_session, "orchestrator", "test-rdev", 8093, "/tmp/test", mock_repo)


class TestStatusTransitionsOnReconnect:
    """Test that status is always set to 'waiting' on successful reconnect."""

    @patch('orchestrator.session.reconnect.send_keys')
    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.check_tunnel_alive', return_value=True)
    @patch('orchestrator.session.reconnect.check_ssh_alive', return_value=True)
    @patch('orchestrator.session.reconnect.check_inside_screen', return_value=False)
    @patch('orchestrator.session.reconnect.check_screen_exists_via_tmux')
    @patch('orchestrator.terminal.session._install_screen_if_needed', return_value=True)
    @patch('orchestrator.session.reconnect.build_system_prompt', return_value=None)
    def test_status_waiting_on_reattach(
        self, mock_prompt, mock_install, mock_screen_check, mock_inside, mock_ssh, 
        mock_tunnel, mock_capture, mock_send, db
    ):
        """Status should be 'waiting' when reattaching to existing screen."""
        from orchestrator.session.reconnect import reconnect_rdev_worker
        
        mock_screen_check.return_value = (True, True)  # Screen + Claude alive
        mock_capture.return_value = "__SYNC_REATTACH_12345__"
        
        mock_repo = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.tunnel_pane = "orchestrator:test-rdev-tunnel"
        mock_session.work_dir = "/home/user/project"
        
        reconnect_rdev_worker(db, mock_session, "orchestrator", "test-rdev", 8093, "/tmp/test", mock_repo)
        
        # Final status should be 'waiting'
        last_update = mock_repo.update_session.call_args_list[-1]
        assert "waiting" in str(last_update)

    @patch('orchestrator.session.reconnect.send_keys')
    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.check_tunnel_alive', return_value=True)
    @patch('orchestrator.session.reconnect.check_ssh_alive', return_value=True)
    @patch('orchestrator.session.reconnect.check_inside_screen', return_value=False)
    @patch('orchestrator.session.reconnect.check_screen_exists_via_tmux')
    @patch('orchestrator.terminal.session._install_screen_if_needed', return_value=True)
    @patch('orchestrator.session.reconnect.build_system_prompt', return_value=None)
    def test_status_waiting_on_new_screen(
        self, mock_prompt, mock_install, mock_screen_check, mock_inside, mock_ssh, 
        mock_tunnel, mock_capture, mock_send, db
    ):
        """Status should be 'waiting' when creating new screen."""
        from orchestrator.session.reconnect import reconnect_rdev_worker
        
        mock_screen_check.return_value = (False, False)  # No screen
        
        mock_repo = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-rdev"
        mock_session.host = "user/rdev-vm"
        mock_session.tunnel_pane = "orchestrator:test-rdev-tunnel"
        mock_session.work_dir = "/home/user/project"
        
        reconnect_rdev_worker(db, mock_session, "orchestrator", "test-rdev", 8093, "/tmp/test", mock_repo)
        
        # Final status should be 'waiting'
        last_update = mock_repo.update_session.call_args_list[-1]
        assert "waiting" in str(last_update)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
