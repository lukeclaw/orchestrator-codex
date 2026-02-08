"""Main orchestration event loop and coordination."""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from orchestrator.core.events import Event, subscribe
from orchestrator.recovery.detector import needs_recovery, get_recovery_reason
from orchestrator.recovery.rebrief import rebrief_session
from orchestrator.recovery.snapshot import create_snapshot
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.state.repositories import activities as activities_repo
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
    ):
        self.conn = conn
        self.config = config
        self.tmux_session = config.get("tmux", {}).get("session_name", "orchestrator")
        self._monitor_task: asyncio.Task | None = None

        # Subscribe to events
        subscribe("*", self._handle_event)

    def _handle_event(self, event: Event):
        """Central event handler."""
        logger.debug("Event: %s %s", event.type, event.data)

        session_name = event.data.get("session")
        if not session_name:
            return

        # Recovery: compact or restart detected
        if needs_recovery(event):
            reason = get_recovery_reason(event)
            logger.warning("Recovery needed for %s: %s", session_name, reason)
            self._execute_recovery(session_name, reason)

        # Auto-approve: session is waiting for input
        if event.type == "session.state_changed":
            new_state = event.data.get("new_state")
            if new_state == "waiting":
                self._handle_waiting(session_name)
            elif new_state == "idle":
                self._handle_idle(session_name)

    def _execute_recovery(self, session_name: str, reason: str):
        """Execute recovery: snapshot + re-brief."""
        try:
            session = sessions_repo.get_session_by_name(self.conn, session_name)
            if not session:
                return

            # Snapshot current state before re-brief
            create_snapshot(self.conn, session.id)

            # Send re-brief
            success = rebrief_session(self.conn, session_name, self.tmux_session)
            if success:
                activities_repo.create_activity(
                    self.conn,
                    event_type="recovery.rebrief",
                    session_id=session.id,
                    event_data={"reason": reason},
                )
                logger.info("Recovery re-brief sent to %s", session_name)
        except Exception:
            logger.exception("Recovery failed for %s", session_name)

    def _handle_waiting(self, session_name: str):
        """Handle a session that's waiting for input — check auto-approve rules."""
        try:
            from orchestrator.automation.auto_approve import check_auto_approve

            output = tmux.capture_output(self.tmux_session, session_name, lines=30)
            response = check_auto_approve(self.conn, session_name, output)

            session = sessions_repo.get_session_by_name(self.conn, session_name)
            if not session:
                return

            if response is not None:
                # Auto-approve: send the response keystroke
                tmux.send_keys(self.tmux_session, session_name, response, enter=False)
                activities_repo.create_activity(
                    self.conn,
                    event_type="auto_approve.sent",
                    session_id=session.id,
                    event_data={"response": repr(response), "output_tail": output[-200:]},
                )
                logger.info("Auto-approved for %s: sent %r", session_name, response)
            else:
                # Create a decision for human review
                from orchestrator.state.repositories import decisions as decisions_repo
                decisions_repo.create_decision(
                    self.conn,
                    question=f"Session '{session_name}' is waiting for input",
                    session_id=session.id,
                    context=output[-500:] if output else "No terminal output captured",
                    urgency="normal",
                )
                logger.info("Created decision for waiting session %s", session_name)
        except ImportError:
            logger.debug("auto_approve module not available")
        except Exception:
            logger.exception("Error handling waiting state for %s", session_name)

    def _handle_idle(self, session_name: str):
        """Handle a session that went idle — check for next task assignment."""
        try:
            from orchestrator.scheduler.scheduler import get_next_assignments
            from orchestrator.terminal.session import send_to_session

            assignments = get_next_assignments(self.conn)
            session = sessions_repo.get_session_by_name(self.conn, session_name)
            if not session:
                return

            for task_id, assigned_session_id in assignments:
                if assigned_session_id != session.id:
                    continue

                task = tasks_repo.get_task(self.conn, task_id)
                if not task:
                    continue

                # Assign the task
                tasks_repo.update_task(
                    self.conn, task_id, assigned_session_id=session.id, status="in_progress"
                )
                sessions_repo.update_session(
                    self.conn, session.id, current_task_id=task_id, status="working"
                )

                # Compose and send context to the worker
                message = f"New task assigned: {task.title}\n\n{task.description or ''}"
                send_to_session(session_name, message, self.tmux_session)

                activities_repo.create_activity(
                    self.conn,
                    event_type="task.assigned",
                    session_id=session.id,
                    task_id=task_id,
                    event_data={"task_title": task.title},
                )
                logger.info("Assigned task '%s' to %s", task.title, session_name)
                break  # One task per idle event
        except Exception:
            logger.exception("Error handling idle state for %s", session_name)

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
