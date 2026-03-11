"""Unit tests for SSH tunnel management.

Tests the tunnel discovery, creation, and cleanup functionality.
Uses mocking to avoid actual subprocess calls for fast execution.
"""

import signal
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.session import tunnel


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset tunnel cache before each test."""
    tunnel._tunnel_cache = {}
    tunnel._cache_timestamp = 0
    yield
    tunnel._tunnel_cache = {}
    tunnel._cache_timestamp = 0


class TestDiscoverActiveTunnels:
    """Tests for discover_active_tunnels()."""

    def test_discovers_single_tunnel(self):
        """Should discover a single SSH tunnel from ps output."""
        ps_output = (
            "USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND\n"
            "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm\n"  # noqa: E501
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)

            tunnels = tunnel.discover_active_tunnels(force_refresh=True)

            assert 4200 in tunnels
            assert tunnels[4200]["pid"] == 12345
            assert tunnels[4200]["remote_port"] == 4200
            assert tunnels[4200]["host"] == "user/rdev-vm"

    def test_discovers_multiple_tunnels(self):
        """Should discover multiple SSH tunnels."""
        ps_output = (
            "USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND\n"
            "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm\n"  # noqa: E501
            "yuqiu    12346   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 3000:localhost:3000 other/rdev-host\n"  # noqa: E501
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)

            tunnels = tunnel.discover_active_tunnels(force_refresh=True)

            assert len(tunnels) == 2
            assert 4200 in tunnels
            assert 3000 in tunnels

    def test_ignores_non_tunnel_ssh(self):
        """Should ignore SSH processes that aren't tunnels."""
        ps_output = (
            "USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND\n"
            "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh user@host\n"  # noqa: E501
            "yuqiu    12346   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm\n"  # noqa: E501
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)

            tunnels = tunnel.discover_active_tunnels(force_refresh=True)

            assert len(tunnels) == 1
            assert 4200 in tunnels

    def test_ignores_grep_processes(self):
        """Should ignore grep processes in ps output."""
        ps_output = (
            "USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND\n"
            "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 grep ssh -N -L\n"  # noqa: E501
            "yuqiu    12346   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm\n"  # noqa: E501
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)

            tunnels = tunnel.discover_active_tunnels(force_refresh=True)

            assert len(tunnels) == 1
            assert 4200 in tunnels

    def test_empty_ps_output(self):
        """Should return empty dict when no tunnels found."""
        ps_output = (
            "USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND\n"
            "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 vim file.py\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)

            tunnels = tunnel.discover_active_tunnels(force_refresh=True)

            assert tunnels == {}

    def test_uses_cache_within_ttl(self):
        """Should use cached results within TTL."""
        ps_output = (
            "USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND\n"
            "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm\n"  # noqa: E501
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)

            # First call populates cache
            tunnels1 = tunnel.discover_active_tunnels()
            assert mock_run.call_count == 1

            # Second call uses cache
            tunnels2 = tunnel.discover_active_tunnels()
            assert mock_run.call_count == 1  # No additional call

            assert tunnels1 == tunnels2

    def test_force_refresh_bypasses_cache(self):
        """Should bypass cache when force_refresh=True."""
        ps_output = (
            "USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND\n"
            "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm\n"  # noqa: E501
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)

            tunnel.discover_active_tunnels()
            tunnel.discover_active_tunnels(force_refresh=True)

            assert mock_run.call_count == 2

    def test_handles_subprocess_timeout(self):
        """Should handle subprocess timeout gracefully."""
        import subprocess

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("ps", 5)

            tunnels = tunnel.discover_active_tunnels(force_refresh=True)

            assert tunnels == {}

    def test_handles_subprocess_error(self):
        """Should handle subprocess errors gracefully."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = Exception("Unexpected error")

            tunnels = tunnel.discover_active_tunnels(force_refresh=True)

            assert tunnels == {}


class TestGetTunnelsForHost:
    """Tests for get_tunnels_for_host()."""

    def test_filters_by_host(self):
        """Should return only tunnels for the specified host."""
        ps_output = (
            "USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND\n"
            "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm\n"  # noqa: E501
            "yuqiu    12346   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 3000:localhost:3000 other/host\n"  # noqa: E501
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)

            tunnels = tunnel.get_tunnels_for_host("user/rdev-vm")

            assert len(tunnels) == 1
            assert 4200 in tunnels
            assert 3000 not in tunnels

    def test_returns_empty_for_unknown_host(self):
        """Should return empty dict for host with no tunnels."""
        ps_output = (
            "USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND\n"
            "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm\n"  # noqa: E501
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)

            tunnels = tunnel.get_tunnels_for_host("unknown/host")

            assert tunnels == {}


class TestFindTunnelByPort:
    """Tests for find_tunnel_by_port()."""

    def test_finds_existing_tunnel(self):
        """Should find tunnel info for existing port."""
        ps_output = (
            "USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND\n"
            "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm\n"  # noqa: E501
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)

            info = tunnel.find_tunnel_by_port(4200)

            assert info is not None
            assert info["pid"] == 12345
            assert info["host"] == "user/rdev-vm"

    def test_returns_none_for_nonexistent_port(self):
        """Should return None for port without tunnel."""
        ps_output = (
            "USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND\n"
            "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm\n"  # noqa: E501
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)

            info = tunnel.find_tunnel_by_port(3000)

            assert info is None


class TestIsProcessAlive:
    """Tests for is_process_alive()."""

    def test_alive_process(self):
        """Should return True for running process."""
        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None  # os.kill returns None on success

            assert tunnel.is_process_alive(12345) is True
            mock_kill.assert_called_once_with(12345, 0)

    def test_dead_process(self):
        """Should return False for dead process."""
        with patch("os.kill") as mock_kill:
            mock_kill.side_effect = ProcessLookupError()

            assert tunnel.is_process_alive(12345) is False

    def test_permission_denied(self):
        """Should return False when permission denied."""
        with patch("os.kill") as mock_kill:
            mock_kill.side_effect = PermissionError()

            assert tunnel.is_process_alive(12345) is False


class TestIsPortAvailable:
    """Tests for is_port_available()."""

    def test_port_free(self):
        """Should return True when lsof reports no listeners."""
        with patch("orchestrator.session.tunnel.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=1)
            assert tunnel.is_port_available(4200) is True

    def test_port_occupied(self):
        """Should return False when lsof reports a listener."""
        with patch("orchestrator.session.tunnel.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="12345\n", returncode=0)
            assert tunnel.is_port_available(4200) is False

    def test_lsof_timeout_falls_back_to_socket(self):
        """Should fall back to socket bind when lsof times out."""
        import subprocess as sp

        with (
            patch("orchestrator.session.tunnel.subprocess.run") as mock_run,
            patch("orchestrator.session.tunnel.socket.socket") as mock_socket,
        ):
            mock_run.side_effect = sp.TimeoutExpired("lsof", 3)
            mock_sock_inst = MagicMock()
            mock_socket.return_value.__enter__ = MagicMock(return_value=mock_sock_inst)
            mock_socket.return_value.__exit__ = MagicMock(return_value=False)
            mock_sock_inst.bind.return_value = None  # bind succeeds
            assert tunnel.is_port_available(4200) is True

    def test_lsof_missing_falls_back_to_socket(self):
        """Should fall back to socket bind when lsof is not installed."""
        with (
            patch("orchestrator.session.tunnel.subprocess.run") as mock_run,
            patch("orchestrator.session.tunnel.socket.socket") as mock_socket,
        ):
            mock_run.side_effect = OSError("lsof not found")
            mock_sock_inst = MagicMock()
            mock_socket.return_value.__enter__ = MagicMock(return_value=mock_sock_inst)
            mock_socket.return_value.__exit__ = MagicMock(return_value=False)
            mock_sock_inst.bind.side_effect = OSError("Address in use")
            assert tunnel.is_port_available(4200) is False


class TestFindAvailablePort:
    """Tests for find_available_port()."""

    def test_returns_preferred_if_available(self):
        """Should return the preferred port if it's free."""
        with patch.object(tunnel, "is_port_available", return_value=True):
            assert tunnel.find_available_port(4200) == 4200

    def test_skips_occupied_ports(self):
        """Should skip occupied ports and return the next free one."""
        # 4200 occupied, 4201 occupied, 4202 free
        with patch.object(tunnel, "is_port_available", side_effect=[False, False, True]):
            assert tunnel.find_available_port(4200) == 4202

    def test_skips_reserved_ports(self):
        """Should skip reserved ports."""
        # Start searching from 8093 (reserved), 8094 should be checked next
        with patch.object(tunnel, "is_port_available", return_value=True):
            assert tunnel.find_available_port(8093) == 8094

    def test_returns_none_when_all_occupied(self):
        """Should return None when no port is available."""
        with patch.object(tunnel, "is_port_available", return_value=False):
            assert tunnel.find_available_port(4200, max_attempts=5) is None

    def test_stops_at_65535(self):
        """Should not exceed port 65535."""
        with patch.object(tunnel, "is_port_available", return_value=False):
            assert tunnel.find_available_port(65530, max_attempts=100) is None


class TestCreateTunnel:
    """Tests for create_tunnel()."""

    def test_creates_new_tunnel(self):
        """Should spawn SSH process for new tunnel."""
        with (
            patch("subprocess.run") as mock_run,
            patch("subprocess.Popen") as mock_popen,
            patch.object(tunnel, "is_port_available", return_value=True),
        ):
            # No existing tunnels
            mock_run.return_value = MagicMock(stdout="", returncode=0)

            # Mock the new SSH process
            mock_proc = MagicMock()
            mock_proc.pid = 99999
            mock_proc.poll.return_value = None  # Process still running
            mock_popen.return_value = mock_proc

            success, result = tunnel.create_tunnel("user/rdev-vm", 4200)

            assert success is True
            assert result["local_port"] == 4200
            assert result["remote_port"] == 4200
            assert result["pid"] == 99999
            assert result["host"] == "user/rdev-vm"

            mock_popen.assert_called_once()
            call_args = mock_popen.call_args[0][0]
            assert "ssh" in call_args
            assert "-N" in call_args
            assert "-L" in call_args
            assert "4200:localhost:4200" in call_args
            assert "user/rdev-vm" in call_args

    def test_returns_existing_tunnel_for_same_host(self):
        """Should return existing tunnel info if same host/port already tunneled."""
        ps_output = (
            "USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND\n"
            "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm\n"  # noqa: E501
        )
        with patch("subprocess.run") as mock_run, patch("os.kill") as mock_kill:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)
            mock_kill.return_value = None  # Process is alive

            success, result = tunnel.create_tunnel("user/rdev-vm", 4200)

            assert success is True
            assert result["existing"] is True
            assert result["pid"] == 12345

    def test_auto_finds_port_when_occupied(self):
        """Should auto-find a new port when the requested one is occupied."""
        with (
            patch("subprocess.run") as mock_run,
            patch("subprocess.Popen") as mock_popen,
            patch.object(tunnel, "is_port_available", side_effect=[False, True]),
        ):
            # No existing SSH tunnels
            mock_run.return_value = MagicMock(stdout="", returncode=0)

            mock_proc = MagicMock()
            mock_proc.pid = 99999
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            success, result = tunnel.create_tunnel("user/rdev-vm", 4200)

            assert success is True
            # local_port should be 4201 (4200 was occupied, search starts at 4201)
            assert result["local_port"] == 4201
            assert result["remote_port"] == 4200

            # Verify SSH command uses the new port
            call_args = mock_popen.call_args[0][0]
            assert "4201:localhost:4200" in call_args

    def test_fails_when_no_port_available(self):
        """Should fail when no available port can be found."""
        with (
            patch("subprocess.run") as mock_run,
            patch.object(tunnel, "is_port_available", return_value=False),
            patch.object(tunnel, "find_available_port", return_value=None),
        ):
            mock_run.return_value = MagicMock(stdout="", returncode=0)

            success, result = tunnel.create_tunnel("user/rdev-vm", 4200)

            assert success is False
            assert "occupied" in result["error"]

    def test_validates_port_range(self):
        """Should reject invalid port numbers."""
        success, result = tunnel.create_tunnel("user/rdev-vm", 0)
        assert success is False
        assert "Port must be" in result["error"]

        success, result = tunnel.create_tunnel("user/rdev-vm", 70000)
        assert success is False
        assert "Port must be" in result["error"]

    def test_reserved_port_auto_assigns_alternative(self):
        """Reserved ports (8093, 9222) should auto-assign the next available port."""
        with (
            patch("subprocess.run") as mock_run,
            patch("subprocess.Popen") as mock_popen,
            patch.object(tunnel, "is_port_available", return_value=True),
            patch.object(tunnel, "find_available_port", return_value=8094),
        ):
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            success, result = tunnel.create_tunnel("user/rdev-vm", 8093)
            assert success is True
            assert result["local_port"] == 8094

    def test_reserved_local_port_auto_assigns_alternative(self):
        """Reserved port as explicit local_port should auto-assign."""
        with (
            patch("subprocess.run") as mock_run,
            patch("subprocess.Popen") as mock_popen,
            patch.object(tunnel, "is_port_available", return_value=True),
            patch.object(tunnel, "find_available_port", return_value=8094),
        ):
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            success, result = tunnel.create_tunnel("user/rdev-vm", 4200, local_port=8093)
            assert success is True
            assert result["local_port"] == 8094

    def test_handles_ssh_failure(self):
        """Should handle SSH process failing to start."""
        with (
            patch("subprocess.run") as mock_run,
            patch("subprocess.Popen") as mock_popen,
            patch.object(tunnel, "is_port_available", return_value=True),
        ):
            mock_run.return_value = MagicMock(stdout="", returncode=0)

            # Mock SSH process that fails immediately
            mock_proc = MagicMock()
            mock_proc.pid = 99999
            mock_proc.poll.return_value = 1  # Process exited
            mock_proc.returncode = 1
            mock_popen.return_value = mock_proc

            success, result = tunnel.create_tunnel("user/rdev-vm", 4200)

            assert success is False
            assert "failed to start" in result["error"]

    def test_custom_local_port(self):
        """Should support custom local port different from remote port."""
        with (
            patch("subprocess.run") as mock_run,
            patch("subprocess.Popen") as mock_popen,
            patch.object(tunnel, "is_port_available", return_value=True),
        ):
            mock_run.return_value = MagicMock(stdout="", returncode=0)

            mock_proc = MagicMock()
            mock_proc.pid = 99999
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            success, result = tunnel.create_tunnel("user/rdev-vm", 4200, local_port=8080)

            assert success is True
            assert result["local_port"] == 8080
            assert result["remote_port"] == 4200


class TestCloseTunnel:
    """Tests for close_tunnel()."""

    def test_closes_existing_tunnel(self):
        """Should send SIGTERM to tunnel process."""
        ps_output = (
            "USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND\n"
            "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm\n"  # noqa: E501
        )
        with patch("subprocess.run") as mock_run, patch("os.kill") as mock_kill:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)

            success, message = tunnel.close_tunnel(4200)

            assert success is True
            mock_kill.assert_called_once_with(12345, signal.SIGTERM)

    def test_returns_false_for_nonexistent_tunnel(self):
        """Should return False for port without tunnel."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)

            success, message = tunnel.close_tunnel(4200)

            assert success is False
            assert "No tunnel found" in message

    def test_verifies_host_ownership(self):
        """Should reject closing tunnel owned by different host."""
        ps_output = (
            "USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND\n"
            "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 other/host\n"  # noqa: E501
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)

            success, message = tunnel.close_tunnel(4200, host="user/rdev-vm")

            assert success is False
            assert "belongs to" in message

    def test_handles_dead_process(self):
        """Should succeed even if process already dead."""
        ps_output = (
            "USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND\n"
            "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm\n"  # noqa: E501
        )
        with patch("subprocess.run") as mock_run, patch("os.kill") as mock_kill:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)
            mock_kill.side_effect = ProcessLookupError()

            success, message = tunnel.close_tunnel(4200)

            assert success is True
            assert "already dead" in message


class TestCleanupTunnelsForHost:
    """Tests for cleanup_tunnels_for_host()."""

    def test_kills_all_tunnels_for_host(self):
        """Should kill all tunnels belonging to the specified host."""
        ps_output = (
            "USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND\n"
            "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm\n"  # noqa: E501
            "yuqiu    12346   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 3000:localhost:3000 user/rdev-vm\n"  # noqa: E501
            "yuqiu    12347   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 8080:localhost:8080 other/host\n"  # noqa: E501
        )
        with patch("subprocess.run") as mock_run, patch("os.kill") as mock_kill:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)

            closed = tunnel.cleanup_tunnels_for_host("user/rdev-vm")

            assert closed == 2
            # Should have killed both 12345 and 12346, but not 12347
            kill_calls = [call[0][0] for call in mock_kill.call_args_list]
            assert 12345 in kill_calls
            assert 12346 in kill_calls
            assert 12347 not in kill_calls

    def test_returns_zero_for_no_tunnels(self):
        """Should return 0 when host has no tunnels."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)

            closed = tunnel.cleanup_tunnels_for_host("user/rdev-vm")

            assert closed == 0


class TestInvalidateCache:
    """Tests for invalidate_cache()."""

    def test_invalidates_cache(self):
        """Should reset cache timestamp to trigger refresh."""
        tunnel._cache_timestamp = 9999999
        tunnel._tunnel_cache = {"cached": "data"}

        tunnel.invalidate_cache()

        assert tunnel._cache_timestamp == 0


class TestReverseTunnelStartupVerification:
    """Tests for SSH startup verification and failure tracking in ReverseTunnelManager."""

    def _make_manager(self, tmp_path):
        return tunnel.ReverseTunnelManager(log_dir=str(tmp_path))

    @patch("orchestrator.session.tunnel.time.sleep")
    @patch("orchestrator.session.tunnel.subprocess.Popen")
    def test_ssh_command_must_not_include_clear_all_forwardings(
        self, mock_popen, mock_sleep, tmp_path
    ):
        """ClearAllForwardings=yes silently clears the -R flag on OpenSSH 10.2+.

        This caused a critical production bug where the tunnel SSH connected
        and authenticated successfully but never set up the remote port
        forwarding — proc.poll() showed "alive" while the remote port was
        never bound.  See docs/009-reconnect-redesign.md Scenario 12.
        """
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.poll.return_value = None  # survives startup
        mock_popen.return_value = mock_proc

        mgr = self._make_manager(tmp_path)
        mgr.start_tunnel("s1", "worker-1", "user/vm")

        cmd = mock_popen.call_args[0][0]
        assert "ClearAllForwardings=yes" not in cmd, (
            "ClearAllForwardings=yes clears -R from the command line on "
            "OpenSSH 10.2+, breaking the reverse tunnel silently"
        )
        assert "ExitOnForwardFailure=yes" not in cmd, (
            "ExitOnForwardFailure=yes kills the entire SSH session when an "
            "inherited LocalForward port conflicts, destroying the -R tunnel"
        )

    @patch("orchestrator.session.tunnel.time.sleep")
    @patch("orchestrator.session.tunnel.subprocess.Popen")
    def test_ssh_command_includes_reverse_forward(self, mock_popen, mock_sleep, tmp_path):
        """The -R flag must be present for the reverse tunnel to work."""
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        mgr = self._make_manager(tmp_path)
        mgr.start_tunnel("s1", "worker-1", "user/vm")

        cmd = mock_popen.call_args[0][0]
        assert "-R" in cmd
        r_idx = cmd.index("-R")
        assert cmd[r_idx + 1] == "8093:127.0.0.1:8093"

    @patch("orchestrator.session.tunnel.time.sleep")
    @patch("orchestrator.session.tunnel.subprocess.Popen")
    def test_returns_none_when_process_dies_during_startup(self, mock_popen, mock_sleep, tmp_path):
        """Should return None when SSH exits during the 3s startup check."""
        mock_proc = MagicMock()
        mock_proc.pid = 200
        mock_proc.poll.return_value = 255  # exited
        mock_popen.return_value = mock_proc

        mgr = self._make_manager(tmp_path)
        result = mgr.start_tunnel("s1", "worker-1", "user/vm")

        assert result is None
        mock_sleep.assert_called_once_with(3)

    @patch("orchestrator.session.tunnel.time.sleep")
    @patch("orchestrator.session.tunnel.subprocess.Popen")
    def test_returns_pid_when_process_survives(self, mock_popen, mock_sleep, tmp_path):
        """Should return PID when SSH is still alive after startup check."""
        mock_proc = MagicMock()
        mock_proc.pid = 300
        mock_proc.poll.return_value = None  # still alive
        mock_popen.return_value = mock_proc

        mgr = self._make_manager(tmp_path)
        result = mgr.start_tunnel("s1", "worker-1", "user/vm")

        assert result == 300

    @patch("orchestrator.session.tunnel.time.sleep")
    @patch("orchestrator.session.tunnel.subprocess.Popen")
    def test_increments_failure_count_on_failure(self, mock_popen, mock_sleep, tmp_path):
        """Should track consecutive failures in _failure_counts."""
        mock_proc = MagicMock()
        mock_proc.pid = 400
        mock_proc.poll.return_value = 1
        mock_popen.return_value = mock_proc

        mgr = self._make_manager(tmp_path)

        mgr.start_tunnel("s1", "worker-1", "user/vm")
        assert mgr._failure_counts["s1"] == 1

        mgr.start_tunnel("s1", "worker-1", "user/vm")
        assert mgr._failure_counts["s1"] == 2

    @patch("orchestrator.session.tunnel.time.sleep")
    @patch("orchestrator.session.tunnel.subprocess.Popen")
    def test_resets_failure_count_on_success(self, mock_popen, mock_sleep, tmp_path):
        """Should reset failure tracking after a successful start."""
        mgr = self._make_manager(tmp_path)

        # First call: fail
        mock_proc_fail = MagicMock()
        mock_proc_fail.pid = 500
        mock_proc_fail.poll.return_value = 1
        mock_popen.return_value = mock_proc_fail
        mgr.start_tunnel("s1", "worker-1", "user/vm")
        assert mgr._failure_counts["s1"] == 1

        # Second call: succeed
        mock_proc_ok = MagicMock()
        mock_proc_ok.pid = 501
        mock_proc_ok.poll.return_value = None
        mock_popen.return_value = mock_proc_ok
        mgr.start_tunnel("s1", "worker-1", "user/vm")
        assert mgr._failure_counts.get("s1", 0) == 0

    @patch("orchestrator.session.tunnel.time.sleep")
    @patch("orchestrator.session.tunnel.subprocess.Popen")
    def test_stop_tunnel_clears_failure_state(self, mock_popen, mock_sleep, tmp_path):
        """Manual stop_tunnel should reset failure tracking."""
        mgr = self._make_manager(tmp_path)

        # Fail once to populate tracking
        mock_proc = MagicMock()
        mock_proc.pid = 600
        mock_proc.poll.return_value = 1
        mock_popen.return_value = mock_proc
        mgr.start_tunnel("s1", "worker-1", "user/vm")
        assert mgr._failure_counts["s1"] == 1

        mgr.stop_tunnel("s1")
        assert mgr._failure_counts.get("s1", 0) == 0
        assert mgr._last_errors.get("s1") is None

    def test_get_failure_info_defaults(self, tmp_path):
        """get_failure_info should return (0, None) for unknown sessions."""
        mgr = self._make_manager(tmp_path)
        count, error = mgr.get_failure_info("unknown")
        assert count == 0
        assert error is None

    def test_read_last_log_line(self, tmp_path):
        """_read_last_log_line should return the last non-empty line."""
        log = tmp_path / "test.log"
        log.write_text("line1\nline2\nline3\n")

        result = tunnel.ReverseTunnelManager._read_last_log_line(str(log))
        assert result == "line3"

    def test_read_last_log_line_empty_file(self, tmp_path):
        """_read_last_log_line should return None for empty files."""
        log = tmp_path / "empty.log"
        log.write_text("")

        result = tunnel.ReverseTunnelManager._read_last_log_line(str(log))
        assert result is None

    def test_read_last_log_line_missing_file(self, tmp_path):
        """_read_last_log_line should return None for missing files."""
        result = tunnel.ReverseTunnelManager._read_last_log_line(str(tmp_path / "nope.log"))
        assert result is None

    @patch("orchestrator.session.tunnel.time.sleep")
    @patch("orchestrator.session.tunnel.subprocess.Popen")
    def test_survives_local_forward_failure(self, mock_popen, mock_sleep, tmp_path):
        """Inherited LocalForward conflict is non-fatal — reverse tunnel still works."""
        mock_proc = MagicMock()
        mock_proc.pid = 700
        mock_proc.poll.return_value = None  # process alive
        mock_popen.return_value = mock_proc

        log_path = tmp_path / "worker-1.log"

        # Simulate SSH writing to the log during the sleep period.
        def write_log_during_sleep(_seconds):
            with open(log_path, "a") as f:
                f.write("Warning: Could not request local forwarding.\n")

        mock_sleep.side_effect = write_log_during_sleep

        mgr = self._make_manager(tmp_path)
        result = mgr.start_tunnel("s1", "worker-1", "user/vm")

        assert result == 700
        # No failure should be recorded
        assert mgr._failure_counts.get("s1", 0) == 0

    @patch("orchestrator.session.tunnel.time.sleep")
    @patch("orchestrator.session.tunnel.subprocess.Popen")
    def test_fails_on_remote_forward_failure(self, mock_popen, mock_sleep, tmp_path):
        """Remote port forwarding failure is fatal — tunnel must be killed."""
        mock_proc = MagicMock()
        mock_proc.pid = 800
        mock_proc.poll.return_value = None  # process alive (SSH stays connected)
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        log_path = tmp_path / "worker-1.log"

        # Simulate SSH writing to the log during the sleep period.
        def write_log_during_sleep(_seconds):
            with open(log_path, "a") as f:
                f.write("Warning: remote port forwarding failed for listen port 8093\n")

        mock_sleep.side_effect = write_log_during_sleep

        mgr = self._make_manager(tmp_path)
        result = mgr.start_tunnel("s1", "worker-1", "user/vm")

        assert result is None
        assert mgr._failure_counts["s1"] == 1
        assert mgr._last_errors["s1"] == "remote port forwarding failed"
        mock_proc.terminate.assert_called_once()

    def test_read_log_since(self, tmp_path):
        """_read_log_since should return only content written after start_pos."""
        log = tmp_path / "test.log"
        log.write_text("old line 1\nold line 2\n")
        start_pos = log.stat().st_size

        # Append new content
        with open(log, "a") as f:
            f.write("new line 1\nnew line 2\n")

        result = tunnel.ReverseTunnelManager._read_log_since(str(log), start_pos)
        assert result == "new line 1\nnew line 2\n"
        assert "old line" not in result

    def test_read_log_since_no_new_content(self, tmp_path):
        """_read_log_since should return None when no new content was written."""
        log = tmp_path / "test.log"
        log.write_text("existing content\n")
        start_pos = log.stat().st_size

        result = tunnel.ReverseTunnelManager._read_log_since(str(log), start_pos)
        assert result is None

    def test_read_log_since_missing_file(self, tmp_path):
        """_read_log_since should return None for missing files."""
        result = tunnel.ReverseTunnelManager._read_log_since(str(tmp_path / "nope.log"), 0)
        assert result is None
