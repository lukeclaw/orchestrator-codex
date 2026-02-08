"""Full session lifecycle: create, start Claude Code, remove."""

from __future__ import annotations

import logging
import sqlite3
import time

from orchestrator.state.models import Session
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.terminal import manager as tmux
from orchestrator.terminal import ssh

logger = logging.getLogger(__name__)


def create_session(
    conn: sqlite3.Connection,
    name: str,
    host: str,
    mp_path: str | None = None,
    tmux_session: str = "orchestrator",
) -> Session:
    """Create a new session: tmux window, SSH, cd to path, persist to DB."""
    # Create tmux window
    target = tmux.create_window(tmux_session, name)

    # If remote, SSH into host
    if host != "local":
        ssh.connect(tmux_session, name, host)
        # Wait a moment for SSH to establish
        time.sleep(2)

    # cd to working directory if specified
    if mp_path:
        tmux.send_keys(tmux_session, name, f"cd {mp_path}")
        time.sleep(0.5)

    # Persist to DB
    session = sessions_repo.create_session(
        conn, name=name, host=host, mp_path=mp_path, tmux_window=target
    )
    logger.info("Created session: %s (host=%s, path=%s)", name, host, mp_path)
    return session


def start_claude_code(
    conn: sqlite3.Connection,
    name: str,
    tmux_session: str = "orchestrator",
) -> bool:
    """Start Claude Code in a session's tmux window."""
    session = sessions_repo.get_session_by_name(conn, name)
    if session is None:
        logger.error("Session not found: %s", name)
        return False

    tmux.send_keys(tmux_session, name, "claude")
    sessions_repo.update_session(conn, session.id, status="working")
    logger.info("Started Claude Code in session: %s", name)
    return True


def remove_session(
    conn: sqlite3.Connection,
    name: str,
    tmux_session: str = "orchestrator",
    kill_window: bool = True,
) -> bool:
    """Remove a session: update DB, optionally kill tmux window."""
    session = sessions_repo.get_session_by_name(conn, name)
    if session is None:
        logger.error("Session not found: %s", name)
        return False

    if kill_window:
        tmux.kill_window(tmux_session, name)

    sessions_repo.delete_session(conn, session.id)
    logger.info("Removed session: %s", name)
    return True


def get_session_output(
    name: str,
    tmux_session: str = "orchestrator",
    lines: int = 50,
) -> str:
    """Get recent terminal output from a session."""
    return tmux.capture_output(tmux_session, name, lines=lines)


def send_to_session(
    name: str,
    message: str,
    tmux_session: str = "orchestrator",
) -> bool:
    """Send a message to a session's Claude Code instance."""
    return tmux.send_keys(tmux_session, name, message)
