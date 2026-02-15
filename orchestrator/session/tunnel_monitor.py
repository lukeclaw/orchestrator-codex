"""Periodic tunnel health monitor.

Background async loop that checks tunnel health for all active rdev workers
and auto-reconnects dead tunnels. This complements the on-demand health checks
in the API by providing proactive, continuous monitoring.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from orchestrator.session.health import check_tunnel_alive, probe_tunnel_connectivity
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.terminal.ssh import is_rdev_host

logger = logging.getLogger(__name__)

# Default interval between tunnel health checks (seconds)
DEFAULT_CHECK_INTERVAL = 60.0


async def tunnel_health_loop(
    conn: sqlite3.Connection,
    tmux_session: str = "orchestrator",
    check_interval: float = DEFAULT_CHECK_INTERVAL,
    api_port: int = 8093,
):
    """Periodically check tunnel health for all active rdev workers.

    For each rdev worker with a tunnel_pane:
    1. Run check_tunnel_alive (tmux output heuristics + active probe)
    2. If dead, auto-reconnect the tunnel

    Args:
        conn: Database connection (read-only for listing sessions)
        tmux_session: tmux session name
        check_interval: Seconds between check cycles
        api_port: API port for reverse tunnel
    """
    logger.info("Tunnel health monitor started (interval=%.0fs)", check_interval)

    while True:
        try:
            await asyncio.sleep(check_interval)
            await _check_all_tunnels(conn, tmux_session, api_port)
        except asyncio.CancelledError:
            logger.info("Tunnel health monitor stopped.")
            break
        except Exception:
            logger.exception("Tunnel health monitor error")
            await asyncio.sleep(check_interval)


async def _check_all_tunnels(
    conn: sqlite3.Connection,
    tmux_session: str,
    api_port: int,
):
    """Run one cycle of tunnel checks across all active rdev workers."""
    sessions = sessions_repo.list_sessions(conn, session_type="worker")

    checked = 0
    reconnected = 0

    for s in sessions:
        # Skip non-rdev, disconnected, or connecting workers
        if not is_rdev_host(s.host):
            continue
        if s.status in ("disconnected", "connecting"):
            continue
        if not s.tunnel_pane:
            continue

        checked += 1

        # Parse tunnel pane
        if ":" in s.tunnel_pane:
            t_sess, t_win = s.tunnel_pane.split(":", 1)
        else:
            t_sess, t_win = tmux_session, s.tunnel_pane

        # Check tunnel health with active probe
        alive = check_tunnel_alive(t_sess, t_win, host=s.host, remote_port=api_port)

        if alive:
            continue

        # Tunnel is dead — attempt auto-reconnect
        logger.warning(
            "Tunnel monitor: %s tunnel dead, attempting auto-reconnect", s.name
        )
        try:
            reconnected_ok = await _reconnect_tunnel(conn, s, tmux_session, api_port)
            if reconnected_ok:
                reconnected += 1
                logger.info("Tunnel monitor: %s tunnel auto-reconnected", s.name)
            else:
                logger.warning("Tunnel monitor: %s tunnel auto-reconnect failed", s.name)
        except Exception:
            logger.exception("Tunnel monitor: %s tunnel reconnect error", s.name)

    if checked > 0:
        logger.debug(
            "Tunnel monitor: checked %d tunnels, reconnected %d", checked, reconnected
        )


async def _reconnect_tunnel(
    conn: sqlite3.Connection,
    session,
    tmux_session: str,
    api_port: int,
) -> bool:
    """Auto-reconnect a dead tunnel in a background thread.

    Uses reconnect_tunnel_only which is synchronous (tmux operations),
    so we run it in an executor to avoid blocking the async loop.
    """
    from orchestrator.session.reconnect import reconnect_tunnel_only
    from orchestrator.state.repositories import sessions as repo

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        reconnect_tunnel_only,
        conn, session, tmux_session, api_port, repo,
    )
