"""Background task that converts heartbeat timestamps into DB intervals."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from orchestrator.state.db import ConnectionFactory
from orchestrator.state.repositories import human_activity

logger = logging.getLogger(__name__)

IDLE_TIMEOUT = 300  # 5 minutes


class HumanActivityTracker:
    def __init__(self, conn_factory: ConnectionFactory):
        self._last_heartbeat: float = 0
        self._conn_factory = conn_factory
        self._task: asyncio.Task | None = None

    def record_heartbeat(self) -> None:
        """Called from WebSocket handler or terminal input. In-memory only."""
        self._last_heartbeat = time.time()

    async def start(self) -> None:
        """Start the background polling loop."""
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop the loop and close any open interval."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Graceful shutdown: close open interval with last heartbeat time
        if self._last_heartbeat > 0:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._close_open_interval)

    def _close_open_interval(self) -> None:
        """Sync helper: close any open interval in DB."""
        with self._conn_factory.connection() as conn:
            open_iv = human_activity.get_open_interval(conn)
            if open_iv:
                end_time = datetime.fromtimestamp(self._last_heartbeat, tz=UTC).isoformat()
                human_activity.close_interval(conn, open_iv["id"], end_time)
                conn.commit()

    async def _run(self) -> None:
        """Background loop, checks every 30s."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                await asyncio.sleep(30)
                await loop.run_in_executor(None, self._tick)
            except asyncio.CancelledError:
                logger.info("Human activity tracker stopped")
                break
            except Exception:
                logger.exception("Human activity tracker error (non-fatal)")

    def _tick(self) -> None:
        """One poll cycle. Runs in executor to avoid blocking event loop."""
        now = time.time()
        is_active = self._last_heartbeat > 0 and (now - self._last_heartbeat) < IDLE_TIMEOUT

        with self._conn_factory.connection() as conn:
            open_interval = human_activity.get_open_interval(conn)

            if is_active and open_interval is None:
                human_activity.start_interval(conn)
                conn.commit()
            elif not is_active and open_interval is not None:
                end_time = datetime.fromtimestamp(self._last_heartbeat, tz=UTC).isoformat()
                human_activity.close_interval(conn, open_interval["id"], end_time)
                conn.commit()
