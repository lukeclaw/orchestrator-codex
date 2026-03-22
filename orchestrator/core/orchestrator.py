"""Main orchestration event loop and coordination."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from orchestrator.core.events import Event, subscribe
from orchestrator.session.tunnel_monitor import tunnel_health_loop
from orchestrator.state.db import ConnectionFactory

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
        self.conn = conn
        self.config = config
        self.tmux_session = config.get("tmux", {}).get("session_name", "orchestrator")
        self.tunnel_manager = tunnel_manager
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
        """Start the orchestrator background tasks."""
        monitoring = self.config.get("monitoring", {})

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
            "Orchestrator started (tunnel check interval=%.0fs)",
            tunnel_interval,
        )

    async def stop(self):
        """Stop the orchestrator."""
        if self._tunnel_monitor_task:
            self._tunnel_monitor_task.cancel()
            try:
                await self._tunnel_monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("Orchestrator stopped")

    async def replace_connection(self, new_conn: sqlite3.Connection):
        """Swap the database connection used by background tasks.

        Stops the tunnel-health loop, replaces ``self.conn``,
        then restarts with the new connection.  This is needed
        when the database file is replaced (e.g. restore from backup) so
        that background tasks don't hold stale file-descriptors.
        """
        await self.stop()
        self.conn = new_conn
        await self.start()
        logger.info("Orchestrator connection replaced and tasks restarted")
