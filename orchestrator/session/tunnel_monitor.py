"""Periodic tunnel health monitor.

Background async loop that checks tunnel health for all active rdev workers
and auto-restarts dead tunnels via the ReverseTunnelManager.

Uses deterministic process-level checks (proc.poll()) instead of
tmux output string parsing.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.terminal.ssh import is_rdev_host

logger = logging.getLogger(__name__)

# Default interval between tunnel health checks (seconds)
DEFAULT_CHECK_INTERVAL = 60.0


async def tunnel_health_loop(
    conn: sqlite3.Connection,
    tunnel_manager=None,
    check_interval: float = DEFAULT_CHECK_INTERVAL,
):
    """Periodically check tunnel health for all active rdev workers.

    For each rdev worker:
    1. Check if tunnel process is alive via tunnel_manager.is_alive()
    2. If dead, restart the tunnel and update the DB with the new PID

    Args:
        conn: Database connection (read-only for listing sessions)
        tunnel_manager: ReverseTunnelManager instance
        check_interval: Seconds between check cycles
    """
    if tunnel_manager is None:
        logger.warning("Tunnel health monitor: no tunnel_manager provided, skipping")
        return

    logger.info("Tunnel health monitor started (interval=%.0fs)", check_interval)

    while True:
        try:
            await asyncio.sleep(check_interval)
            await _check_all_tunnels(conn, tunnel_manager)
        except asyncio.CancelledError:
            logger.info("Tunnel health monitor stopped.")
            break
        except Exception:
            logger.exception("Tunnel health monitor error")
            await asyncio.sleep(check_interval)


async def _check_all_tunnels(
    conn: sqlite3.Connection,
    tunnel_manager,
):
    """Run one cycle of tunnel checks across all active rdev workers."""
    sessions = sessions_repo.list_sessions(conn, session_type="worker")

    checked = 0
    restarted = 0

    for s in sessions:
        # Skip non-rdev, disconnected, or connecting workers
        if not is_rdev_host(s.host):
            continue
        if s.status in ("disconnected", "connecting"):
            continue

        checked += 1

        # Deterministic check: is the tunnel process alive?
        alive = tunnel_manager.is_alive(s.id)

        if alive:
            continue

        # Tunnel is dead — restart it
        logger.warning(
            "Tunnel monitor: %s tunnel dead, restarting", s.name
        )
        try:
            new_pid = await asyncio.get_event_loop().run_in_executor(
                None,
                tunnel_manager.restart_tunnel,
                s.id, s.name, s.host,
            )
            if new_pid:
                restarted += 1
                sessions_repo.update_session(conn, s.id, tunnel_pid=new_pid)
                logger.info("Tunnel monitor: %s tunnel restarted (pid=%d)", s.name, new_pid)
            else:
                logger.warning("Tunnel monitor: %s tunnel restart failed", s.name)
        except Exception:
            logger.exception("Tunnel monitor: %s tunnel restart error", s.name)

    if checked > 0:
        logger.debug(
            "Tunnel monitor: checked %d tunnels, restarted %d", checked, restarted
        )
