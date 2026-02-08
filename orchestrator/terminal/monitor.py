"""Background async task for passive monitoring of terminal output.

This module is READ-ONLY — it only reads terminal state and emits events.
All database writes happen in the StateManager (core/state_manager.py).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from orchestrator.core.events import Event, publish
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.terminal import manager as tmux
from orchestrator.terminal.output_parser import (
    EventType,
    SessionState,
    detect_state,
    parse_output,
)

logger = logging.getLogger(__name__)

# Track previous output per session to detect changes
_previous_output: dict[str, str] = {}
_previous_state: dict[str, str] = {}


async def poll_session(
    conn: sqlite3.Connection,
    session_name: str,
    tmux_session: str = "orchestrator",
) -> list[Event]:
    """Poll a single session and return any events detected.
    
    This function is READ-ONLY. It detects state changes and emits events,
    but does NOT write to the database. The StateManager handles all writes.
    """
    output = tmux.capture_output(tmux_session, session_name, lines=50)

    # Skip if output hasn't changed
    prev = _previous_output.get(session_name, "")
    if output == prev:
        return []
    _previous_output[session_name] = output

    events = []

    # Detect state change
    new_state = detect_state(output)
    old_state = _previous_state.get(session_name)

    if old_state != new_state.value:
        _previous_state[session_name] = new_state.value

        # Emit state change event — StateManager will handle DB update
        events.append(Event(
            type="session.state_changed",
            data={
                "session": session_name,
                "old_state": old_state,
                "new_state": new_state.value,
            },
        ))

    # Parse for specific events
    parsed = parse_output(output)
    for pe in parsed:
        event = Event(
            type=f"session.{pe.event_type.value}",
            data={"session": session_name, **pe.data},
        )
        events.append(event)

    return events


async def monitor_loop(
    conn: sqlite3.Connection,
    tmux_session: str = "orchestrator",
    poll_interval: float = 5.0,
    active_interval: float = 2.0,
):
    """Main monitoring loop. Polls all sessions at the configured interval.
    
    This loop is READ-ONLY. It reads session list and terminal output,
    then publishes events. The StateManager subscribes to these events
    and handles all database writes.
    """
    logger.info("Passive monitor started (interval=%.1fs)", poll_interval)

    while True:
        try:
            sessions = sessions_repo.list_sessions(conn)

            for session in sessions:
                if session.status == "disconnected":
                    continue

                try:
                    events = await poll_session(conn, session.name, tmux_session)
                    for event in events:
                        publish(event)
                except Exception:
                    logger.exception("Error polling session %s", session.name)

            # Use shorter interval if any session is actively working
            has_active = any(s.status == "working" for s in sessions)
            interval = active_interval if has_active else poll_interval
            await asyncio.sleep(interval)

        except asyncio.CancelledError:
            logger.info("Passive monitor stopped.")
            break
        except Exception:
            logger.exception("Monitor loop error")
            await asyncio.sleep(poll_interval)


def clear_state():
    """Clear cached state (for testing)."""
    _previous_output.clear()
    _previous_state.clear()
