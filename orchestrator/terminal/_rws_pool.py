"""Server pool — connection pooling for RemoteWorkerServer instances."""

from __future__ import annotations

import logging
import threading
import time

from orchestrator.terminal._rws_client import RemoteWorkerServer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Server pool
# ---------------------------------------------------------------------------
_server_pool: dict[str, RemoteWorkerServer] = {}
_starting: dict[str, threading.Thread] = {}
_pool_lock = threading.Lock()

# Concurrency guard: only one thread per host enters Phase 2 socket reconnect
_reconnecting: set[str] = set()

# Backoff: suppress rapid background starts after failure
_last_start_fail: dict[str, float] = {}
_BACKOFF_SECS: float = 30.0


def get_remote_worker_server(host: str) -> RemoteWorkerServer:
    """Return an alive RemoteWorkerServer for *host*, if one is ready.

    Never blocks: if no server is ready, kicks off a background start and
    raises ``RuntimeError`` immediately so the caller falls back to other
    paths.  Subsequent calls return the server once it's up.
    """
    # Phase 1: Quick check under lock (dict reads/writes only — microseconds)
    server = None
    needs_reconnect = False
    possibly_dead = False
    with _pool_lock:
        server = _server_pool.get(host)
        if server is not None:
            if server._tunnel_proc is not None and server._tunnel_proc.poll() is None:
                if server._cmd_sock is not None:
                    return server
                needs_reconnect = True
            else:
                # Process exited — need TCP check outside lock
                possibly_dead = True

    # Phase 1.5: TCP health check OUTSIDE the lock (may take up to 2s)
    if server is not None and possibly_dead:
        if server._is_tunnel_port_open(timeout=2.0):
            # Port still works (e.g. ControlMaster forwarding) — treat as alive
            if server._cmd_sock is not None:
                return server
            needs_reconnect = True
            possibly_dead = False
        else:
            # Truly dead — remove stale server
            with _pool_lock:
                try:
                    server.stop()
                except Exception:
                    pass
                _server_pool.pop(host, None)
            server = None

    # Phase 2: Socket reconnect OUTSIDE the lock (reduced timeout: 2s)
    # Concurrency guard: only one thread per host attempts reconnect
    if server is not None and needs_reconnect:
        entered = False
        with _pool_lock:
            if host not in _reconnecting:
                _reconnecting.add(host)
                entered = True

        if not entered:
            # Another thread is already reconnecting — skip to Phase 3
            pass
        else:
            try:
                server._connect_command_socket(timeout=2.0)
                logger.info("Reconnected command socket for %s", host)
                return server
            except Exception:
                logger.warning("Socket reconnect failed for %s, restarting", host)
                with _pool_lock:
                    try:
                        server.stop()
                    except Exception:
                        pass
                    _server_pool.pop(host, None)
            finally:
                with _pool_lock:
                    _reconnecting.discard(host)

    # Phase 3: No server available — check if already starting, else kick off background
    with _pool_lock:
        # Already starting in background — don't launch a second one
        if host in _starting and _starting[host].is_alive():
            raise RuntimeError("Connecting to remote host\u2026")

        # Backoff: suppress rapid retries after failure
        fail_time = _last_start_fail.get(host)
        if fail_time is not None:
            elapsed = time.monotonic() - fail_time
            if elapsed < _BACKOFF_SECS:
                raise RuntimeError(
                    f"Connecting to remote host\u2026 (retry in {int(_BACKOFF_SECS - elapsed)}s)"
                )
            # Backoff expired — allow retry
            del _last_start_fail[host]

        # Kick off background start
        def _start_in_background() -> None:
            s = None
            try:
                s = RemoteWorkerServer(host)
                s.start()
                with _pool_lock:
                    _server_pool[host] = s
                    _last_start_fail.pop(host, None)
                logger.info("Remote worker server ready for %s", host)
            except Exception:
                # Clean up leaked SSH tunnel from failed start
                if s is not None:
                    try:
                        s.stop()
                    except Exception:
                        pass
                logger.warning(
                    "Background start of RWS for %s failed, retrying",
                    host,
                    exc_info=True,
                )
                # Retry: deploy daemon (reuses if alive) + tunnel + socket.
                # Do NOT kill the daemon — it may have active PTYs with
                # running Claude sessions that we'd destroy.
                s2 = None
                try:
                    s2 = RemoteWorkerServer(host)
                    s2.start()
                    with _pool_lock:
                        _server_pool[host] = s2
                        _last_start_fail.pop(host, None)
                    logger.info(
                        "Remote worker server ready for %s (retry succeeded)",
                        host,
                    )
                except Exception:
                    # Clean up leaked SSH tunnel from failed retry
                    if s2 is not None:
                        try:
                            s2.stop()
                        except Exception:
                            pass
                    with _pool_lock:
                        _last_start_fail[host] = time.monotonic()
                    logger.warning(
                        "Retry start of RWS for %s also failed",
                        host,
                        exc_info=True,
                    )
            finally:
                with _pool_lock:
                    _starting.pop(host, None)

        t = threading.Thread(target=_start_in_background, daemon=True)
        _starting[host] = t
        t.start()
        raise RuntimeError("Connecting to remote host\u2026")


def force_restart_server(host: str, timeout: float = 30.0) -> RemoteWorkerServer:
    """Kill the remote daemon and start a fresh one synchronously.

    Used when the running daemon is outdated (e.g. missing actions added in
    newer versions).  If active PTYs are running, the upgrade is deferred
    to avoid killing Claude sessions — the old daemon is reused.

    Raises RuntimeError if the restart fails.
    """
    with _pool_lock:
        old = _server_pool.get(host)

    # Check for active PTYs before killing — don't kill Claude sessions
    if old:
        try:
            resp = old.execute({"action": "pty_list"}, timeout=5)
            alive_ptys = [p for p in resp.get("ptys", []) if p.get("alive")]
            if alive_ptys:
                logger.info(
                    "Deferring RWS daemon upgrade for %s: %d active PTYs",
                    host,
                    len(alive_ptys),
                )
                return old  # Reuse old daemon — upgrade when PTYs are gone
        except Exception:
            pass  # Can't query — proceed with restart

    with _pool_lock:
        _server_pool.pop(host, None)
    if old:
        try:
            old.kill_remote_daemon()
            old.stop()
        except Exception:
            pass

    new_rws = RemoteWorkerServer(host)
    new_rws.start(timeout=timeout)
    with _pool_lock:
        _server_pool[host] = new_rws
    logger.info("Force-restarted RWS daemon for %s", host)
    return new_rws


def ensure_rws_starting(host: str) -> None:
    """Trigger a background RWS start for *host* if not already started.

    Called eagerly (e.g. on session page load) so the daemon is ready by
    the time the first operation arrives.  Never blocks or raises.
    """
    try:
        get_remote_worker_server(host)
    except RuntimeError:
        pass  # Expected — "starting in background" or "still starting up"


def shutdown_all_rws_servers() -> None:
    """Stop all remote worker server clients.  Safe to call multiple times."""
    with _pool_lock:
        for host, server in _server_pool.items():
            try:
                server.stop()
            except Exception:
                logger.debug("Error stopping RWS for %s", host, exc_info=True)
        _server_pool.clear()
        _starting.clear()
        _reconnecting.clear()
        _last_start_fail.clear()
    logger.info("All remote worker servers shut down")
