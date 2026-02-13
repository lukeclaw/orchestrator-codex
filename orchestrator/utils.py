"""Shared utilities for the orchestrator.

This module provides common helpers to ensure consistency across the codebase.
"""

from datetime import datetime, timezone


def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string with timezone.
    
    Always use this instead of datetime.now().isoformat() to avoid
    timezone bugs when comparing timestamps.
    
    Returns:
        ISO 8601 string like "2026-02-13T05:30:00+00:00"
    """
    return datetime.now(timezone.utc).isoformat()
