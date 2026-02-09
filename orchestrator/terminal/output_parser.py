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
    COMPACT = "compact"


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

# Context compaction (Claude Code specific)
COMPACT_PATTERNS = [
    re.compile(r"/compact", re.IGNORECASE),
    re.compile(r"Context.*compacted", re.IGNORECASE),
    re.compile(r"conversation.*summarized", re.IGNORECASE),
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
