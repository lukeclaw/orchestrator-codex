"""Unit tests for SSH tunnel management.

Tests the tunnel discovery, creation, and cleanup functionality.
Uses mocking to avoid actual subprocess calls for fast execution.
"""

import os
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
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm
"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)
            
            tunnels = tunnel.discover_active_tunnels(force_refresh=True)
            
            assert 4200 in tunnels
            assert tunnels[4200]["pid"] == 12345
            assert tunnels[4200]["remote_port"] == 4200
            assert tunnels[4200]["host"] == "user/rdev-vm"

    def test_discovers_multiple_tunnels(self):
        """Should discover multiple SSH tunnels."""
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm
yuqiu    12346   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 3000:localhost:3000 other/rdev-host
"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)
            
            tunnels = tunnel.discover_active_tunnels(force_refresh=True)
            
            assert len(tunnels) == 2
            assert 4200 in tunnels
            assert 3000 in tunnels

    def test_ignores_non_tunnel_ssh(self):
        """Should ignore SSH processes that aren't tunnels."""
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh user@host
yuqiu    12346   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm
"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)
            
            tunnels = tunnel.discover_active_tunnels(force_refresh=True)
            
            assert len(tunnels) == 1
            assert 4200 in tunnels

    def test_ignores_grep_processes(self):
        """Should ignore grep processes in ps output."""
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 grep ssh -N -L
yuqiu    12346   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm
"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)
            
            tunnels = tunnel.discover_active_tunnels(force_refresh=True)
            
            assert len(tunnels) == 1
            assert 4200 in tunnels

    def test_empty_ps_output(self):
        """Should return empty dict when no tunnels found."""
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 vim file.py
"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)
            
            tunnels = tunnel.discover_active_tunnels(force_refresh=True)
            
            assert tunnels == {}

    def test_uses_cache_within_ttl(self):
        """Should use cached results within TTL."""
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm
"""
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
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm
"""
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
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm
yuqiu    12346   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 3000:localhost:3000 other/host
"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)
            
            tunnels = tunnel.get_tunnels_for_host("user/rdev-vm")
            
            assert len(tunnels) == 1
            assert 4200 in tunnels
            assert 3000 not in tunnels

    def test_returns_empty_for_unknown_host(self):
        """Should return empty dict for host with no tunnels."""
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm
"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)
            
            tunnels = tunnel.get_tunnels_for_host("unknown/host")
            
            assert tunnels == {}


class TestFindTunnelByPort:
    """Tests for find_tunnel_by_port()."""

    def test_finds_existing_tunnel(self):
        """Should find tunnel info for existing port."""
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm
"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)
            
            info = tunnel.find_tunnel_by_port(4200)
            
            assert info is not None
            assert info["pid"] == 12345
            assert info["host"] == "user/rdev-vm"

    def test_returns_none_for_nonexistent_port(self):
        """Should return None for port without tunnel."""
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm
"""
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


class TestCreateTunnel:
    """Tests for create_tunnel()."""

    def test_creates_new_tunnel(self):
        """Should spawn SSH process for new tunnel."""
        with patch("subprocess.run") as mock_run, \
             patch("subprocess.Popen") as mock_popen:
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
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm
"""
        with patch("subprocess.run") as mock_run, \
             patch("os.kill") as mock_kill:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)
            mock_kill.return_value = None  # Process is alive
            
            success, result = tunnel.create_tunnel("user/rdev-vm", 4200)
            
            assert success is True
            assert result["existing"] is True
            assert result["pid"] == 12345

    def test_rejects_port_used_by_different_host(self):
        """Should reject if port is already tunneled to different host."""
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 other/host
"""
        with patch("subprocess.run") as mock_run, \
             patch("os.kill") as mock_kill:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)
            mock_kill.return_value = None  # Process is alive
            
            success, result = tunnel.create_tunnel("user/rdev-vm", 4200)
            
            assert success is False
            assert "already tunneled" in result["error"]

    def test_validates_port_range(self):
        """Should reject invalid port numbers."""
        success, result = tunnel.create_tunnel("user/rdev-vm", 0)
        assert success is False
        assert "Port must be" in result["error"]
        
        success, result = tunnel.create_tunnel("user/rdev-vm", 70000)
        assert success is False
        assert "Port must be" in result["error"]

    def test_rejects_reserved_port_8093(self):
        """Should reject port 8093 which is used for reverse tunnel."""
        success, result = tunnel.create_tunnel("user/rdev-vm", 8093)
        assert success is False
        assert "reserved" in result["error"].lower()
        assert "8093" in result["error"]

    def test_rejects_reserved_port_as_local_port(self):
        """Should reject reserved port even when specified as local_port."""
        success, result = tunnel.create_tunnel("user/rdev-vm", 4200, local_port=8093)
        assert success is False
        assert "reserved" in result["error"].lower()

    def test_handles_ssh_failure(self):
        """Should handle SSH process failing to start."""
        with patch("subprocess.run") as mock_run, \
             patch("subprocess.Popen") as mock_popen:
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
        with patch("subprocess.run") as mock_run, \
             patch("subprocess.Popen") as mock_popen:
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
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm
"""
        with patch("subprocess.run") as mock_run, \
             patch("os.kill") as mock_kill:
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
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 other/host
"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)
            
            success, message = tunnel.close_tunnel(4200, host="user/rdev-vm")
            
            assert success is False
            assert "belongs to" in message

    def test_handles_dead_process(self):
        """Should succeed even if process already dead."""
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm
"""
        with patch("subprocess.run") as mock_run, \
             patch("os.kill") as mock_kill:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)
            mock_kill.side_effect = ProcessLookupError()
            
            success, message = tunnel.close_tunnel(4200)
            
            assert success is True
            assert "already dead" in message


class TestCleanupTunnelsForHost:
    """Tests for cleanup_tunnels_for_host()."""

    def test_kills_all_tunnels_for_host(self):
        """Should kill all tunnels belonging to the specified host."""
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm
yuqiu    12346   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 3000:localhost:3000 user/rdev-vm
yuqiu    12347   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 8080:localhost:8080 other/host
"""
        with patch("subprocess.run") as mock_run, \
             patch("os.kill") as mock_kill:
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
