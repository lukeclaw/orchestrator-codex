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

    # Reconcile: check if DB sessions still have tmux windows.
    # Skip remote sessions — they use RWS PTY (no tmux window).
    from orchestrator.terminal.ssh import is_remote_host

    db_sessions = sessions.list_sessions(conn)
    tmux_windows = tmux.list_windows(tmux_session)
    window_names = {w.name for w in tmux_windows}

    for s in db_sessions:
        if is_remote_host(s.host):
            continue  # Remote workers use RWS PTY, not tmux windows
        if s.name not in window_names and s.status != "disconnected":
            logger.warning("Session %s has no tmux window, marking disconnected", s.name)
            sessions.update_session(conn, s.id, status="disconnected")


def recover_tunnels(conn: sqlite3.Connection, tunnel_manager):
    """Recover reverse tunnels after orchestrator restart.

    For each rdev session with a stored tunnel_pid, try to adopt the
    existing SSH process. If the process is dead, start a fresh tunnel.

    Important: we recover tunnels for ALL remote workers, including
    disconnected ones.  Tunnels use ``start_new_session=True`` and survive
    restarts, so a "disconnected" worker may still have a live SSH process
    holding port 8093 on the remote.  If we skip it, the orphaned process
    blocks every subsequent tunnel attempt for that host.
    """
    from orchestrator.terminal.ssh import is_remote_host

    all_sessions = sessions.list_sessions(conn, session_type="worker")
    recovered = 0

    for s in all_sessions:
        if not is_remote_host(s.host):
            continue

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


def migrate_legacy_screen_sessions(conn: sqlite3.Connection):
    """Kill screen sessions for legacy remote workers, triggering RWS reconnect."""
    import subprocess

    from orchestrator.terminal.ssh import is_remote_host

    all_sessions = sessions.list_sessions(conn, session_type="worker")
    legacy = [
        s
        for s in all_sessions
        if is_remote_host(s.host) and not s.rws_pty_id and s.status not in ("idle", "disconnected")
    ]
    if not legacy:
        return

    logger.info("Migrating %d legacy screen-based remote sessions", len(legacy))
    for s in legacy:
        screen_name = f"claude-{s.id}"
        try:
            kill_cmd = (
                f"screen -ls | grep -w '{screen_name}' | awk '{{print $1}}' | "
                f'while read sid; do screen -X -S "$sid" quit; done'
            )
            subprocess.run(
                [
                    "ssh",
                    "-o",
                    "ConnectTimeout=5",
                    "-o",
                    "BatchMode=yes",
                    s.host,
                    kill_cmd,
                ],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass  # Best-effort
        sessions.update_session(conn, s.id, status="disconnected")
        logger.info("Migrated %s: killed screen, set disconnected", s.name)


def shutdown(conn: sqlite3.Connection):
    """Clean shutdown."""
    logger.info("Shutting down orchestrator")
