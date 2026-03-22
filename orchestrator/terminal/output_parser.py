"""Marker-based parsing utilities for captured terminal output."""

from __future__ import annotations


def parse_between_markers(output: str, start_marker: str, end_marker: str) -> list[str]:
    """Extract lines between start and end markers.

    Used to parse terminal output where command echo may contain similar strings.
    Only lines between markers (at start of line) are returned.

    Args:
        output: Raw terminal output
        start_marker: Unique start marker string
        end_marker: Unique end marker string

    Returns:
        List of lines between markers (stripped), empty list if markers not found
    """
    lines = output.split("\n")
    result_lines = []
    in_section = False

    for line in lines:
        stripped = line.strip()
        if stripped == start_marker or start_marker in stripped:
            in_section = True
            continue
        if stripped == end_marker or end_marker in stripped:
            break
        if in_section and stripped:
            result_lines.append(stripped)

    return result_lines


def parse_hostname_from_markers(output: str, start_marker: str, end_marker: str) -> str | None:
    """Extract hostname from captured terminal output between markers.

    The output includes the command line itself, so we need to find markers
    that appear at the START of a line (the actual echo output), not within
    the command line.

    Returns the hostname string or None if parsing failed.
    """
    lines = output.split("\n")
    start_line_idx = None
    end_line_idx = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == start_marker:
            start_line_idx = i
        elif stripped == end_marker and start_line_idx is not None:
            end_line_idx = i
            break

    if start_line_idx is None or end_line_idx is None:
        return None

    hostname_lines = [
        line.strip() for line in lines[start_line_idx + 1 : end_line_idx] if line.strip()
    ]
    if hostname_lines:
        return hostname_lines[0]
    return None


def check_screen_status_from_output(
    output: str, start_marker: str, end_marker: str
) -> tuple[bool, bool]:
    """Parse screen check output using markers.

    Args:
        output: Raw terminal output
        start_marker: Unique start marker (e.g., __SCRCHK_START_12345__)
        end_marker: Unique end marker (e.g., __SCRCHK_END_12345__)

    Returns:
        (screen_exists: bool, claude_running: bool)
    """
    screen_exists = False
    claude_running = False

    lines = output.split("\n")
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
