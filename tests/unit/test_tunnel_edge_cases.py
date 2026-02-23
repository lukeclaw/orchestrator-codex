"""Edge case tests for SSH tunnel management.

Tests for regex patterns, race conditions, and other edge cases.
"""

import re
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


class TestRegexPatternEdgeCases:
    """Tests for the SSH command regex pattern edge cases."""
    
    # The pattern from tunnel.py
    pattern = re.compile(r'ssh\s+.*-N\s+.*-L\s+(\d+):localhost:(\d+)\s+.*?(\S+)\s*$')

    def test_standard_tunnel_command(self):
        """Should match standard tunnel command."""
        line = "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm"
        match = self.pattern.search(line)
        assert match is not None
        assert match.group(1) == "4200"
        assert match.group(2) == "4200"
        assert match.group(3) == "user/rdev-vm"

    def test_tunnel_with_ssh_options(self):
        """Should match tunnel with SSH options like -o."""
        line = "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 -o StrictHostKeyChecking=no user/rdev-vm"
        match = self.pattern.search(line)
        assert match is not None
        assert match.group(1) == "4200"
        assert match.group(3) == "user/rdev-vm"

    def test_tunnel_with_different_local_remote_ports(self):
        """Should correctly parse different local and remote ports."""
        line = "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 8080:localhost:4200 user/rdev-vm"
        match = self.pattern.search(line)
        assert match is not None
        assert match.group(1) == "8080"  # local port
        assert match.group(2) == "4200"  # remote port

    def test_tunnel_with_options_before_N(self):
        """Should match when options come before -N."""
        line = "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -o Option=value -N -L 4200:localhost:4200 user/rdev-vm"
        match = self.pattern.search(line)
        assert match is not None

    def test_does_not_match_reverse_tunnel(self):
        """Should NOT match reverse tunnels (-R instead of -L)."""
        line = "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -R 8093:127.0.0.1:8093 user/rdev-vm"
        match = self.pattern.search(line)
        assert match is None

    def test_does_not_match_non_blocking_ssh(self):
        """Should NOT match SSH without -N flag."""
        line = "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -L 4200:localhost:4200 user/rdev-vm"
        match = self.pattern.search(line)
        assert match is None

    def test_host_with_special_characters(self):
        """Should match hosts with hyphens and underscores."""
        line = "yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 my-project_mt/sleepy-franklin-123"
        match = self.pattern.search(line)
        assert match is not None
        assert match.group(3) == "my-project_mt/sleepy-franklin-123"


class TestReservedPorts:
    """Tests for reserved port handling."""

    def test_get_reserved_ports_returns_copy(self):
        """Should return a copy of reserved ports, not the original."""
        ports1 = tunnel.get_reserved_ports()
        ports2 = tunnel.get_reserved_ports()
        assert ports1 == ports2
        assert ports1 is not ports2  # Different objects
        
        # Modifying returned set shouldn't affect original
        ports1.add(9999)
        assert 9999 not in tunnel.RESERVED_PORTS

    def test_8093_is_reserved(self):
        """Port 8093 should be in reserved ports."""
        assert 8093 in tunnel.RESERVED_PORTS


class TestPortConflictDetection:
    """Tests for port conflict detection edge cases."""

    def test_dead_process_allows_port_reuse(self):
        """Should allow reusing port if previous tunnel process is dead."""
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm
"""
        def run_side_effect(cmd, **kwargs):
            # ps aux → return tunnel process list
            if cmd[0] == "ps":
                return MagicMock(stdout=ps_output, returncode=0)
            # lsof → return empty (port is available)
            if cmd[0] == "lsof":
                return MagicMock(stdout="", returncode=1)
            return MagicMock(stdout="", returncode=0)

        with patch("subprocess.run", side_effect=run_side_effect), \
             patch("os.kill") as mock_kill, \
             patch("subprocess.Popen") as mock_popen, \
             patch("time.sleep"):
            # Process is dead
            mock_kill.side_effect = ProcessLookupError()

            # New tunnel should be allowed
            mock_proc = MagicMock()
            mock_proc.pid = 99999
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            success, result = tunnel.create_tunnel("other/host", 4200)

            assert success is True
            assert result["pid"] == 99999


class TestCleanupEdgeCases:
    """Tests for cleanup edge cases."""

    def test_cleanup_handles_mixed_success_failure(self):
        """Should continue cleanup even if some kills fail."""
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm
yuqiu    12346   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 3000:localhost:3000 user/rdev-vm
"""
        with patch("subprocess.run") as mock_run, \
             patch("os.kill") as mock_kill:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)
            
            # First kill succeeds, second fails with permission error
            mock_kill.side_effect = [None, PermissionError("denied")]
            
            closed = tunnel.cleanup_tunnels_for_host("user/rdev-vm")
            
            # Should count the one that succeeded
            assert closed == 1


class TestCacheEdgeCases:
    """Tests for cache behavior edge cases."""

    def test_cache_returns_copy_not_reference(self):
        """Should return a copy of cache to prevent external modification."""
        ps_output = """USER       PID  %CPU %MEM      VSZ    RSS   TT  STAT STARTED      TIME COMMAND
yuqiu    12345   0.0  0.0 408628368   1234 s000  S+   10:00AM   0:00.01 ssh -N -L 4200:localhost:4200 user/rdev-vm
"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)
            
            tunnels1 = tunnel.discover_active_tunnels(force_refresh=True)
            tunnels2 = tunnel.discover_active_tunnels()
            
            # Should be equal but not same object
            assert tunnels1 == tunnels2
            assert tunnels1 is not tunnels2
            
            # Modifying returned dict shouldn't affect cache
            tunnels1[9999] = {"test": True}
            tunnels3 = tunnel.discover_active_tunnels()
            assert 9999 not in tunnels3


class TestAPIErrorHandling:
    """Tests for API error handling edge cases."""

    def test_create_tunnel_api_with_reserved_port(self):
        """API should return appropriate error for reserved port."""
        from fastapi.testclient import TestClient
        from orchestrator.api.app import create_app
        from orchestrator.state.db import get_connection
        from orchestrator.state.migrations.runner import apply_migrations
        from orchestrator.state.repositories import sessions as repo
        
        conn = get_connection(":memory:")
        apply_migrations(conn)
        
        # Create rdev session
        session = repo.create_session(conn, "test-worker", "user/rdev-vm", "/tmp/work")
        
        app = create_app(db=conn)
        with TestClient(app) as client:
            response = client.post(
                f"/api/sessions/{session.id}/tunnel",
                json={"port": 8093}
            )
            
            assert response.status_code == 500
            assert "reserved" in response.json()["detail"].lower()
        
        conn.close()
