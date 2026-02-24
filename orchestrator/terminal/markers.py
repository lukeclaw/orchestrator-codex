"""Marker-based terminal command utilities.

This module provides a safe, systematic way to send commands to terminal sessions
and parse their output without being fooled by command echo.

THE PROBLEM:
Terminal output includes the command line itself, so simple checks like
`"RESULT" in output` can match the command echo, not the actual result.

THE SOLUTION:
Always use start/end markers and only parse content BETWEEN them.

USAGE:
    from orchestrator.terminal.markers import MarkerCommand, send_and_parse

    # Simple yes/no check
    result = send_and_parse(
        tmux_sess, tmux_win,
        command="which screen",
        success_output="INSTALLED",
        failure_output="NOT_FOUND",
    )
    if result == "INSTALLED":
        ...

    # Get command output
    cmd = MarkerCommand("hostname")
    send_keys(tmux_sess, tmux_win, cmd.full_command, enter=True)
    time.sleep(1)
    output = capture_output(tmux_sess, tmux_win, lines=15)
    hostname = cmd.parse_result(output)

    # Wait for command completion
    from orchestrator.terminal.markers import wait_for_completion
    success = wait_for_completion(tmux_sess, tmux_win, timeout=60)
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _generate_marker_id() -> int:
    """Generate a random marker ID."""
    return random.randint(10000, 99999)


@dataclass
class MarkerCommand:
    """A terminal command wrapped with start/end markers for safe parsing.
    
    This class ensures that command results can be reliably extracted from
    terminal output without being fooled by command echo.
    
    Example:
        cmd = MarkerCommand("hostname")
        # cmd.full_command = "echo __MRK_START_12345__ && hostname && echo __MRK_END_12345__"
        
        send_keys(tmux_sess, tmux_win, cmd.full_command, enter=True)
        output = capture_output(tmux_sess, tmux_win, lines=15)
        result = cmd.parse_result(output)  # Returns only the hostname, not command echo
    """
    command: str
    prefix: str = "MRK"
    marker_id: int = field(default_factory=_generate_marker_id)

    @property
    def start_marker(self) -> str:
        return f"__{self.prefix}_START_{self.marker_id}__"

    @property
    def end_marker(self) -> str:
        return f"__{self.prefix}_END_{self.marker_id}__"

    @property
    def full_command(self) -> str:
        """The command wrapped with echo markers."""
        return f"echo {self.start_marker} && {self.command} && echo {self.end_marker}"

    def parse_result(self, output: str) -> str | None:
        """Parse the command result from terminal output.
        
        Returns the content between markers (stripped), or None if markers not found.
        """
        return parse_between_markers(output, self.start_marker, self.end_marker)

    def check_contains(self, output: str, value: str) -> bool:
        """Check if the parsed result contains a specific value.
        
        This is the SAFE way to check for values - it only looks between markers.
        """
        result = self.parse_result(output)
        if result is None:
            return False
        return value in result


def parse_between_markers(output: str, start_marker: str, end_marker: str) -> str | None:
    """Extract content between start and end markers.
    
    This is the SAFE way to parse terminal output. It ignores command echo lines
    and only returns content that appears AFTER the start marker line and BEFORE the end marker line.
    
    IMPORTANT: We only match markers when they appear as the ENTIRE line (stripped).
    This prevents both:
    1. Command echo lines from triggering (they contain more than just the marker)
    2. Result lines that happen to contain the marker string from being skipped
    
    Args:
        output: Raw terminal output (may include command echo)
        start_marker: Unique start marker string
        end_marker: Unique end marker string
        
    Returns:
        Stripped content between markers, or None if markers not found
    """
    in_section = False
    result_lines = []

    for line in output.splitlines():
        stripped = line.strip()
        # Match if the line equals the marker OR if the line is ONLY the marker
        # (allows for some terminal formatting characters)
        # But NOT if the line contains much more than the marker (command echo)
        if stripped == start_marker or (start_marker in stripped and len(stripped) < len(start_marker) + 5):
            in_section = True
            continue
        # Only look for end marker AFTER start marker is found. Terminal line
        # wrapping can place the end marker on its own line within the command
        # echo (before the actual output), which would cause an early break.
        if in_section:
            if stripped == end_marker or (end_marker in stripped and len(stripped) < len(end_marker) + 5):
                break
            result_lines.append(stripped)

    if not in_section:
        return None

    return "\n".join(result_lines).strip() or None


def parse_first_line(output: str, start_marker: str, end_marker: str) -> str | None:
    """Extract the first non-empty line between markers.
    
    Useful for commands that return a single value (e.g., hostname).
    """
    result = parse_between_markers(output, start_marker, end_marker)
    if result is None:
        return None

    for line in result.splitlines():
        if line.strip():
            return line.strip()
    return None


def check_result_contains(output: str, start_marker: str, end_marker: str, value: str) -> bool:
    """Check if the content between markers contains a specific value.
    
    This is the SAFE alternative to `value in output`.
    
    Args:
        output: Raw terminal output
        start_marker: Start marker
        end_marker: End marker
        value: The value to check for
        
    Returns:
        True if value is found between markers, False otherwise
    """
    result = parse_between_markers(output, start_marker, end_marker)
    if result is None:
        return False
    return value in result


def send_marker_command(
    send_keys_fn: Callable,
    capture_fn: Callable,
    tmux_sess: str,
    tmux_win: str,
    command: str,
    prefix: str = "CMD",
    wait_time: float = 2.0,
    capture_lines: int = 15,
) -> tuple[MarkerCommand, str | None]:
    """Send a marker-wrapped command and return parsed result.
    
    This is a convenience function that handles the full flow:
    1. Create MarkerCommand
    2. Send to terminal
    3. Wait for output
    4. Parse and return result
    
    Args:
        send_keys_fn: Function to send keys (e.g., tmux.send_keys)
        capture_fn: Function to capture output (e.g., tmux.capture_output)
        tmux_sess: tmux session name
        tmux_win: tmux window name
        command: The command to run
        prefix: Marker prefix for identification
        wait_time: Time to wait after sending command
        capture_lines: Number of lines to capture
        
    Returns:
        (MarkerCommand, parsed_result) - the command object and parsed result
    """
    cmd = MarkerCommand(command, prefix=prefix)
    send_keys_fn(tmux_sess, tmux_win, cmd.full_command, enter=True)
    time.sleep(wait_time)
    output = capture_fn(tmux_sess, tmux_win, lines=capture_lines)
    return cmd, cmd.parse_result(output)


def wait_for_completion(
    send_keys_fn: Callable,
    capture_fn: Callable,
    tmux_sess: str,
    tmux_win: str,
    timeout: int = 60,
    poll_interval: float = 2.0,
) -> bool:
    """Wait for a previous command to complete.
    
    Sends a marker command that will only execute after the previous command finishes.
    Polls until the marker appears in output or timeout is reached.
    
    Args:
        send_keys_fn: Function to send keys
        capture_fn: Function to capture output
        tmux_sess: tmux session name
        tmux_win: tmux window name
        timeout: Maximum seconds to wait
        poll_interval: Seconds between polls
        
    Returns:
        True if command completed within timeout, False otherwise
    """
    cmd = MarkerCommand("echo DONE", prefix="WAIT")
    send_keys_fn(tmux_sess, tmux_win, cmd.full_command, enter=True)

    start_time = time.time()
    while time.time() - start_time < timeout:
        time.sleep(poll_interval)
        output = capture_fn(tmux_sess, tmux_win, lines=15)

        if cmd.check_contains(output, "DONE"):
            return True

    logger.warning("Command did not complete within %d seconds", timeout)
    return False


def check_yes_no(
    send_keys_fn: Callable,
    capture_fn: Callable,
    tmux_sess: str,
    tmux_win: str,
    check_command: str,
    prefix: str = "CHK",
    wait_time: float = 2.0,
    retry_wait: float = 3.0,
    timeout: float | None = None,
    poll_interval: float = 1.0,
) -> bool | None:
    """Run a yes/no check command and return the result.
    
    Wraps the command to output YES on success, NO on failure.
    
    Args:
        send_keys_fn: Function to send keys
        capture_fn: Function to capture output
        tmux_sess: tmux session name
        tmux_win: tmux window name
        check_command: Command that returns 0 on success (e.g., "which screen")
        prefix: Marker prefix
        wait_time: Initial wait before first poll (kept for backward compat)
        retry_wait: Unused, kept for backward compatibility
        timeout: Total time to wait for result (default: wait_time + retry_wait)
        poll_interval: Time between capture attempts after initial wait
        
    Returns:
        True if command succeeded, False if failed, None if couldn't determine
    """
    if timeout is None:
        timeout = wait_time + retry_wait

    cmd = MarkerCommand(
        f"({check_command}) && echo YES || echo NO",
        prefix=prefix
    )

    send_keys_fn(tmux_sess, tmux_win, cmd.full_command, enter=True)
    time.sleep(wait_time)

    # Poll for markers until timeout (capture-first so we check at boundary)
    deadline = time.time() + (timeout - wait_time)
    result = None
    while True:
        output = capture_fn(tmux_sess, tmux_win, lines=15)
        result = cmd.parse_result(output)
        if result is not None:
            break
        if time.time() >= deadline:
            break
        time.sleep(poll_interval)

    if result is None:
        logger.warning("Could not parse yes/no result for command: %s", check_command)
        return None

    if "YES" in result:
        return True
    if "NO" in result:
        return False

    logger.warning("Unexpected yes/no result: %r", result)
    return None
