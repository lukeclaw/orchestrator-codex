"""Internal event bus (pub/sub) for decoupling modules."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


@dataclass
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# Type alias for event handlers
EventHandler = Callable[[Event], None]

_handlers: dict[str, list[EventHandler]] = defaultdict(list)


def subscribe(event_type: str, handler: EventHandler):
    """Subscribe a handler to an event type."""
    _handlers[event_type].append(handler)


def unsubscribe(event_type: str, handler: EventHandler):
    """Unsubscribe a handler from an event type."""
    if handler in _handlers[event_type]:
        _handlers[event_type].remove(handler)


def publish(event: Event):
    """Publish an event to all subscribed handlers."""
    for handler in _handlers.get(event.type, []):
        handler(event)
    # Also notify wildcard subscribers
    for handler in _handlers.get("*", []):
        handler(event)


def clear():
    """Clear all subscriptions (for testing)."""
    _handlers.clear()
