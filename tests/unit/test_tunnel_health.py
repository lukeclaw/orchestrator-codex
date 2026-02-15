"""Tests for tunnel health checking, probing, kill escalation, and periodic monitoring.

Covers:
- SSH keepalive options in tunnel command
- Active tunnel probing (probe_tunnel_connectivity)
- check_tunnel_alive with active probe integration
- check_tunnel_alive false-positive fallback removal
- kill_tunnel_processes SIGKILL escalation
- find_tunnel_pids process discovery
- Periodic tunnel health monitor loop
"""

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from orchestrator.session import tunnel


@pytest.fixture(autouse=True)
def reset_tunnel_cache():
    """Reset tunnel module global cache before and after each test."""
    tunnel._tunnel_cache = {}
    tunnel._cache_timestamp = 0
    yield
    tunnel._tunnel_cache = {}
    tunnel._cache_timestamp = 0


# ==============================================================================
# SSH Keepalive Options
# ==============================================================================


class TestSSHKeepalive:
    """Verify the reverse tunnel command includes keepalive and failure options."""

    @patch("orchestrator.terminal.ssh.send_keys")
    def test_setup_rdev_tunnel_includes_keepalive(self, mock_send_keys):
        """setup_rdev_tunnel should include ServerAliveInterval, ServerAliveCountMax,
        and ExitOnForwardFailure in the SSH command."""
        from orchestrator.terminal.ssh import setup_rdev_tunnel

        setup_rdev_tunnel("sess", "win", "user/rdev-vm", 8093, 8093)

        mock_send_keys.assert_called_once()
        cmd = mock_send_keys.call_args[0][2]

        assert "ServerAliveInterval=30" in cmd
        assert "ServerAliveCountMax=3" in cmd
        assert "ExitOnForwardFailure=yes" in cmd
        assert "-N" in cmd
        assert "-R 8093:127.0.0.1:8093" in cmd
        assert "user/rdev-vm" in cmd

    @patch("orchestrator.terminal.ssh.send_keys")
    def test_setup_rdev_tunnel_still_has_host_key_options(self, mock_send_keys):
        """Should still disable strict host key checking for ephemeral rdev VMs."""
        from orchestrator.terminal.ssh import setup_rdev_tunnel

        setup_rdev_tunnel("sess", "win", "user/rdev-vm", 8093, 8093)

        cmd = mock_send_keys.call_args[0][2]
        assert "StrictHostKeyChecking=no" in cmd
        assert "UserKnownHostsFile=/dev/null" in cmd


# ==============================================================================
# probe_tunnel_connectivity
# ==============================================================================


class TestProbeTunnelConnectivity:
    """Tests for the active tunnel probe that SSHes to remote and curls the API."""

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_true_on_http_200(self, mock_run):
        """Should return True when curl returns HTTP 200."""
        from orchestrator.session.health import probe_tunnel_connectivity

        mock_run.return_value = MagicMock(stdout="200", stderr="", returncode=0)

        assert probe_tunnel_connectivity("user/rdev-vm") is True
        mock_run.assert_called_once()
        # Verify SSH command structure
        args = mock_run.call_args[0][0]
        assert args[0] == "ssh"
        assert "user/rdev-vm" in args
        # BatchMode=yes should appear somewhere in the args
        full_cmd = " ".join(args)
        assert "BatchMode=yes" in full_cmd

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_false_on_http_error(self, mock_run):
        """Should return False when curl returns non-200 status."""
        from orchestrator.session.health import probe_tunnel_connectivity

        mock_run.return_value = MagicMock(stdout="000", stderr="Connection refused", returncode=0)

        assert probe_tunnel_connectivity("user/rdev-vm") is False

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_false_on_timeout(self, mock_run):
        """Should return False when SSH+curl times out."""
        from orchestrator.session.health import probe_tunnel_connectivity
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=8)

        assert probe_tunnel_connectivity("user/rdev-vm") is False

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_false_on_ssh_failure(self, mock_run):
        """Should return False when SSH itself fails."""
        from orchestrator.session.health import probe_tunnel_connectivity

        mock_run.return_value = MagicMock(stdout="", stderr="Connection refused", returncode=255)

        assert probe_tunnel_connectivity("user/rdev-vm") is False

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_false_on_exception(self, mock_run):
        """Should return False on unexpected exceptions."""
        from orchestrator.session.health import probe_tunnel_connectivity

        mock_run.side_effect = OSError("Network error")

        assert probe_tunnel_connectivity("user/rdev-vm") is False

    @patch("orchestrator.session.health.subprocess.run")
    def test_custom_port(self, mock_run):
        """Should use the specified remote port."""
        from orchestrator.session.health import probe_tunnel_connectivity

        mock_run.return_value = MagicMock(stdout="200", stderr="", returncode=0)

        probe_tunnel_connectivity("user/rdev-vm", remote_port=9999)

        args = mock_run.call_args[0][0]
        # The curl command is the last argument to SSH
        curl_cmd = args[-1]
        assert "9999" in curl_cmd

    @patch("orchestrator.session.health.subprocess.run")
    def test_handles_quoted_http_code(self, mock_run):
        """Should handle curl output with quotes around the HTTP code."""
        from orchestrator.session.health import probe_tunnel_connectivity

        # curl -w '%{http_code}' may return with quotes
        mock_run.return_value = MagicMock(stdout="'200'", stderr="", returncode=0)

        assert probe_tunnel_connectivity("user/rdev-vm") is True


# ==============================================================================
# check_tunnel_alive (updated with active probe + no false-positive fallback)
# ==============================================================================


class TestCheckTunnelAlive:
    """Tests for check_tunnel_alive with the updated logic."""

    @patch("orchestrator.session.health.capture_output")
    def test_returns_false_on_no_output(self, mock_capture):
        """Empty output → dead."""
        from orchestrator.session.health import check_tunnel_alive

        mock_capture.return_value = ""
        assert check_tunnel_alive("sess", "win") is False

    @patch("orchestrator.session.health.capture_output")
    def test_returns_false_on_none_output(self, mock_capture):
        """None output → dead."""
        from orchestrator.session.health import check_tunnel_alive

        mock_capture.return_value = None
        assert check_tunnel_alive("sess", "win") is False

    @patch("orchestrator.session.health.capture_output")
    def test_detects_connection_closed(self, mock_capture):
        """Should detect 'Connection closed' error."""
        from orchestrator.session.health import check_tunnel_alive

        mock_capture.return_value = "ssh -N -R ...\nConnection closed by remote host"
        assert check_tunnel_alive("sess", "win") is False

    @patch("orchestrator.session.health.capture_output")
    def test_detects_connection_refused(self, mock_capture):
        """Should detect 'Connection refused' error."""
        from orchestrator.session.health import check_tunnel_alive

        mock_capture.return_value = "ssh: connect to host ...: Connection refused"
        assert check_tunnel_alive("sess", "win") is False

    @patch("orchestrator.session.health.capture_output")
    def test_detects_broken_pipe(self, mock_capture):
        """Should detect 'broken pipe' error."""
        from orchestrator.session.health import check_tunnel_alive

        mock_capture.return_value = "Write failed: Broken pipe"
        assert check_tunnel_alive("sess", "win") is False

    @patch("orchestrator.session.health.capture_output")
    def test_detects_host_key_changed(self, mock_capture):
        """Should detect host key verification failure."""
        from orchestrator.session.health import check_tunnel_alive

        mock_capture.return_value = "@@@@@@@@@\nREMOTE HOST IDENTIFICATION HAS CHANGED\n@@@@@@@@@"
        assert check_tunnel_alive("sess", "win") is False

    @patch("orchestrator.session.health.capture_output")
    def test_detects_shell_prompt(self, mock_capture):
        """Shell prompt at end → tunnel exited back to shell."""
        from orchestrator.session.health import check_tunnel_alive

        mock_capture.return_value = "some output\nuser@host $ "
        assert check_tunnel_alive("sess", "win") is False

    @patch("orchestrator.session.health.capture_output")
    def test_alive_when_ssh_command_visible(self, mock_capture):
        """Should return True when SSH command with -R is visible and no errors."""
        from orchestrator.session.health import check_tunnel_alive

        mock_capture.return_value = "ssh -o StrictHostKeyChecking=no -N -R 8093:127.0.0.1:8093 user/rdev-vm"
        assert check_tunnel_alive("sess", "win") is True

    @patch("orchestrator.session.health.capture_output")
    def test_alive_when_ssh_L_command_visible(self, mock_capture):
        """Should return True when SSH command with -L is visible and no errors."""
        from orchestrator.session.health import check_tunnel_alive

        mock_capture.return_value = "ssh -N -L 4200:localhost:4200 user/rdev-vm"
        assert check_tunnel_alive("sess", "win") is True

    @patch("orchestrator.session.health.probe_tunnel_connectivity")
    @patch("orchestrator.session.health.capture_output")
    def test_inconclusive_with_host_does_active_probe(self, mock_capture, mock_probe):
        """When tmux output is inconclusive and host is provided, should use active probe."""
        from orchestrator.session.health import check_tunnel_alive

        # Output that doesn't match any rule (no ssh command, no error, no prompt)
        mock_capture.return_value = "some random output\nwithout indicators"
        mock_probe.return_value = True

        result = check_tunnel_alive("sess", "win", host="user/rdev-vm")

        assert result is True
        mock_probe.assert_called_once_with("user/rdev-vm", 8093)

    @patch("orchestrator.session.health.probe_tunnel_connectivity")
    @patch("orchestrator.session.health.capture_output")
    def test_inconclusive_with_host_probe_fails(self, mock_capture, mock_probe):
        """When tmux inconclusive and active probe fails, should return False."""
        from orchestrator.session.health import check_tunnel_alive

        mock_capture.return_value = "some random output"
        mock_probe.return_value = False

        result = check_tunnel_alive("sess", "win", host="user/rdev-vm")

        assert result is False
        mock_probe.assert_called_once()

    @patch("orchestrator.session.health.capture_output")
    def test_inconclusive_without_host_returns_false(self, mock_capture):
        """When tmux inconclusive and no host for probing, should return False (fail safe)."""
        from orchestrator.session.health import check_tunnel_alive

        # Output that doesn't match any rule
        mock_capture.return_value = "some random output\nwithout any indicators"

        # No host provided → can't probe → fail safe
        result = check_tunnel_alive("sess", "win")
        assert result is False

    @patch("orchestrator.session.health.capture_output")
    def test_exception_returns_false(self, mock_capture):
        """Should return False on exception."""
        from orchestrator.session.health import check_tunnel_alive

        mock_capture.side_effect = RuntimeError("tmux error")
        assert check_tunnel_alive("sess", "win") is False

    @patch("orchestrator.session.health.probe_tunnel_connectivity")
    @patch("orchestrator.session.health.capture_output")
    def test_custom_remote_port_passed_to_probe(self, mock_capture, mock_probe):
        """Custom remote_port should be passed through to probe."""
        from orchestrator.session.health import check_tunnel_alive

        mock_capture.return_value = "ambiguous output"
        mock_probe.return_value = True

        check_tunnel_alive("sess", "win", host="user/rdev-vm", remote_port=9999)

        mock_probe.assert_called_once_with("user/rdev-vm", 9999)


# ==============================================================================
# find_tunnel_pids
# ==============================================================================


class TestFindTunnelPids:
    """Tests for finding SSH tunnel PIDs by host."""

    @patch("orchestrator.session.health.subprocess.run")
    def test_finds_matching_pids(self, mock_run):
        """Should find PIDs of SSH -N -R tunnel processes for the host."""
        from orchestrator.session.health import find_tunnel_pids

        ps_output = (
            "USER       PID  %CPU %MEM COMMAND\n"
            "yuqiu    11111   0.0  0.0 ssh -o StrictHostKeyChecking=no -N -R 8093:127.0.0.1:8093 user/rdev-vm\n"
            "yuqiu    22222   0.0  0.0 ssh -N -L 4200:localhost:4200 user/rdev-vm\n"  # -L not -R
            "yuqiu    33333   0.0  0.0 ssh -o Foo=bar -N -R 9093:127.0.0.1:9093 other/host\n"  # different host
        )
        mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)

        pids = find_tunnel_pids("user/rdev-vm")

        assert 11111 in pids
        assert 22222 not in pids  # -L tunnel, not -R
        assert 33333 not in pids  # different host

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_empty_on_no_matches(self, mock_run):
        """Should return empty list if no tunnel processes found."""
        from orchestrator.session.health import find_tunnel_pids

        mock_run.return_value = MagicMock(stdout="USER PID COMMAND\n", returncode=0)

        assert find_tunnel_pids("user/rdev-vm") == []

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_empty_on_timeout(self, mock_run):
        """Should return empty list on subprocess timeout."""
        from orchestrator.session.health import find_tunnel_pids
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ps", timeout=5)

        assert find_tunnel_pids("user/rdev-vm") == []

    @patch("orchestrator.session.health.subprocess.run")
    def test_skips_grep_lines(self, mock_run):
        """Should skip grep processes in ps output."""
        from orchestrator.session.health import find_tunnel_pids

        ps_output = (
            "yuqiu    11111   0.0  0.0 ssh -N -R 8093:127.0.0.1:8093 user/rdev-vm\n"
            "yuqiu    99999   0.0  0.0 grep ssh -N -R user/rdev-vm\n"
        )
        mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)

        pids = find_tunnel_pids("user/rdev-vm")

        assert 11111 in pids
        assert 99999 not in pids


# ==============================================================================
# kill_tunnel_processes (SIGKILL escalation)
# ==============================================================================


class TestKillTunnelProcesses:
    """Tests for the robust kill with SIGTERM → SIGKILL escalation."""

    @patch("orchestrator.session.health.time.sleep")
    @patch("orchestrator.session.health._is_pid_alive")
    @patch("orchestrator.session.health.os.kill")
    @patch("orchestrator.session.health.find_tunnel_pids")
    def test_kills_with_sigterm(self, mock_find, mock_kill, mock_alive, mock_sleep):
        """Should send SIGTERM first and succeed if process exits."""
        from orchestrator.session.health import kill_tunnel_processes

        mock_find.return_value = [1234]
        # Process dies after SIGTERM (not alive on first check)
        mock_alive.return_value = False

        result = kill_tunnel_processes("user/rdev-vm", graceful_timeout=1.0)

        assert result == 1
        mock_kill.assert_called_once_with(1234, signal.SIGTERM)

    @patch("orchestrator.session.health.time.sleep")
    @patch("orchestrator.session.health._is_pid_alive")
    @patch("orchestrator.session.health.os.kill")
    @patch("orchestrator.session.health.find_tunnel_pids")
    def test_escalates_to_sigkill(self, mock_find, mock_kill, mock_alive, mock_sleep):
        """Should escalate to SIGKILL if process survives SIGTERM."""
        from orchestrator.session.health import kill_tunnel_processes

        mock_find.return_value = [5678]
        # Process stays alive through all checks (SIGTERM doesn't work)
        mock_alive.return_value = True
        # Use short timeout so we don't loop many times
        mock_sleep.side_effect = lambda _: None

        result = kill_tunnel_processes("user/rdev-vm", graceful_timeout=0.1)

        assert result == 1
        # Should have sent SIGTERM then SIGKILL
        kill_calls = mock_kill.call_args_list
        assert call(5678, signal.SIGTERM) in kill_calls
        assert call(5678, signal.SIGKILL) in kill_calls

    @patch("orchestrator.session.health.find_tunnel_pids")
    def test_returns_zero_when_no_processes(self, mock_find):
        """Should return 0 when no tunnel processes found."""
        from orchestrator.session.health import kill_tunnel_processes

        mock_find.return_value = []

        assert kill_tunnel_processes("user/rdev-vm") == 0

    @patch("orchestrator.session.health.time.sleep")
    @patch("orchestrator.session.health._is_pid_alive")
    @patch("orchestrator.session.health.os.kill")
    @patch("orchestrator.session.health.find_tunnel_pids")
    def test_handles_multiple_processes(self, mock_find, mock_kill, mock_alive, mock_sleep):
        """Should kill all matching tunnel processes."""
        from orchestrator.session.health import kill_tunnel_processes

        mock_find.return_value = [1111, 2222]
        mock_alive.return_value = False  # Both die after SIGTERM

        result = kill_tunnel_processes("user/rdev-vm", graceful_timeout=1.0)

        assert result == 2
        assert call(1111, signal.SIGTERM) in mock_kill.call_args_list
        assert call(2222, signal.SIGTERM) in mock_kill.call_args_list

    @patch("orchestrator.session.health.time.sleep")
    @patch("orchestrator.session.health._is_pid_alive")
    @patch("orchestrator.session.health.os.kill")
    @patch("orchestrator.session.health.find_tunnel_pids")
    def test_handles_process_lookup_error(self, mock_find, mock_kill, mock_alive, mock_sleep):
        """Should handle ProcessLookupError (process already dead)."""
        from orchestrator.session.health import kill_tunnel_processes

        mock_find.return_value = [1234]
        mock_kill.side_effect = ProcessLookupError
        mock_alive.return_value = False

        # Should not raise
        result = kill_tunnel_processes("user/rdev-vm", graceful_timeout=0.1)
        assert result == 1


# ==============================================================================
# Periodic tunnel health monitor
# ==============================================================================


class TestTunnelHealthLoop:
    """Tests for the periodic tunnel health monitoring loop (subprocess-based)."""

    @patch("orchestrator.session.tunnel_monitor.sessions_repo")
    def test_check_all_tunnels_skips_non_rdev(self, mock_repo):
        """Should skip local (non-rdev) workers."""
        from orchestrator.session.tunnel_monitor import _check_all_tunnels

        mock_session = MagicMock()
        mock_session.host = "local"
        mock_session.status = "waiting"
        mock_session.id = "sess-1"
        mock_repo.list_sessions.return_value = [mock_session]

        mock_tm = MagicMock()

        asyncio.run(_check_all_tunnels(MagicMock(), mock_tm))

        mock_tm.is_alive.assert_not_called()

    @patch("orchestrator.session.tunnel_monitor.sessions_repo")
    def test_check_all_tunnels_skips_disconnected(self, mock_repo):
        """Should skip disconnected workers."""
        from orchestrator.session.tunnel_monitor import _check_all_tunnels

        mock_session = MagicMock()
        mock_session.host = "user/rdev-vm"
        mock_session.status = "disconnected"
        mock_session.id = "sess-1"
        mock_repo.list_sessions.return_value = [mock_session]

        mock_tm = MagicMock()

        asyncio.run(_check_all_tunnels(MagicMock(), mock_tm))

        mock_tm.is_alive.assert_not_called()

    @patch("orchestrator.session.tunnel_monitor.sessions_repo")
    def test_check_all_tunnels_skips_connecting(self, mock_repo):
        """Should skip workers in connecting state."""
        from orchestrator.session.tunnel_monitor import _check_all_tunnels

        mock_session = MagicMock()
        mock_session.host = "user/rdev-vm"
        mock_session.status = "connecting"
        mock_session.id = "sess-1"
        mock_repo.list_sessions.return_value = [mock_session]

        mock_tm = MagicMock()

        asyncio.run(_check_all_tunnels(MagicMock(), mock_tm))

        mock_tm.is_alive.assert_not_called()

    @patch("orchestrator.session.tunnel_monitor.sessions_repo")
    def test_check_all_tunnels_skips_alive(self, mock_repo):
        """Should not restart when tunnel is alive."""
        from orchestrator.session.tunnel_monitor import _check_all_tunnels

        mock_session = MagicMock()
        mock_session.host = "user/rdev-vm"
        mock_session.status = "waiting"
        mock_session.name = "w1"
        mock_session.id = "sess-1"
        mock_repo.list_sessions.return_value = [mock_session]

        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = True

        asyncio.run(_check_all_tunnels(MagicMock(), mock_tm))

        mock_tm.is_alive.assert_called_once_with("sess-1")
        mock_tm.restart_tunnel.assert_not_called()

    @patch("orchestrator.session.tunnel_monitor.sessions_repo")
    def test_check_all_tunnels_restarts_dead(self, mock_repo):
        """Should restart tunnel via tunnel_manager when dead."""
        from orchestrator.session.tunnel_monitor import _check_all_tunnels

        mock_session = MagicMock()
        mock_session.host = "user/rdev-vm"
        mock_session.status = "waiting"
        mock_session.name = "w1"
        mock_session.id = "sess-1"
        mock_repo.list_sessions.return_value = [mock_session]

        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = False
        mock_tm.restart_tunnel.return_value = 99999

        asyncio.run(_check_all_tunnels(MagicMock(), mock_tm))

        mock_tm.is_alive.assert_called_once_with("sess-1")
        mock_tm.restart_tunnel.assert_called_once_with("sess-1", "w1", "user/rdev-vm")

    @patch("orchestrator.session.tunnel_monitor.sessions_repo")
    def test_check_all_tunnels_updates_db_on_restart(self, mock_repo):
        """Should update tunnel_pid in DB after successful restart."""
        from orchestrator.session.tunnel_monitor import _check_all_tunnels

        mock_conn = MagicMock()
        mock_session = MagicMock()
        mock_session.host = "user/rdev-vm"
        mock_session.status = "idle"
        mock_session.name = "w1"
        mock_session.id = "sess-1"
        mock_repo.list_sessions.return_value = [mock_session]

        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = False
        mock_tm.restart_tunnel.return_value = 12345

        asyncio.run(_check_all_tunnels(mock_conn, mock_tm))

        mock_repo.update_session.assert_called_once_with(mock_conn, "sess-1", tunnel_pid=12345)

    @patch("orchestrator.session.tunnel_monitor.sessions_repo")
    def test_check_all_tunnels_no_db_update_on_restart_failure(self, mock_repo):
        """Should not update DB when restart returns None."""
        from orchestrator.session.tunnel_monitor import _check_all_tunnels

        mock_session = MagicMock()
        mock_session.host = "user/rdev-vm"
        mock_session.status = "waiting"
        mock_session.name = "w1"
        mock_session.id = "sess-1"
        mock_repo.list_sessions.return_value = [mock_session]

        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = False
        mock_tm.restart_tunnel.return_value = None

        asyncio.run(_check_all_tunnels(MagicMock(), mock_tm))

        mock_repo.update_session.assert_not_called()


# ==============================================================================
# Integration: reconnect_tunnel_only uses ReverseTunnelManager
# ==============================================================================


class TestReconnectTunnelOnlyKillEscalation:
    """Verify reconnect_tunnel_only uses tunnel_manager for subprocess-based restart."""

    def test_restart_via_tunnel_manager(self, db):
        """Should call tunnel_manager.restart_tunnel and update DB on success."""
        from orchestrator.session.reconnect import reconnect_tunnel_only

        mock_session = MagicMock()
        mock_session.name = "w1"
        mock_session.host = "user/rdev-vm"
        mock_session.id = "sess-123"

        mock_repo = MagicMock()
        mock_tm = MagicMock()
        mock_tm.restart_tunnel.return_value = 55555

        result = reconnect_tunnel_only(db, mock_session, "orchestrator", 8093, mock_repo, tunnel_manager=mock_tm)

        assert result is True
        mock_tm.restart_tunnel.assert_called_once_with("sess-123", "w1", "user/rdev-vm")
        mock_repo.update_session.assert_called_once_with(db, "sess-123", tunnel_pid=55555)
