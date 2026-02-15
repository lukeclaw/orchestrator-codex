"""Main orchestration event loop and coordination."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from orchestrator.core.events import Event, subscribe
from orchestrator.state.db import ConnectionFactory
from orchestrator.terminal.monitor import monitor_loop
from orchestrator.session.tunnel_monitor import tunnel_health_loop

logger = logging.getLogger(__name__)


class Orchestrator:
    """Main orchestration engine that ties everything together."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        config: dict,
        db_path: str | Path | None = None,
        tunnel_manager=None,
    ):
        self.conn = conn  # For read operations (monitor loop)
        self.config = config
        self.tmux_session = config.get("tmux", {}).get("session_name", "orchestrator")
        self.tunnel_manager = tunnel_manager
        self._monitor_task: asyncio.Task | None = None
        self._tunnel_monitor_task: asyncio.Task | None = None

        # Connection factory for write operations (avoids lock contention)
        self._conn_factory: ConnectionFactory | None = None
        if db_path:
            self._conn_factory = ConnectionFactory(db_path)

        # Subscribe to events
        subscribe("*", self._handle_event)

    def _handle_event(self, event: Event):
        """Central event handler."""
        logger.debug("Event: %s %s", event.type, event.data)

    async def start(self):
        """Start the orchestrator (monitoring loop)."""
        monitoring = self.config.get("monitoring", {})
        poll_interval = monitoring.get("poll_interval_seconds", 5)

        self._monitor_task = asyncio.create_task(
            monitor_loop(
                self.conn,
                tmux_session=self.tmux_session,
                poll_interval=poll_interval,
            )
        )

        # Start periodic tunnel health monitor
        tunnel_interval = monitoring.get("tunnel_check_interval_seconds", 60)
        self._tunnel_monitor_task = asyncio.create_task(
            tunnel_health_loop(
                self.conn,
                tunnel_manager=self.tunnel_manager,
                check_interval=tunnel_interval,
            )
        )

        logger.info(
            "Orchestrator started (monitor interval=%.1fs, tunnel check interval=%.0fs)",
            poll_interval, tunnel_interval,
        )

    async def stop(self):
        """Stop the orchestrator."""
        if self._tunnel_monitor_task:
            self._tunnel_monitor_task.cancel()
            try:
                await self._tunnel_monitor_task
            except asyncio.CancelledError:
                pass
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("Orchestrator stopped")
