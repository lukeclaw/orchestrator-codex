"""Compose re-brief message from snapshot and send to session via tmux."""

from __future__ import annotations

import json
import logging
import sqlite3

from orchestrator.llm.templates import render_template
from orchestrator.recovery.snapshot import get_latest_snapshot
from orchestrator.state.repositories import sessions
from orchestrator.terminal.session import send_to_session

logger = logging.getLogger(__name__)


def rebrief_session(
    conn: sqlite3.Connection,
    session_name: str,
    tmux_session: str = "orchestrator",
) -> bool:
    """Compose and send a re-brief message to a session that lost context."""
    session = sessions.get_session_by_name(conn, session_name)
    if session is None:
        logger.error("Session not found: %s", session_name)
        return False

    snapshot = get_latest_snapshot(conn, session.id)
    if snapshot is None:
        logger.warning("No snapshot available for session %s", session_name)
        return False

    # Parse key decisions
    try:
        key_decisions = json.loads(snapshot.key_decisions) if snapshot.key_decisions else []
        decisions_text = "\n".join(
            f"- Q: {d['question']} -> A: {d.get('response', 'pending')}"
            for d in key_decisions
        )
    except (json.JSONDecodeError, TypeError):
        decisions_text = "No recent decisions"

    # Parse file paths
    try:
        file_paths = json.loads(snapshot.file_paths) if snapshot.file_paths else []
        files_text = "\n".join(f"- {fp}" for fp in file_paths) if file_paths else "Not tracked"
    except (json.JSONDecodeError, TypeError):
        files_text = "Not tracked"

    # Render re-brief template
    variables = {
        "session_name": session_name,
        "task_summary": snapshot.task_summary or "No current task",
        "key_decisions": decisions_text,
        "file_paths": files_text,
        "last_known_state": snapshot.last_known_state or "Unknown",
    }

    message = render_template(conn, "rebrief", variables)
    if message is None:
        # Fallback message
        message = (
            f"You previously lost context. Here is your current assignment:\n"
            f"Task: {variables['task_summary']}\n"
            f"Decisions: {variables['key_decisions']}\n"
            f"Last state: {variables['last_known_state']}\n"
            f"Please continue your work."
        )

    success = send_to_session(session_name, message, tmux_session)
    if success:
        logger.info("Re-briefed session: %s", session_name)
    else:
        logger.error("Failed to re-brief session: %s", session_name)

    return success
