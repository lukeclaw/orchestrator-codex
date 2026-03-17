"""Periodic tunnel health monitor.

Background async loop that checks tunnel health for all active rdev workers
and auto-restarts dead tunnels via the ReverseTunnelManager.

Two-tier health checking:
  - Every cycle (~60s): fast process-level check via proc.poll()
  - Every Nth cycle (~5 min): active connectivity probe that SSHes to
    the remote and curls the tunneled API endpoint.  This catches "zombie"
    tunnels where the SSH process is alive but the port forward is broken.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.terminal.ssh import is_remote_host

logger = logging.getLogger(__name__)

# Default interval between tunnel health checks (seconds)
DEFAULT_CHECK_INTERVAL = 60.0

# How often (in cycles) to run the full connectivity probe.
# With a 60s check interval this means every 5 minutes.
DEFAULT_DEEP_PROBE_EVERY_N_CYCLES = 5


async def tunnel_health_loop(
    conn: sqlite3.Connection,
    tunnel_manager=None,
    check_interval: float = DEFAULT_CHECK_INTERVAL,
    deep_probe_every: int = DEFAULT_DEEP_PROBE_EVERY_N_CYCLES,
):
    """Periodically check tunnel health for all active rdev workers.

    For each rdev worker:
    1. Every cycle: fast process-level check via is_alive()
    2. Every ``deep_probe_every`` cycles: active connectivity probe via
       check_connectivity() — catches zombie tunnels.
    3. Dead or unreachable tunnels are restarted automatically.

    Args:
        conn: Database connection
        tunnel_manager: ReverseTunnelManager instance
        check_interval: Seconds between check cycles
        deep_probe_every: Run the full connectivity probe every N cycles
    """
    if tunnel_manager is None:
        logger.warning("Tunnel health monitor: no tunnel_manager provided, skipping")
        return

    logger.info(
        "Tunnel health monitor started (interval=%.0fs, deep probe every %d cycles)",
        check_interval,
        deep_probe_every,
    )

    cycle = 0
    while True:
        try:
            await asyncio.sleep(check_interval)
            cycle += 1
            do_deep_probe = cycle % deep_probe_every == 0
            await _check_all_tunnels(conn, tunnel_manager, deep_probe=do_deep_probe)
        except asyncio.CancelledError:
            logger.info("Tunnel health monitor stopped.")
            break
        except Exception:
            logger.exception("Tunnel health monitor error")
            await asyncio.sleep(check_interval)


async def _check_all_tunnels(
    conn: sqlite3.Connection,
    tunnel_manager,
    *,
    deep_probe: bool = False,
):
    """Run one cycle of tunnel checks across all active rdev workers.

    Args:
        deep_probe: If True, also run active connectivity probes on tunnels
            whose process is still alive to detect zombie tunnels.
    """
    sessions = sessions_repo.list_sessions(conn, session_type="worker")

    checked = 0
    restarted = 0
    newly_disconnected = []
    # Sessions whose process is alive but need a connectivity probe
    needs_probe: list = []

    for s in sessions:
        # Skip non-rdev, disconnected, or connecting workers
        if not is_remote_host(s.host):
            continue
        if s.status in ("disconnected", "connecting", "error"):
            continue

        checked += 1

        # Fast check: is the tunnel process alive?
        if not tunnel_manager.is_alive(s.id):
            ok = await _restart_tunnel(tunnel_manager, conn, s, reason="process dead")
            restarted += ok
            if not ok:
                newly_disconnected.append(s)
            continue

        # Process is alive — queue for deep probe if this is a probe cycle
        if deep_probe:
            needs_probe.append(s)

    # Run connectivity probes concurrently so we don't wait 8s × N sequentially
    if needs_probe:
        probe_results = await _probe_tunnels_concurrent(tunnel_manager, needs_probe)
        for s, is_healthy in probe_results:
            if not is_healthy:
                ok = await _restart_tunnel(
                    tunnel_manager,
                    conn,
                    s,
                    reason="connectivity probe failed",
                )
                restarted += ok
                if not ok:
                    newly_disconnected.append(s)

    # Mark unreachable workers as disconnected so the UI reflects reality
    # immediately, rather than waiting for the next 5-minute health check.
    # The health check's auto-reconnect will recover them when connectivity
    # returns.
    if newly_disconnected:
        for s in newly_disconnected:
            if s.status not in ("disconnected", "connecting", "error"):
                sessions_repo.update_session(conn, s.id, status="disconnected")
        names = [s.name for s in newly_disconnected]
        logger.warning(
            "Tunnel monitor: marked %d workers disconnected (tunnel restart failed): %s",
            len(names),
            ", ".join(names),
        )
        # Publish events so the UI updates via WebSocket
        try:
            from orchestrator.core.events import Event, publish

            for s in newly_disconnected:
                publish(
                    Event(
                        type="session.status_changed",
                        data={
                            "session_id": s.id,
                            "session_name": s.name,
                            "old_status": s.status,
                            "new_status": "disconnected",
                        },
                    )
                )
        except Exception:
            pass  # best-effort

    if checked > 0:
        msg = "Tunnel monitor: checked %d tunnels, restarted %d"
        if deep_probe:
            msg += f" (deep probe: {len(needs_probe)} probed)"
        logger.debug(msg, checked, restarted)


async def _probe_tunnels_concurrent(tunnel_manager, sessions) -> list[tuple]:
    """Run check_connectivity() for multiple sessions concurrently.

    Returns list of (session, is_healthy) tuples.
    """
    loop = asyncio.get_event_loop()

    async def _probe_one(s):
        healthy = await loop.run_in_executor(
            None,
            tunnel_manager.check_connectivity,
            s.id,
        )
        return (s, healthy)

    results = await asyncio.gather(
        *[_probe_one(s) for s in sessions],
        return_exceptions=True,
    )

    out = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Tunnel monitor: connectivity probe raised: %s", r)
            continue
        out.append(r)
    return out


async def _restart_tunnel(tunnel_manager, conn, session, *, reason: str) -> int:
    """Restart a single tunnel. Returns 1 on success, 0 on failure.

    Caller (_check_all_tunnels) marks sessions disconnected on failure.
    """
    from orchestrator.session.tunnel import ReverseTunnelManager

    failure_count, last_error = tunnel_manager.get_failure_info(session.id)
    if failure_count >= ReverseTunnelManager.MAX_CONSECUTIVE_FAILURES:
        logger.error(
            "Tunnel monitor: %s failed %d consecutive times, giving up (last error: %s)",
            session.name,
            failure_count,
            last_error,
        )
        return 0

    logger.warning(
        "Tunnel monitor: %s tunnel unhealthy (%s), restarting (attempt %d)",
        session.name,
        reason,
        failure_count + 1,
    )
    try:
        new_pid = await asyncio.get_event_loop().run_in_executor(
            None,
            tunnel_manager.restart_tunnel,
            session.id,
            session.name,
            session.host,
        )
        if new_pid:
            sessions_repo.update_session(conn, session.id, tunnel_pid=new_pid)
            logger.info("Tunnel monitor: %s tunnel restarted (pid=%d)", session.name, new_pid)
            return 1
        else:
            logger.warning("Tunnel monitor: %s tunnel restart failed", session.name)
            return 0
    except Exception:
        logger.exception("Tunnel monitor: %s tunnel restart error", session.name)
        return 0
