"""Main orchestration event loop and coordination."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from orchestrator.core.events import Event, subscribe
from orchestrator.state.db import ConnectionFactory
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.state.repositories import tasks as tasks_repo
from orchestrator.terminal import manager as tmux
from orchestrator.terminal.monitor import monitor_loop

logger = logging.getLogger(__name__)


class Orchestrator:
    """Main orchestration engine that ties everything together."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        config: dict,
        db_path: str | Path | None = None,
    ):
        self.conn = conn  # For read operations (monitor loop)
        self.config = config
        self.tmux_session = config.get("tmux", {}).get("session_name", "orchestrator")
        self._monitor_task: asyncio.Task | None = None
        
        # Connection factory for write operations (avoids lock contention)
        self._conn_factory: ConnectionFactory | None = None
        if db_path:
            self._conn_factory = ConnectionFactory(db_path)

        # Subscribe to events
        subscribe("*", self._handle_event)

    def _get_write_conn(self) -> sqlite3.Connection:
        """Get a connection for write operations.
        
        Uses connection factory if available (reduces lock contention),
        otherwise falls back to shared connection.
        """
        if self._conn_factory:
            return self._conn_factory.create()
        return self.conn

    def _handle_event(self, event: Event):
        """Central event handler."""
        logger.debug("Event: %s %s", event.type, event.data)

        session_name = event.data.get("session")
        if not session_name:
            return

        # Auto-approve: session is waiting for input
        if event.type == "session.state_changed":
            new_state = event.data.get("new_state")
            if new_state == "waiting":
                self._handle_waiting(session_name)
            elif new_state == "idle":
                self._handle_idle(session_name)

    def _handle_waiting(self, session_name: str):
        """Handle a session that's waiting for input — check auto-approve rules."""
        conn = self._get_write_conn()
        try:
            from orchestrator.automation.auto_approve import check_auto_approve

            session = sessions_repo.get_session_by_name(conn, session_name)
            if not session:
                return

            # Skip paused workers - they should not receive automatic responses
            if session.status == "paused":
                logger.debug("Skipping paused worker %s for auto-approve", session_name)
                return

            output = tmux.capture_output(self.tmux_session, session_name, lines=30)
            response = check_auto_approve(conn, session_name, output)

            if response is not None:
                # Auto-approve: send the response keystroke
                tmux.send_keys(self.tmux_session, session_name, response, enter=False)
                logger.info("Auto-approved for %s: sent %r", session_name, response)
            else:
                logger.info("Session %s waiting for input - manual intervention needed", session_name)
        except ImportError:
            logger.debug("auto_approve module not available")
        except Exception:
            logger.exception("Error handling waiting state for %s", session_name)
        finally:
            if self._conn_factory and conn is not self.conn:
                conn.close()

    def _handle_idle(self, session_name: str):
        """Handle a session that went idle — check for next task assignment."""
        conn = self._get_write_conn()
        try:
            from orchestrator.scheduler.scheduler import get_next_assignments
            from orchestrator.terminal.session import send_to_session

            session = sessions_repo.get_session_by_name(conn, session_name)
            if not session:
                return

            # Skip paused workers - they should not receive automatic task assignments
            if session.status == "paused":
                logger.debug("Skipping paused worker %s for task assignment", session_name)
                return

            assignments = get_next_assignments(conn)

            for task_id, assigned_session_id in assignments:
                if assigned_session_id != session.id:
                    continue

                task = tasks_repo.get_task(conn, task_id)
                if not task:
                    continue

                # Assign the task
                tasks_repo.update_task(
                    conn, task_id, assigned_session_id=session.id, status="in_progress"
                )
                sessions_repo.update_session(
                    conn, session.id, status="working"
                )

                # Compose and send context to the worker
                message = f"New task assigned: {task.title}\n\n{task.description or ''}"
                send_to_session(session_name, message, self.tmux_session)
                logger.info("Assigned task '%s' to %s", task.title, session_name)
                break  # One task per idle event
        except Exception:
            logger.exception("Error handling idle state for %s", session_name)
        finally:
            if self._conn_factory and conn is not self.conn:
                conn.close()

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
        logger.info("Orchestrator started (monitor interval=%.1fs)", poll_interval)

    async def stop(self):
        """Stop the orchestrator."""
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("Orchestrator stopped")
