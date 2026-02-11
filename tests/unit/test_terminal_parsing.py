"""Unit tests for terminal output parsing utilities."""

import pytest
from unittest.mock import patch, MagicMock


class TestMarkerParsing:
    """Test marker-based output parsing."""

    def test_parse_between_markers_extracts_content(self):
        """Should extract lines between start and end markers."""
        from orchestrator.session.reconnect import parse_hostname_from_output
        
        start = "SSH_START_12345"
        end = "SSH_END_12345"
        output = f"""$ echo {start} && hostname && echo {end}
{start}
rdev-fuzzy-kumquat
{end}
$"""
        
        result = parse_hostname_from_output(output, start, end)
        assert result == "rdev-fuzzy-kumquat"

    def test_parse_ignores_command_echo_with_marker_text(self):
        """Should not match markers that appear in command line."""
        from orchestrator.session.reconnect import parse_hostname_from_output
        
        start = "SSH_START_99999"
        end = "SSH_END_99999"
        # The command contains the markers, but actual output shows different hostname
        output = f"""[user@local]$ echo {start} && hostname && echo {end}
{start}
local-machine
{end}
[user@local]$"""
        
        result = parse_hostname_from_output(output, start, end)
        assert result == "local-machine"
        assert result != "rdev"  # Should not find rdev- prefix

    def test_parse_handles_missing_end_marker(self):
        """Should return None if end marker is missing."""
        from orchestrator.session.reconnect import parse_hostname_from_output
        
        start = "SSH_START_12345"
        end = "SSH_END_12345"
        output = f"""{start}
rdev-test
incomplete output..."""
        
        result = parse_hostname_from_output(output, start, end)
        assert result is None

    def test_parse_handles_missing_start_marker(self):
        """Should return None if start marker is missing."""
        from orchestrator.session.reconnect import parse_hostname_from_output
        
        start = "SSH_START_12345"
        end = "SSH_END_12345"
        output = f"""some random output
rdev-test
{end}"""
        
        result = parse_hostname_from_output(output, start, end)
        assert result is None


class TestTunnelCheck:
    """Test tunnel alive detection."""

    @patch('orchestrator.session.health.capture_output')
    def test_tunnel_alive_detects_active_ssh(self, mock_capture):
        """Active SSH tunnel command should be detected as alive."""
        from orchestrator.session.health import check_tunnel_alive
        
        mock_capture.return_value = "ssh -L 8093:localhost:8093 user@rdev-host\n"
        
        result = check_tunnel_alive("orchestrator", "test-tunnel")
        assert result == True

    @patch('orchestrator.session.health.capture_output')
    def test_tunnel_dead_detects_shell_prompt(self, mock_capture):
        """Shell prompt indicates tunnel has exited."""
        from orchestrator.session.health import check_tunnel_alive
        
        # Various shell prompts
        prompts = [
            "user@macbook ~ % ",
            "bash-5.1$ ",
            "[user@host ~]$ ",
            "❯ ",
        ]
        
        for prompt in prompts:
            mock_capture.return_value = f"Connection closed\n{prompt}"
            result = check_tunnel_alive("orchestrator", "test-tunnel")
            assert result == False, f"Shell prompt '{prompt}' should indicate dead tunnel"

    @patch('orchestrator.session.health.capture_output')
    def test_tunnel_dead_detects_connection_error(self, mock_capture):
        """Connection errors should indicate dead tunnel."""
        from orchestrator.session.health import check_tunnel_alive
        
        errors = [
            "Connection closed by remote host.",
            "Connection refused",
            "Connection timed out",
            "Connection reset by peer",
            "broken pipe",
            "Host key verification failed",
            "Permission denied (publickey)",
            "Could not resolve hostname",
            "Network is unreachable",
        ]
        
        for error in errors:
            mock_capture.return_value = f"{error}\n$ "
            result = check_tunnel_alive("orchestrator", "test-tunnel")
            assert result == False, f"Error '{error}' should indicate dead tunnel"


class TestScreenCheck:
    """Test screen session detection via tmux."""

    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.send_keys')
    def test_screen_exists_detected_correctly(self, mock_send, mock_capture):
        """SCREEN_EXISTS in output between markers should be detected."""
        from orchestrator.session.reconnect import check_screen_exists_via_tmux
        
        # The function generates its own markers, so we need to capture what it sends
        # and return output with those markers. For simplicity, test the parsing logic directly.
        # Simulate that capture returns output with markers matching what was sent
        def capture_side_effect(tmux_sess, tmux_win, lines=20):
            # Extract the marker from the last send_keys call
            if mock_send.call_args_list:
                last_call = str(mock_send.call_args_list[-1])
                # Find marker pattern in the call
                import re
                match = re.search(r'__SCRCHK_START_(\d+)__', last_call)
                if match:
                    marker_id = match.group(1)
                    return f"""__SCRCHK_START_{marker_id}__
SCREEN_EXISTS
CLAUDE_RUNNING
__SCRCHK_END_{marker_id}__"""
            return ""
        
        mock_capture.side_effect = capture_side_effect
        
        screen_exists, claude_running = check_screen_exists_via_tmux(
            "orchestrator", "test-worker", "claude-test", "test-session-id"
        )
        
        assert screen_exists == True
        assert claude_running == True

    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.send_keys')
    def test_screen_missing_detected_correctly(self, mock_send, mock_capture):
        """SCREEN_MISSING in output should be detected."""
        from orchestrator.session.reconnect import check_screen_exists_via_tmux
        
        def capture_side_effect(tmux_sess, tmux_win, lines=20):
            if mock_send.call_args_list:
                last_call = str(mock_send.call_args_list[-1])
                import re
                match = re.search(r'__SCRCHK_START_(\d+)__', last_call)
                if match:
                    marker_id = match.group(1)
                    return f"""__SCRCHK_START_{marker_id}__
SCREEN_MISSING
CLAUDE_MISSING
__SCRCHK_END_{marker_id}__"""
            return ""
        
        mock_capture.side_effect = capture_side_effect
        
        screen_exists, claude_running = check_screen_exists_via_tmux(
            "orchestrator", "test-worker", "claude-test", "test-session-id"
        )
        
        assert screen_exists == False
        assert claude_running == False

    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.send_keys')
    def test_claude_running_detected_correctly(self, mock_send, mock_capture):
        """CLAUDE_RUNNING should be detected when present."""
        from orchestrator.session.reconnect import check_screen_exists_via_tmux
        
        def capture_side_effect(tmux_sess, tmux_win, lines=20):
            if mock_send.call_args_list:
                last_call = str(mock_send.call_args_list[-1])
                import re
                match = re.search(r'__SCRCHK_START_(\d+)__', last_call)
                if match:
                    marker_id = match.group(1)
                    return f"""__SCRCHK_START_{marker_id}__
SCREEN_EXISTS
CLAUDE_RUNNING
__SCRCHK_END_{marker_id}__"""
            return ""
        
        mock_capture.side_effect = capture_side_effect
        
        screen_exists, claude_running = check_screen_exists_via_tmux(
            "orchestrator", "test-worker", "claude-test", "test-session-id"
        )
        
        assert claude_running == True

    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.send_keys')
    def test_claude_missing_detected_correctly(self, mock_send, mock_capture):
        """CLAUDE_MISSING should be detected (screen exists but Claude crashed)."""
        from orchestrator.session.reconnect import check_screen_exists_via_tmux
        
        def capture_side_effect(tmux_sess, tmux_win, lines=20):
            if mock_send.call_args_list:
                last_call = str(mock_send.call_args_list[-1])
                import re
                match = re.search(r'__SCRCHK_START_(\d+)__', last_call)
                if match:
                    marker_id = match.group(1)
                    return f"""__SCRCHK_START_{marker_id}__
SCREEN_EXISTS
CLAUDE_MISSING
__SCRCHK_END_{marker_id}__"""
            return ""
        
        mock_capture.side_effect = capture_side_effect
        
        screen_exists, claude_running = check_screen_exists_via_tmux(
            "orchestrator", "test-worker", "claude-test", "test-session-id"
        )
        
        assert screen_exists == True
        assert claude_running == False


class TestSSHCheck:
    """Test SSH connection alive check."""

    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.send_keys')
    def test_ssh_alive_validates_hostname(self, mock_send, mock_capture):
        """SSH is alive if hostname starts with rdev-."""
        from orchestrator.session.reconnect import check_ssh_alive
        
        def capture_side_effect(tmux_sess, tmux_win, lines=15):
            if mock_send.call_args_list:
                last_call = str(mock_send.call_args_list[-1])
                import re
                match = re.search(r'SSH_START_(\d+)', last_call)
                if match:
                    marker_id = match.group(1)
                    return f"""SSH_START_{marker_id}
rdev-fuzzy-kumquat
SSH_END_{marker_id}"""
            return ""
        
        mock_capture.side_effect = capture_side_effect
        
        result = check_ssh_alive("orchestrator", "test-worker", "user/rdev-vm")
        assert result == True

    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.send_keys')
    def test_ssh_dead_local_hostname(self, mock_send, mock_capture):
        """SSH is dead if hostname is local machine (not rdev-)."""
        from orchestrator.session.reconnect import check_ssh_alive
        
        def capture_side_effect(tmux_sess, tmux_win, lines=15):
            if mock_send.call_args_list:
                last_call = str(mock_send.call_args_list[-1])
                import re
                match = re.search(r'SSH_START_(\d+)', last_call)
                if match:
                    marker_id = match.group(1)
                    return f"""SSH_START_{marker_id}
macbook-pro.local
SSH_END_{marker_id}"""
            return ""
        
        mock_capture.side_effect = capture_side_effect
        
        result = check_ssh_alive("orchestrator", "test-worker", "user/rdev-vm")
        assert result == False

    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.send_keys')
    def test_ssh_dead_no_output(self, mock_send, mock_capture):
        """SSH is dead if no parseable output."""
        from orchestrator.session.reconnect import check_ssh_alive
        
        mock_capture.return_value = ""
        
        result = check_ssh_alive("orchestrator", "test-worker", "user/rdev-vm", retries=1)
        assert result == False


class TestInsideScreenCheck:
    """Test inside-screen detection via $STY environment variable."""

    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.send_keys')
    def test_inside_screen_detected(self, mock_send, mock_capture):
        """Should detect when $STY is set (inside screen)."""
        from orchestrator.session.reconnect import check_inside_screen
        
        def capture_side_effect(tmux_sess, tmux_win, lines=10):
            if mock_send.call_args_list:
                last_call = str(mock_send.call_args_list[-1])
                import re
                match = re.search(r'STY_START_(\d+)', last_call)
                if match:
                    marker_id = match.group(1)
                    # Simulate being inside screen - $STY has value
                    return f"""STY_START_{marker_id}
12345.claude-session-abc
STY_END_{marker_id}"""
            return ""
        
        mock_capture.side_effect = capture_side_effect
        
        result = check_inside_screen("orchestrator", "test-worker")
        assert result == True

    @patch('orchestrator.session.reconnect.capture_output')
    @patch('orchestrator.session.reconnect.send_keys')
    def test_not_inside_screen_detected(self, mock_send, mock_capture):
        """Should detect when $STY is empty (not inside screen)."""
        from orchestrator.session.reconnect import check_inside_screen
        
        def capture_side_effect(tmux_sess, tmux_win, lines=10):
            if mock_send.call_args_list:
                last_call = str(mock_send.call_args_list[-1])
                import re
                match = re.search(r'STY_START_(\d+)', last_call)
                if match:
                    marker_id = match.group(1)
                    # Simulate NOT being inside screen - $STY is empty
                    return f"""STY_START_{marker_id}

STY_END_{marker_id}"""
            return ""
        
        mock_capture.side_effect = capture_side_effect
        
        result = check_inside_screen("orchestrator", "test-worker")
        assert result == False


class TestSubprocessScreenCheck:
    """Test subprocess-based screen/claude check (used before touching tmux)."""

    @patch('subprocess.run')
    def test_screen_and_claude_rdev_alive(self, mock_run):
        """Should detect when both screen and Claude are running."""
        from orchestrator.session.health import check_screen_and_claude_rdev
        
        # Mock successful SSH with both screen and Claude running
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="SCREEN_EXISTS\nCLAUDE_RUNNING\n",
            stderr=""
        )
        
        status, reason = check_screen_and_claude_rdev("user/rdev-vm", "test-session-id")
        
        assert status == "alive"
        assert "running" in reason.lower() or "exists" in reason.lower()

    @patch('subprocess.run')
    def test_screen_and_claude_rdev_dead(self, mock_run):
        """Should detect when Claude is not running."""
        from orchestrator.session.health import check_screen_and_claude_rdev
        
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="SCREEN_MISSING\nCLAUDE_MISSING\n",
            stderr=""
        )
        
        status, reason = check_screen_and_claude_rdev("user/rdev-vm", "test-session-id")
        
        assert status == "dead"

    @patch('subprocess.run')
    def test_screen_and_claude_rdev_ssh_failure(self, mock_run):
        """Should handle SSH connection failures gracefully."""
        from orchestrator.session.health import check_screen_and_claude_rdev
        
        mock_run.side_effect = Exception("Connection refused")
        
        status, reason = check_screen_and_claude_rdev("user/rdev-vm", "test-session-id")
        
        # The function returns screen_detached on failure (not unknown)
        assert status in ("unknown", "screen_detached", "dead")
        # Just verify it doesn't crash and returns some status


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
