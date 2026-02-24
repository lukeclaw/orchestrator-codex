"""Tier 1 pattern detection on captured terminal output (regex-based).

NOTE: Worker status is managed by Claude Code hooks (see worker/cli_scripts.py).
This module only detects specific events like PR creation, test results, etc.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class EventType(Enum):
    TEST_PASS = "test_pass"
    TEST_FAIL = "test_fail"
    PR_CREATED = "pr_created"
    BUILD_SUCCESS = "build_success"
    BUILD_FAILURE = "build_failure"
    ERROR = "error"


@dataclass
class OutputEvent:
    event_type: EventType
    data: dict
    raw_match: str = ""


# --- Patterns ---

# Test results
TEST_PASS_PATTERNS = [
    re.compile(r"(\d+) passed", re.IGNORECASE),
    re.compile(r"All tests pass", re.IGNORECASE),
    re.compile(r"Tests:\s+\d+ passed", re.IGNORECASE),
    re.compile(r"BUILD SUCCESSFUL", re.IGNORECASE),
]

TEST_FAIL_PATTERNS = [
    re.compile(r"(\d+) failed", re.IGNORECASE),
    re.compile(r"FAILED\s+tests?/", re.IGNORECASE),
    re.compile(r"Tests:\s+\d+ failed", re.IGNORECASE),
    re.compile(r"AssertionError", re.IGNORECASE),
]

# PR creation
PR_PATTERNS = [
    re.compile(r"(?:PR|pull request)\s*#?(\d+)\s*created", re.IGNORECASE),
    re.compile(r"(https?://\S+/pull/\d+)"),
    re.compile(r"Created pull request\s*#?(\d+)", re.IGNORECASE),
]

# Build results
BUILD_SUCCESS_PATTERNS = [
    re.compile(r"Build succeeded", re.IGNORECASE),
    re.compile(r"Compiled successfully", re.IGNORECASE),
    re.compile(r"BUILD SUCCESSFUL", re.IGNORECASE),
]

BUILD_FAILURE_PATTERNS = [
    re.compile(r"Build failed", re.IGNORECASE),
    re.compile(r"Compilation error", re.IGNORECASE),
    re.compile(r"BUILD FAILED", re.IGNORECASE),
    re.compile(r"ERROR in", re.IGNORECASE),
]

# Error patterns
ERROR_PATTERNS = [
    re.compile(r"Traceback \(most recent call last\)", re.IGNORECASE),
    re.compile(r"Error:\s+(.+)", re.IGNORECASE),
    re.compile(r"fatal:\s+(.+)", re.IGNORECASE),
    re.compile(r"ENOENT|EPERM|EACCES"),
]




# --- Marker-based parsing utilities ---

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
    lines = output.split('\n')
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
    lines = output.split('\n')
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

    hostname_lines = [l.strip() for l in lines[start_line_idx + 1:end_line_idx] if l.strip()]
    if hostname_lines:
        return hostname_lines[0]
    return None


def check_screen_status_from_output(
    output: str,
    start_marker: str,
    end_marker: str
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


def parse_output(output: str) -> list[OutputEvent]:
    """Parse terminal output and return detected events."""
    events = []

    # Check for PR creation
    for pattern in PR_PATTERNS:
        match = pattern.search(output)
        if match:
            events.append(OutputEvent(
                event_type=EventType.PR_CREATED,
                data={"match": match.group(0), "groups": match.groups()},
                raw_match=match.group(0),
            ))

    # Check for test results
    for pattern in TEST_PASS_PATTERNS:
        match = pattern.search(output)
        if match:
            events.append(OutputEvent(
                event_type=EventType.TEST_PASS,
                data={"match": match.group(0)},
                raw_match=match.group(0),
            ))
            break

    for pattern in TEST_FAIL_PATTERNS:
        match = pattern.search(output)
        if match:
            events.append(OutputEvent(
                event_type=EventType.TEST_FAIL,
                data={"match": match.group(0)},
                raw_match=match.group(0),
            ))
            break

    # Check for build results
    for pattern in BUILD_SUCCESS_PATTERNS:
        match = pattern.search(output)
        if match:
            events.append(OutputEvent(
                event_type=EventType.BUILD_SUCCESS,
                data={"match": match.group(0)},
                raw_match=match.group(0),
            ))
            break

    for pattern in BUILD_FAILURE_PATTERNS:
        match = pattern.search(output)
        if match:
            events.append(OutputEvent(
                event_type=EventType.BUILD_FAILURE,
                data={"match": match.group(0)},
                raw_match=match.group(0),
            ))
            break

    # Check for errors (only if no other specific events found)
    if not events:
        for pattern in ERROR_PATTERNS:
            match = pattern.search(output)
            if match:
                events.append(OutputEvent(
                    event_type=EventType.ERROR,
                    data={"match": match.group(0)},
                    raw_match=match.group(0),
                ))
                break

    return events
