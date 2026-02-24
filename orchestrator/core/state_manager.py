"""Centralized state manager for all database writes triggered by events.

This module is the SINGLE POINT of database writes for event-driven operations.
It subscribes to events from the monitor and other components, then batches
and executes writes in a controlled manner to avoid lock contention.

Architecture:
    Monitor (read-only) → Events → StateManager (writes) → DB
    API Routes → DB (via deps.py connection)

The StateManager uses its own connection and handles retries internally.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.core.events import Event, subscribe
from orchestrator.state.db import ConnectionFactory

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class StateManager:
    """Manages all event-driven database state updates.
    
    This centralizes writes to avoid lock contention between the monitor
    and API routes. The monitor emits events, and this manager handles
    all the corresponding DB updates.
    """

    def __init__(self, db_path: str | Path):
        self.conn_factory = ConnectionFactory(db_path)
        self._event_queue: asyncio.Queue[Event] = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task | None = None

        # Subscribe to events that require DB writes
        subscribe("session.state_changed", self._queue_event)

    def _queue_event(self, event: Event):
        """Queue an event for processing (called from sync context)."""
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full, dropping event: %s", event.type)

    async def start(self):
        """Start the state manager processing loop."""
        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        logger.info("StateManager started")

    async def stop(self):
        """Stop the state manager."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("StateManager stopped")

    async def _process_loop(self):
        """Main processing loop — handles queued events."""
        while self._running:
            try:
                # Wait for an event with timeout to allow clean shutdown
                try:
                    event = await asyncio.wait_for(
                        self._event_queue.get(), timeout=1.0
                    )
                except TimeoutError:
                    continue

                await self._handle_event(event)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in state manager loop")

    async def _handle_event(self, event: Event):
        """Handle a single event by updating the database."""
        try:
            if event.type == "session.state_changed":
                await self._handle_state_change(event)
        except Exception:
            logger.exception("Failed to handle event: %s", event.type)

    async def _handle_state_change(self, event: Event):
        """Update session status in DB when state changes."""
        session_name = event.data.get("session")
        new_state = event.data.get("new_state")

        if not session_name or not new_state:
            return

        # Run in thread pool to avoid blocking async loop
        await asyncio.to_thread(
            self._update_session_state, session_name, new_state
        )

    def _update_session_state(self, session_name: str, new_state: str):
        """Update session state in database (runs in thread pool).
        
        Note: No @with_retry here since update_session already has retry logic.
        """
        with self.conn_factory.connection() as conn:
            from orchestrator.state.repositories import sessions as sessions_repo

            session = sessions_repo.get_session_by_name(conn, session_name)
            if session:
                # last_status_changed_at is auto-updated by update_session when status changes
                sessions_repo.update_session(
                    conn,
                    session.id,
                    status=new_state,
                )
                logger.debug("Updated session %s state to %s", session_name, new_state)
