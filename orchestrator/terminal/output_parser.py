"""Tier 1 pattern detection on captured terminal output (regex-based)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class SessionState(Enum):
    IDLE = "idle"
    WORKING = "working"
    WAITING = "waiting"
    ERROR = "error"
    UNKNOWN = "unknown"


class EventType(Enum):
    STATE_CHANGE = "state_change"
    TEST_PASS = "test_pass"
    TEST_FAIL = "test_fail"
    PR_CREATED = "pr_created"
    BUILD_SUCCESS = "build_success"
    BUILD_FAILURE = "build_failure"
    ERROR = "error"
    COMPACT = "compact"


@dataclass
class OutputEvent:
    event_type: EventType
    data: dict
    raw_match: str = ""


# --- Patterns ---

# Claude Code idle prompt (waiting for user input)
IDLE_PATTERNS = [
    re.compile(r"^>\s*$", re.MULTILINE),  # bare ">" prompt
    re.compile(r"^\s*\$\s*$", re.MULTILINE),  # bare "$" prompt
    re.compile(r"What would you like to do", re.IGNORECASE),
]

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

# Context compaction (Claude Code specific)
COMPACT_PATTERNS = [
    re.compile(r"/compact", re.IGNORECASE),
    re.compile(r"Context.*compacted", re.IGNORECASE),
    re.compile(r"conversation.*summarized", re.IGNORECASE),
]

# Waiting for user input (Claude Code permission/continuation prompts)
WAITING_PATTERNS = [
    # Claude Code tool approval prompts
    re.compile(r"Allow\s+\w+", re.IGNORECASE),  # "Allow Read", "Allow Write", etc.
    re.compile(r"Do you want to proceed", re.IGNORECASE),
    re.compile(r"Shall I\s+(continue|proceed)", re.IGNORECASE),
    re.compile(r"Want me to\s+(continue|proceed)", re.IGNORECASE),
    re.compile(r"Continue\?", re.IGNORECASE),
    re.compile(r"Press Enter to continue", re.IGNORECASE),
    re.compile(r"Has this been completed", re.IGNORECASE),
    re.compile(r"\(y/n\)", re.IGNORECASE),
    re.compile(r"\(Y/n\)"),
    re.compile(r"\(yes/no\)", re.IGNORECASE),
    re.compile(r"Would you like me to", re.IGNORECASE),
    re.compile(r"Should I\s+(continue|proceed|go ahead)", re.IGNORECASE),
]

# Working indicators
WORKING_PATTERNS = [
    re.compile(r"Reading file", re.IGNORECASE),
    re.compile(r"Writing to", re.IGNORECASE),
    re.compile(r"Editing file", re.IGNORECASE),
    re.compile(r"Running:", re.IGNORECASE),
    re.compile(r"Searching", re.IGNORECASE),
]


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

    # Check for compaction
    for pattern in COMPACT_PATTERNS:
        match = pattern.search(output)
        if match:
            events.append(OutputEvent(
                event_type=EventType.COMPACT,
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


def detect_state(output: str) -> SessionState:
    """Detect the current session state from terminal output.

    Analyzes the last few lines of output to determine state.
    """
    if not output.strip():
        return SessionState.UNKNOWN

    # Check recent lines (last ~10 lines)
    recent = "\n".join(output.strip().split("\n")[-10:])

    # Check for waiting state first (highest priority — user bottleneck)
    for pattern in WAITING_PATTERNS:
        if pattern.search(recent):
            return SessionState.WAITING

    # Check for working state (most common during active sessions)
    for pattern in WORKING_PATTERNS:
        if pattern.search(recent):
            return SessionState.WORKING

    # Check for error state
    for pattern in ERROR_PATTERNS:
        if pattern.search(recent):
            return SessionState.ERROR

    # Check for idle (prompt visible)
    for pattern in IDLE_PATTERNS:
        if pattern.search(recent):
            return SessionState.IDLE

    # Default: if there's recent output, assume working
    return SessionState.WORKING
