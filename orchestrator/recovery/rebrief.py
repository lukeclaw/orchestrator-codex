"""Compose re-brief message from snapshot and send to session via tmux."""

from __future__ import annotations

import json
import logging
import sqlite3

from string import Template

from orchestrator.recovery.snapshot import get_latest_snapshot
from orchestrator.state.repositories import sessions, templates as templates_repo
from orchestrator.terminal.session import send_to_session


def _render_template(conn, template_name: str, variables: dict) -> str | None:
    """Load a prompt template from DB and render it with variables."""
    tpl = templates_repo.get_prompt_template(conn, template_name)
    if tpl is None:
        return None
    return Template(tpl.template).safe_substitute(variables)

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
        "file_paths": files_text,
        "last_known_state": snapshot.last_known_state or "Unknown",
    }

    message = _render_template(conn, "rebrief", variables)
    if message is None:
        # Fallback message
        message = (
            f"You previously lost context. Here is your current assignment:\n"
            f"Task: {variables['task_summary']}\n"
            f"Last state: {variables['last_known_state']}\n"
            f"Please continue your work."
        )

    success = send_to_session(session_name, message, tmux_session)
    if success:
        logger.info("Re-briefed session: %s", session_name)
    else:
        logger.error("Failed to re-brief session: %s", session_name)

    return success
