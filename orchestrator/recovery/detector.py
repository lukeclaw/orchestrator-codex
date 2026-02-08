"""Detect /compact, restart, crash via passive monitor signals."""

from __future__ import annotations

import logging

from orchestrator.core.events import Event
from orchestrator.terminal.output_parser import EventType

logger = logging.getLogger(__name__)


def needs_recovery(event: Event) -> bool:
    """Check if an event indicates a session needs recovery."""
    if event.type == f"session.{EventType.COMPACT.value}":
        return True
    if event.type == "session.state_changed":
        old = event.data.get("old_state")
        new = event.data.get("new_state")
        # Disconnected -> any state = possible restart
        if old == "disconnected" and new in ("idle", "working"):
            return True
    return False


def get_recovery_reason(event: Event) -> str:
    """Get a human-readable reason for the recovery."""
    if event.type == f"session.{EventType.COMPACT.value}":
        return "Context compacted (/compact)"
    if event.type == "session.state_changed":
        return f"State changed: {event.data.get('old_state')} -> {event.data.get('new_state')}"
    return "Unknown recovery trigger"
