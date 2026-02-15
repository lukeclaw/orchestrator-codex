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


def recover_tunnels(conn: sqlite3.Connection, tunnel_manager):
    """Recover reverse tunnels after orchestrator restart.

    For each rdev session with a stored tunnel_pid, try to adopt the
    existing SSH process. If the process is dead, start a fresh tunnel.
    Also cleans up any legacy tmux tunnel windows.
    """
    from orchestrator.terminal.ssh import is_rdev_host

    all_sessions = sessions.list_sessions(conn, session_type="worker")
    recovered = 0

    for s in all_sessions:
        if not is_rdev_host(s.host):
            continue
        if s.status in ("disconnected",):
            continue

        # Clean up legacy tmux tunnel windows (from old tmux-based approach)
        if s.tunnel_pane:
            try:
                if ":" in s.tunnel_pane:
                    t_sess, t_win = s.tunnel_pane.split(":", 1)
                else:
                    t_sess, t_win = "orchestrator", s.tunnel_pane
                tmux.kill_window(t_sess, t_win)
                logger.info("Cleaned up legacy tmux tunnel window %s", s.tunnel_pane)
            except Exception:
                pass
            # Clear the old tunnel_pane field
            sessions.update_session(conn, s.id, tunnel_pane=None)

        # Recover or start tunnel
        pid = tunnel_manager.recover_tunnel(
            session_id=s.id,
            session_name=s.name,
            host=s.host,
            stored_pid=s.tunnel_pid,
        )

        if pid:
            sessions.update_session(conn, s.id, tunnel_pid=pid)
            recovered += 1
        else:
            logger.warning("Failed to recover tunnel for %s", s.name)

    if recovered:
        logger.info("Recovered %d tunnels on startup", recovered)


def shutdown(conn: sqlite3.Connection):
    """Clean shutdown."""
    logger.info("Shutting down orchestrator")
