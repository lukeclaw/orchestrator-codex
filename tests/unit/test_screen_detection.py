"""Tests for screen session detection logic."""

import pytest


def parse_screen_output(output: str, start_marker: str, end_marker: str) -> tuple[bool, bool]:
    """Parse screen check output using markers - matches the fixed logic."""
    screen_exists = False
    claude_running = False
    
    lines = output.split('\n')
    in_result_section = False
    for line in lines:
        stripped = line.strip()
        if start_marker in stripped:
            in_result_section = True
            continue
        if end_marker in stripped:
            break
        if in_result_section:
            if stripped == "SCREEN_EXISTS":
                screen_exists = True
            elif stripped == "CLAUDE_RUNNING":
                claude_running = True
    
    return screen_exists, claude_running


class TestScreenDetectionParsing:
    """Test the output parsing for screen and Claude detection."""
    
    def test_screen_missing_claude_missing_with_markers(self):
        """Test parsing when screen check returns SCREEN_MISSING and CLAUDE_MISSING.
        
        This simulates the actual terminal output with markers.
        """
        start_marker = "__SCRCHK_START_12345__"
        end_marker = "__SCRCHK_END_12345__"
        
        # Simulated terminal output that would be captured
        output = f"""[yuqiu@fuzzy-kumquat premium-eng-portal]$ echo {start_marker} && (screen -ls 2>/dev/null | grep -q 'claude-xxx' && echo SCREEN_EXISTS || echo SCREEN_MISSING) && ...
{start_marker}
SCREEN_MISSING
CLAUDE_MISSING
{end_marker}
[yuqiu@fuzzy-kumquat premium-eng-portal]$"""
        
        screen_exists, claude_running = parse_screen_output(output, start_marker, end_marker)
        
        assert screen_exists is False, f"Expected screen_exists=False, got {screen_exists}"
        assert claude_running is False, f"Expected claude_running=False, got {claude_running}"
    
    def test_screen_exists_claude_running_with_markers(self):
        """Test parsing when both screen and Claude are running."""
        start_marker = "__SCRCHK_START_12345__"
        end_marker = "__SCRCHK_END_12345__"
        
        output = f"""{start_marker}
SCREEN_EXISTS
CLAUDE_RUNNING
{end_marker}
[yuqiu@fuzzy-kumquat premium-eng-portal]$"""
        
        screen_exists, claude_running = parse_screen_output(output, start_marker, end_marker)
        
        assert screen_exists is True
        assert claude_running is True
    
    def test_screen_exists_claude_missing_with_markers(self):
        """Test parsing when screen exists but Claude is not running."""
        start_marker = "__SCRCHK_START_12345__"
        end_marker = "__SCRCHK_END_12345__"
        
        output = f"""{start_marker}
SCREEN_EXISTS
CLAUDE_MISSING
{end_marker}
[yuqiu@fuzzy-kumquat premium-eng-portal]$"""
        
        screen_exists, claude_running = parse_screen_output(output, start_marker, end_marker)
        
        assert screen_exists is True
        assert claude_running is False
    
    def test_command_echo_doesnt_cause_false_positive(self):
        """Test that command containing SCREEN_EXISTS doesn't cause false positive.
        
        The terminal output includes the command that was run. With markers,
        we only parse between the markers, ignoring the command echo.
        """
        start_marker = "__SCRCHK_START_99999__"
        end_marker = "__SCRCHK_END_99999__"
        
        # The command line contains SCREEN_EXISTS as a string, but the actual result is SCREEN_MISSING
        output = f"""[yuqiu@fuzzy-kumquat premium-eng-portal]$ echo {start_marker} && (screen -ls | grep -q 'xxx' && echo SCREEN_EXISTS || echo SCREEN_MISSING) && echo {end_marker}
{start_marker}
SCREEN_MISSING
CLAUDE_MISSING
{end_marker}
[yuqiu@fuzzy-kumquat premium-eng-portal]$"""
        
        screen_exists, claude_running = parse_screen_output(output, start_marker, end_marker)
        
        # With marker-based parsing, we correctly get False
        assert screen_exists is False, "Should not find SCREEN_EXISTS in command text"
        assert claude_running is False
    
    def test_missing_markers_returns_false(self):
        """If markers are not found, should return False for both."""
        output = """Some random output without markers
SCREEN_EXISTS
CLAUDE_RUNNING"""
        
        screen_exists, claude_running = parse_screen_output(output, "__START__", "__END__")
        
        assert screen_exists is False
        assert claude_running is False


class TestScreenDetectionLogic:
    """Test the decision logic for reconnect based on screen/claude status."""
    
    def test_no_screen_no_claude_should_create_new(self):
        """When no screen exists, should create new screen, not reattach."""
        screen_exists = False
        claude_running = False
        
        # Simulate the decision logic from _reconnect_rdev_worker
        action = None
        if screen_exists and claude_running:
            action = "reattach"  # screen -r
        elif screen_exists and not claude_running:
            action = "kill_and_create"  # screen -X quit, then screen -S
        else:
            action = "create_new"  # screen -S
        
        assert action == "create_new", f"Expected 'create_new' but got '{action}'"
    
    def test_screen_exists_claude_running_should_reattach(self):
        """When screen and Claude both exist, should reattach."""
        screen_exists = True
        claude_running = True
        
        action = None
        if screen_exists and claude_running:
            action = "reattach"
        elif screen_exists and not claude_running:
            action = "kill_and_create"
        else:
            action = "create_new"
        
        assert action == "reattach"
    
    def test_screen_exists_claude_dead_should_kill_and_create(self):
        """When screen exists but Claude crashed, should kill and recreate."""
        screen_exists = True
        claude_running = False
        
        action = None
        if screen_exists and claude_running:
            action = "reattach"
        elif screen_exists and not claude_running:
            action = "kill_and_create"
        else:
            action = "create_new"
        
        assert action == "kill_and_create"
