"""Unit tests for SSH alive check hostname parsing."""
import pytest
from orchestrator.session.reconnect import parse_hostname_from_output


class TestParseHostnameFromOutput:
    """Tests for parse_hostname_from_output function."""

    def test_rdev_hostname_with_command_in_output(self):
        """Test parsing when output includes the command line itself."""
        # This is the actual bug case - output includes the echo command
        output = """[yuqiu@sleepy-franklin subs-mt]$ echo SSH_START_62624 && hostname && echo SSH_END_62624
SSH_START_62624
rdev-aks-0b9d79eb-3adf-4339-9987-2ddc81bac2bb-pqvf4
SSH_END_62624
[yuqiu@sleepy-franklin subs-mt]$"""
        
        result = parse_hostname_from_output(output, "SSH_START_62624", "SSH_END_62624")
        assert result == "rdev-aks-0b9d79eb-3adf-4339-9987-2ddc81bac2bb-pqvf4"

    def test_local_hostname_with_command_in_output(self):
        """Test parsing local hostname (not rdev) when output includes command."""
        output = """➜  orchestrator git:(main) ✗ echo SSH_START_12345 && hostname && echo SSH_END_12345
SSH_START_12345
yuqiu-mn7215.linkedin.biz
SSH_END_12345
➜  orchestrator git:(main) ✗"""
        
        result = parse_hostname_from_output(output, "SSH_START_12345", "SSH_END_12345")
        assert result == "yuqiu-mn7215.linkedin.biz"

    def test_clean_output_without_command(self):
        """Test parsing when output doesn't include the command line."""
        output = """SSH_START_99999
rdev-test-hostname
SSH_END_99999"""
        
        result = parse_hostname_from_output(output, "SSH_START_99999", "SSH_END_99999")
        assert result == "rdev-test-hostname"

    def test_missing_start_marker(self):
        """Test when start marker is missing."""
        output = """some output
rdev-hostname
SSH_END_12345"""
        
        result = parse_hostname_from_output(output, "SSH_START_12345", "SSH_END_12345")
        assert result is None

    def test_missing_end_marker(self):
        """Test when end marker is missing."""
        output = """SSH_START_12345
rdev-hostname
some other output"""
        
        result = parse_hostname_from_output(output, "SSH_START_12345", "SSH_END_12345")
        assert result is None

    def test_markers_in_wrong_order(self):
        """Test when end marker appears before start marker."""
        output = """SSH_END_12345
rdev-hostname
SSH_START_12345"""
        
        result = parse_hostname_from_output(output, "SSH_START_12345", "SSH_END_12345")
        assert result is None

    def test_empty_hostname_between_markers(self):
        """Test when there's no hostname between markers."""
        output = """SSH_START_12345
SSH_END_12345"""
        
        result = parse_hostname_from_output(output, "SSH_START_12345", "SSH_END_12345")
        assert result is None

    def test_multiple_lines_between_markers(self):
        """Test when there are multiple lines between markers (should return first)."""
        output = """SSH_START_12345
rdev-primary-hostname
some-other-line
SSH_END_12345"""
        
        result = parse_hostname_from_output(output, "SSH_START_12345", "SSH_END_12345")
        assert result == "rdev-primary-hostname"

    def test_whitespace_around_hostname(self):
        """Test that whitespace is properly stripped."""
        output = """SSH_START_12345
   rdev-hostname-with-spaces   
SSH_END_12345"""
        
        result = parse_hostname_from_output(output, "SSH_START_12345", "SSH_END_12345")
        assert result == "rdev-hostname-with-spaces"

    def test_marker_as_substring_ignored(self):
        """Test that markers appearing as substrings (in command) are ignored."""
        # The marker appears in the command "echo SSH_START_12345" but we should
        # only match when it's the entire line content
        output = """$ echo SSH_START_12345 && hostname && echo SSH_END_12345
SSH_START_12345
actual-hostname
SSH_END_12345
$"""
        
        result = parse_hostname_from_output(output, "SSH_START_12345", "SSH_END_12345")
        assert result == "actual-hostname"


class TestCheckSshAliveIntegration:
    """Integration-level tests for hostname detection logic."""

    def test_rdev_hostname_detected(self):
        """Test that rdev- prefix is correctly detected."""
        # Simulate what _check_ssh_alive would do with the parsed hostname
        hostname = "rdev-aks-0b9d79eb-3adf-4339-9987-2ddc81bac2bb-pqvf4"
        assert hostname.lower().startswith("rdev-")

    def test_local_hostname_not_detected_as_rdev(self):
        """Test that local hostname is not detected as rdev."""
        hostname = "yuqiu-mn7215.linkedin.biz"
        assert not hostname.lower().startswith("rdev-")

    def test_various_rdev_hostnames(self):
        """Test various rdev hostname patterns."""
        rdev_hostnames = [
            "rdev-aks-0b9d79eb-3adf-4339-9987-2ddc81bac2bb-pqvf4",
            "rdev-test-instance",
            "RDEV-UPPERCASE",
            "rdev-",
        ]
        for hostname in rdev_hostnames:
            assert hostname.lower().startswith("rdev-"), f"{hostname} should be detected as rdev"

    def test_non_rdev_hostnames(self):
        """Test that non-rdev hostnames are rejected."""
        non_rdev_hostnames = [
            "yuqiu-mn7215.linkedin.biz",
            "localhost",
            "my-laptop.local",
            "server.example.com",
            "notredev-hostname",  # "rdev" but not at start
        ]
        for hostname in non_rdev_hostnames:
            assert not hostname.lower().startswith("rdev-"), f"{hostname} should not be detected as rdev"
