"""Shared utilities for the orchestrator.

This module provides common helpers to ensure consistency across the codebase.
"""

import re
from datetime import UTC, datetime


def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string with timezone.
    
    Always use this instead of datetime.now().isoformat() to avoid
    timezone bugs when comparing timestamps.
    
    Returns:
        ISO 8601 string like "2026-02-13T05:30:00+00:00"
    """
    return datetime.now(UTC).isoformat()


# URL pattern → tag mapping. Order matters: first match wins.
_TAG_RULES: list[tuple[re.Pattern, str]] = [
    # GitHub
    (re.compile(r"github\.com/.+/pull/\d+", re.I), "PR"),
    (re.compile(r"github\.com/.+/issues/\d+", re.I), "Issue"),
    (re.compile(r"github\.com/.+/actions/runs/", re.I), "CI"),
    (re.compile(r"github\.com/", re.I), "GitHub"),
    # Google Workspace
    (re.compile(r"docs\.google\.com/document/", re.I), "Doc"),
    (re.compile(r"docs\.google\.com/spreadsheets/", re.I), "Sheet"),
    (re.compile(r"docs\.google\.com/presentation/", re.I), "Slides"),
    (re.compile(r"docs\.google\.com/forms/", re.I), "Form"),
    (re.compile(r"drive\.google\.com/", re.I), "Drive"),
    # Slack
    (re.compile(r"\.slack\.com/", re.I), "Slack"),
    # Atlassian
    (re.compile(r"atlassian\.net/wiki/", re.I), "Wiki"),
    (re.compile(r"atlassian\.net/browse/", re.I), "Jira"),
    (re.compile(r"atlassian\.net/jira/", re.I), "Jira"),
    # Figma
    (re.compile(r"figma\.com/", re.I), "Figma"),
]


def derive_tag_from_url(url: str) -> str | None:
    """Derive a short tag from a URL based on known patterns.

    Returns a tag string like "PR", "Doc", "Slack", etc., or None if no
    pattern matches.
    """
    for pattern, tag in _TAG_RULES:
        if pattern.search(url):
            return tag
    return None
