"""Startup and shutdown procedures."""

from __future__ import annotations

import logging
import os
import sqlite3

from orchestrator.state.db import with_retry
from orchestrator.state.repositories import sessions
from orchestrator.terminal import manager as tmux

logger = logging.getLogger(__name__)


@with_retry
def startup_check(conn: sqlite3.Connection, tmux_session: str = "orchestrator"):
    """Run startup checks: verify tmux session, reconcile DB with tmux state."""
    # Skip reconciliation if tmux is not available or in test mode
    if not tmux.is_tmux_available() or os.environ.get("ORCHESTRATOR_SKIP_RECONCILE"):
        logger.info("Skipping startup reconciliation")
        return

    # Ensure tmux session exists
    if not tmux.session_exists(tmux_session):
        tmux.create_session(tmux_session)
        logger.info("Created tmux session: %s", tmux_session)

    # Reconcile: check if DB sessions still have tmux windows
    db_sessions = sessions.list_sessions(conn)
    tmux_windows = tmux.list_windows(tmux_session)
    window_names = {w.name for w in tmux_windows}

    for s in db_sessions:
        if s.name not in window_names and s.status != "disconnected":
            logger.warning("Session %s has no tmux window, marking disconnected", s.name)
            sessions.update_session(conn, s.id, status="disconnected")


def shutdown(conn: sqlite3.Connection):
    """Clean shutdown."""
    logger.info("Shutting down orchestrator")
