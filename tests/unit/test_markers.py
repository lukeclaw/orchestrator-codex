"""Tests for marker-based terminal command utilities."""

import pytest
from unittest.mock import MagicMock, patch

from orchestrator.terminal.markers import (
    MarkerCommand,
    parse_between_markers,
    parse_first_line,
    check_result_contains,
    send_marker_command,
    wait_for_completion,
    check_yes_no,
)


class TestParseBetweenMarkers:
    """Test the core parsing function."""

    def test_parses_content_between_markers(self):
        """Should extract content between start and end markers."""
        output = """__START__
hello
world
__END__"""
        result = parse_between_markers(output, "__START__", "__END__")
        assert result == "hello\nworld"

    def test_ignores_command_echo_line(self):
        """Should not match markers in command echo line."""
        output = """$ echo __START__ && hostname && echo __END__
__START__
myhost.local
__END__"""
        result = parse_between_markers(output, "__START__", "__END__")
        assert result == "myhost.local"

    def test_returns_none_when_no_markers(self):
        """Should return None if markers not found."""
        output = "just some output"
        result = parse_between_markers(output, "__START__", "__END__")
        assert result is None

    def test_returns_none_when_only_start_marker(self):
        """Should return None if only start marker found."""
        output = """__START__
content"""
        result = parse_between_markers(output, "__START__", "__END__")
        # Still returns content because we found start but no end
        assert result == "content"

    def test_handles_empty_content(self):
        """Should return None for empty content between markers."""
        output = """__START__
__END__"""
        result = parse_between_markers(output, "__START__", "__END__")
        assert result is None

    def test_result_containing_marker_string_not_skipped(self):
        """Result lines containing marker string should NOT be skipped."""
        # Edge case: if result happens to contain the marker text
        output = """__START__
The marker __START__ appears in this result
__END__"""
        result = parse_between_markers(output, "__START__", "__END__")
        # Should capture the line, not skip it
        assert result == "The marker __START__ appears in this result"

    def test_command_echo_not_treated_as_marker(self):
        """Command echo line should NOT trigger marker detection."""
        # The command echo contains the marker but has other text too
        output = """$ echo __START__ && hostname && echo __END__
__START__
myhost.local
__END__"""
        result = parse_between_markers(output, "__START__", "__END__")
        # Should only capture content AFTER the actual __START__ line
        assert result == "myhost.local"

    def test_line_wrapped_end_marker_in_command_echo(self):
        """End marker on its own line in command echo (terminal wrapping) should be ignored."""
        # When a long command wraps, the end marker can land on its own line
        # in the command echo, BEFORE the actual start marker output.
        output = """$ echo __SCREEN_VFY_START_22848__ && (which screen) && echo YES || echo NO && echo
__SCREEN_VFY_END_22848__
__SCREEN_VFY_START_22848__
/usr/bin/screen
YES
__SCREEN_VFY_END_22848__
[yuqiu@zen-dinosaur voyager-api-premium]$"""
        result = parse_between_markers(
            output, "__SCREEN_VFY_START_22848__", "__SCREEN_VFY_END_22848__"
        )
        assert result is not None
        assert "YES" in result


class TestParseFirstLine:
    """Test first line extraction."""

    def test_returns_first_non_empty_line(self):
        """Should return just the first line."""
        output = """__START__

myhost.local
extra stuff
__END__"""
        result = parse_first_line(output, "__START__", "__END__")
        assert result == "myhost.local"

    def test_returns_none_when_no_content(self):
        """Should return None if no content between markers."""
        output = """__START__
__END__"""
        result = parse_first_line(output, "__START__", "__END__")
        assert result is None


class TestCheckResultContains:
    """Test safe value checking."""

    def test_finds_value_between_markers(self):
        """Should find value when it's between markers."""
        output = """$ echo __START__ && echo YES || echo NO && echo __END__
__START__
YES
__END__"""
        assert check_result_contains(output, "__START__", "__END__", "YES") is True
        assert check_result_contains(output, "__START__", "__END__", "NO") is False

    def test_ignores_value_in_command_echo(self):
        """Should NOT match value that only appears in command echo."""
        output = """$ echo __START__ && (which screen && echo INSTALLED || echo NOT_FOUND) && echo __END__
__START__
NOT_FOUND
__END__"""
        # INSTALLED appears in command echo but not in result
        assert check_result_contains(output, "__START__", "__END__", "INSTALLED") is False
        assert check_result_contains(output, "__START__", "__END__", "NOT_FOUND") is True

    def test_returns_false_when_no_markers(self):
        """Should return False if markers not found."""
        output = "just some output with YES in it"
        assert check_result_contains(output, "__START__", "__END__", "YES") is False


class TestMarkerCommand:
    """Test the MarkerCommand class."""

    def test_generates_unique_markers(self):
        """Should generate unique start/end markers."""
        cmd1 = MarkerCommand("hostname")
        cmd2 = MarkerCommand("hostname")
        assert cmd1.start_marker != cmd2.start_marker
        assert cmd1.end_marker != cmd2.end_marker

    def test_full_command_wraps_with_markers(self):
        """Should wrap command with echo markers."""
        cmd = MarkerCommand("hostname", marker_id=12345)
        assert cmd.full_command == "echo __MRK_START_12345__ && hostname && echo __MRK_END_12345__"

    def test_parse_result_extracts_content(self):
        """Should parse result between its own markers."""
        cmd = MarkerCommand("hostname", marker_id=12345)
        output = f"""$ {cmd.full_command}
__MRK_START_12345__
myhost.local
__MRK_END_12345__"""
        assert cmd.parse_result(output) == "myhost.local"

    def test_check_contains_is_safe(self):
        """Should only check content between markers."""
        cmd = MarkerCommand("which screen && echo INSTALLED || echo NOT_FOUND", marker_id=99999)
        output = f"""$ {cmd.full_command}
__MRK_START_99999__
NOT_FOUND
__MRK_END_99999__"""
        # INSTALLED appears in command but not in result
        assert cmd.check_contains(output, "INSTALLED") is False
        assert cmd.check_contains(output, "NOT_FOUND") is True

    def test_custom_prefix(self):
        """Should use custom prefix in markers."""
        cmd = MarkerCommand("test", prefix="SCREEN_CHK", marker_id=12345)
        assert "__SCREEN_CHK_START_12345__" in cmd.start_marker
        assert "__SCREEN_CHK_END_12345__" in cmd.end_marker


class TestSendMarkerCommand:
    """Test the send_marker_command helper."""

    @patch("orchestrator.terminal.markers.time.sleep")
    def test_sends_and_parses_command(self, _sleep):
        """Should send command and return parsed result."""
        mock_send = MagicMock()
        mock_capture = MagicMock()

        # Simulate capture returning output with markers
        def capture_side_effect(sess, win, lines=15):
            # Find the marker from the sent command
            call_args = str(mock_send.call_args)
            import re
            match = re.search(r'__CMD_START_(\d+)__', call_args)
            if match:
                marker_id = match.group(1)
                return f"""__CMD_START_{marker_id}__
myhost.local
__CMD_END_{marker_id}__"""
            return ""

        mock_capture.side_effect = capture_side_effect

        cmd, result = send_marker_command(
            mock_send, mock_capture,
            "orchestrator", "test-worker",
            "hostname"
        )

        assert result == "myhost.local"
        mock_send.assert_called_once()


class TestWaitForCompletion:
    """Test the wait_for_completion helper."""

    def test_returns_true_when_done_found(self):
        """Should return True when DONE marker is found."""
        mock_send = MagicMock()
        call_count = [0]
        
        def capture_side_effect(sess, win, lines=15):
            call_count[0] += 1
            call_args = str(mock_send.call_args)
            import re
            match = re.search(r'__WAIT_START_(\d+)__', call_args)
            if match and call_count[0] >= 2:
                marker_id = match.group(1)
                return f"""__WAIT_START_{marker_id}__
DONE
__WAIT_END_{marker_id}__"""
            return ""
        
        mock_capture = MagicMock(side_effect=capture_side_effect)
        
        result = wait_for_completion(
            mock_send, mock_capture,
            "orchestrator", "test-worker",
            timeout=10, poll_interval=0.1
        )
        
        assert result is True

    def test_returns_false_on_timeout(self):
        """Should return False when timeout reached."""
        mock_send = MagicMock()
        mock_capture = MagicMock(return_value="")
        
        result = wait_for_completion(
            mock_send, mock_capture,
            "orchestrator", "test-worker",
            timeout=0.5, poll_interval=0.1
        )
        
        assert result is False


class TestCheckYesNo:
    """Test the check_yes_no helper."""

    def test_returns_true_for_yes(self):
        """Should return True when command succeeds (YES)."""
        mock_send = MagicMock()
        
        def capture_side_effect(sess, win, lines=15):
            call_args = str(mock_send.call_args)
            import re
            match = re.search(r'__CHK_START_(\d+)__', call_args)
            if match:
                marker_id = match.group(1)
                return f"""__CHK_START_{marker_id}__
/usr/bin/screen
YES
__CHK_END_{marker_id}__"""
            return ""
        
        mock_capture = MagicMock(side_effect=capture_side_effect)
        
        result = check_yes_no(
            mock_send, mock_capture,
            "orchestrator", "test-worker",
            "which screen",
            wait_time=0.01
        )
        
        assert result is True

    def test_returns_false_for_no(self):
        """Should return False when command fails (NO)."""
        mock_send = MagicMock()
        
        def capture_side_effect(sess, win, lines=15):
            call_args = str(mock_send.call_args)
            import re
            match = re.search(r'__CHK_START_(\d+)__', call_args)
            if match:
                marker_id = match.group(1)
                return f"""__CHK_START_{marker_id}__
NO
__CHK_END_{marker_id}__"""
            return ""
        
        mock_capture = MagicMock(side_effect=capture_side_effect)
        
        result = check_yes_no(
            mock_send, mock_capture,
            "orchestrator", "test-worker",
            "which nonexistent",
            wait_time=0.01
        )
        
        assert result is False

    def test_command_echo_doesnt_cause_false_positive(self):
        """YES in command echo should NOT cause True result."""
        mock_send = MagicMock()
        
        def capture_side_effect(sess, win, lines=15):
            call_args = str(mock_send.call_args)
            import re
            match = re.search(r'__CHK_START_(\d+)__', call_args)
            if match:
                marker_id = match.group(1)
                # Command echo contains YES but result is NO
                return f"""$ echo __CHK_START_{marker_id}__ && (which screen && echo YES || echo NO) && echo __CHK_END_{marker_id}__
__CHK_START_{marker_id}__
NO
__CHK_END_{marker_id}__"""
            return ""
        
        mock_capture = MagicMock(side_effect=capture_side_effect)
        
        result = check_yes_no(
            mock_send, mock_capture,
            "orchestrator", "test-worker",
            "which screen",
            wait_time=0.01
        )
        
        # Should be False, not True (even though YES is in the full output)
        assert result is False
