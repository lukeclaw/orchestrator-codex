"""SSH tunnel management for rdev workers.

Provides:
- ReverseTunnelManager: Manages reverse SSH tunnels (-R) as direct subprocesses
  for the orchestrator API tunnel (port 8093). Replaces the old tmux-based approach.
- Forward tunnel discovery/management: Discovers SSH port-forward tunnels (-L)
  via process scanning for user-created tunnels.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field

from orchestrator.session.health import probe_tunnel_connectivity

logger = logging.getLogger(__name__)

# In-memory cache (rebuilt from process scan)
_tunnel_cache: dict[int, dict] = {}
_cache_timestamp: float = 0
CACHE_TTL = 5.0  # seconds

# Reserved ports that cannot be used for forward tunnels
# 8093 is used for the reverse tunnel (API access from rdev to local orchestrator)
# 9222 is reserved for the local shared Chrome instance (CDP debugging port)
RESERVED_PORTS = {8093, 9222}


def get_reserved_ports() -> set:
    """Get the set of reserved ports that cannot be used for forward tunnels."""
    return RESERVED_PORTS.copy()


def is_port_available(port: int) -> bool:
    """Check if a local port is available using lsof.

    Uses lsof to check if any process is listening on the given TCP port.
    Falls back to a socket bind check if lsof is not available.
    """
    try:
        result = subprocess.run(
            ["lsof", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        # lsof -t outputs PIDs of listeners; empty = port is free
        return result.stdout.strip() == ""
    except (subprocess.TimeoutExpired, OSError):
        # lsof not available or timed out — fall back to socket bind
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return True
        except OSError:
            return False


def find_available_port(preferred: int, max_attempts: int = 100) -> int | None:
    """Find an available port, starting from preferred and incrementing.

    Args:
        preferred: The preferred port to start searching from.
        max_attempts: Maximum number of ports to try.

    Returns:
        An available port number, or None if no port found.
    """
    for offset in range(max_attempts):
        candidate = preferred + offset
        if candidate > 65535:
            break
        if candidate in RESERVED_PORTS:
            continue
        if is_port_available(candidate):
            return candidate
    return None


def discover_active_tunnels(force_refresh: bool = False) -> dict[int, dict]:
    """Scan for active SSH port-forward tunnels via ps.

    Returns:
        {local_port: {"pid": int, "remote_port": int, "host": str}}
    """
    global _tunnel_cache, _cache_timestamp

    now = time.time()
    if not force_refresh and (now - _cache_timestamp) < CACHE_TTL and _tunnel_cache:
        return _tunnel_cache.copy()

    tunnels = {}
    try:
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5)

        # Pattern: ssh -N -L <local_port>:localhost:<remote_port> <host>
        # Example: ssh -N -L 4200:localhost:4200 user/rdev-vm
        # Also handle: ssh -N -L 4200:localhost:4200 -o Option=value host
        pattern = re.compile(r"ssh\s+.*-N\s+.*-L\s+(\d+):localhost:(\d+)\s+.*?(\S+)\s*$")

        for line in result.stdout.split("\n"):
            if "ssh" not in line or "-L" not in line or "-N" not in line:
                continue

            # Skip grep processes
            if "grep" in line:
                continue

            match = pattern.search(line)
            if match:
                local_port = int(match.group(1))
                remote_port = int(match.group(2))
                host = match.group(3)

                # Extract PID (second column in ps aux)
                parts = line.split()
                if len(parts) > 1:
                    try:
                        pid = int(parts[1])
                        tunnels[local_port] = {
                            "pid": pid,
                            "remote_port": remote_port,
                            "host": host,
                        }
                        logger.debug(
                            "Discovered tunnel: local:%d -> %s:%d (pid=%d)",
                            local_port,
                            host,
                            remote_port,
                            pid,
                        )
                    except ValueError:
                        pass
    except subprocess.TimeoutExpired:
        logger.warning("Tunnel discovery timed out")
    except Exception as e:
        logger.warning("Tunnel discovery failed: %s", e)

    _tunnel_cache = tunnels
    _cache_timestamp = now
    return tunnels.copy()


def invalidate_cache() -> None:
    """Invalidate the tunnel cache to force fresh discovery."""
    global _cache_timestamp
    _cache_timestamp = 0


def get_tunnels_for_host(host: str) -> dict[int, dict]:
    """Get all tunnels for a specific rdev host.

    Args:
        host: rdev host (e.g., "user/rdev-vm")

    Returns:
        {local_port: {"pid": int, "remote_port": int, "host": str}}
    """
    all_tunnels = discover_active_tunnels()
    return {port: info for port, info in all_tunnels.items() if info["host"] == host}


def find_tunnel_by_port(local_port: int) -> dict | None:
    """Find tunnel info for a specific local port.

    Args:
        local_port: The local port number

    Returns:
        {"pid": int, "remote_port": int, "host": str} or None
    """
    tunnels = discover_active_tunnels()
    return tunnels.get(local_port)


def is_process_alive(pid: int) -> bool:
    """Check if a process is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def create_tunnel(host: str, remote_port: int, local_port: int | None = None) -> tuple[bool, dict]:
    """Create an SSH port-forward tunnel.

    If the requested local port is occupied, automatically finds the next
    available port. The actual local port used is returned in the result dict.

    Args:
        host: rdev host to tunnel to
        remote_port: Port on remote host
        local_port: Local port (defaults to same as remote_port)

    Returns:
        (success: bool, info: dict)
        info contains: {"local_port", "remote_port", "pid", "host"} on success
        or {"error": str} on failure
    """
    requested_port = local_port or remote_port
    local_port = requested_port

    # Validate port range
    if not (1 <= local_port <= 65535) or not (1 <= remote_port <= 65535):
        return False, {"error": "Port must be between 1 and 65535"}

    # Check if there's already a tunnel to the same host+remote_port on a
    # different local port (e.g. requested port 9222 was reserved for local
    # Chrome, so a previous call allocated 9223).  Reuse it to avoid leaks.
    # This check runs BEFORE the reserved-port gate so we can still find and
    # reuse an existing tunnel even when the requested port is reserved.
    for port, info in get_tunnels_for_host(host).items():
        if info["remote_port"] == remote_port and is_process_alive(info["pid"]):
            logger.info(
                "Reusing existing tunnel local:%d -> %s:%d (requested %d)",
                port,
                host,
                remote_port,
                requested_port,
            )
            return True, {
                "local_port": port,
                "remote_port": remote_port,
                "pid": info["pid"],
                "host": host,
                "existing": True,
            }

    # Check for reserved ports — auto-assign a different local port instead
    # of rejecting outright (e.g. 9222 reserved for local Chrome, 8093 for
    # the reverse tunnel).
    if local_port in RESERVED_PORTS:
        new_port = find_available_port(local_port + 1)
        if new_port is None:
            return False, {
                "error": f"Port {local_port} is reserved and no available port found nearby"
            }
        logger.info("Port %d is reserved, using %d instead", local_port, new_port)
        local_port = new_port

    # Check if an existing SSH tunnel already covers this exact request
    existing = find_tunnel_by_port(local_port)
    if existing:
        if is_process_alive(existing["pid"]):
            if existing["host"] == host and existing["remote_port"] == remote_port:
                # Same host, same port - tunnel already exists
                return True, {
                    "local_port": local_port,
                    "remote_port": existing["remote_port"],
                    "pid": existing["pid"],
                    "host": host,
                    "existing": True,
                }

    # Check if the local port is actually available (any process, not just SSH tunnels)
    if not is_port_available(local_port):
        new_port = find_available_port(local_port + 1)
        if new_port is None:
            return False, {
                "error": f"Port {local_port} is occupied and no available port found nearby"
            }
        logger.info("Port %d is occupied, using %d instead", local_port, new_port)
        local_port = new_port

    # Spawn SSH tunnel in background
    try:
        proc = subprocess.Popen(
            ["ssh", "-N", "-L", f"{local_port}:localhost:{remote_port}", host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Give it a moment to fail if it's going to
        time.sleep(0.2)

        if proc.poll() is not None:
            # Process already exited - failed to establish
            return False, {"error": f"SSH tunnel failed to start (exit code: {proc.returncode})"}

        # Invalidate cache
        invalidate_cache()

        logger.info(
            "Created tunnel local:%d -> %s:%d (pid=%d)", local_port, host, remote_port, proc.pid
        )

        return True, {
            "local_port": local_port,
            "remote_port": remote_port,
            "pid": proc.pid,
            "host": host,
        }
    except Exception as e:
        logger.error("Failed to create tunnel: %s", e)
        return False, {"error": str(e)}


def close_tunnel(local_port: int, host: str | None = None) -> tuple[bool, str]:
    """Close an SSH tunnel on a specific port.

    Args:
        local_port: The local port of the tunnel
        host: Optional host to verify ownership (safety check)

    Returns:
        (success: bool, message: str)
    """
    tunnel = find_tunnel_by_port(local_port)

    if not tunnel:
        return False, f"No tunnel found on port {local_port}"

    # Verify host if provided
    if host and tunnel["host"] != host:
        return False, f"Port {local_port} belongs to {tunnel['host']}, not {host}"

    pid = tunnel["pid"]
    try:
        os.kill(pid, signal.SIGTERM)
        logger.info("Closed tunnel on port %d (pid=%d)", local_port, pid)
        invalidate_cache()
        return True, f"Tunnel on port {local_port} closed"
    except ProcessLookupError:
        invalidate_cache()
        return True, f"Tunnel process {pid} already dead"
    except PermissionError:
        return False, f"Permission denied killing process {pid}"


def cleanup_tunnels_for_host(host: str) -> int:
    """Kill all tunnels for a given rdev host.

    Args:
        host: rdev host (e.g., "user/rdev-vm")

    Returns:
        Number of tunnels closed
    """
    tunnels = get_tunnels_for_host(host)
    closed = 0

    for port, info in tunnels.items():
        try:
            os.kill(info["pid"], signal.SIGTERM)
            logger.info("Cleanup: killed tunnel on port %d for host %s", port, host)
            closed += 1
        except ProcessLookupError:
            pass
        except PermissionError:
            logger.warning("Permission denied killing tunnel pid %d", info["pid"])

    if closed > 0:
        invalidate_cache()

    return closed


# =====================================================================
# Reverse Tunnel Manager (replaces tmux-based tunnel management)
# =====================================================================

# Default port used by the reverse tunnel for API access
DEFAULT_API_PORT = 8093

# Seconds to wait after SIGTERM before escalating to SIGKILL
_GRACEFUL_TIMEOUT = 3.0

# Directory for tunnel stderr logs
DEFAULT_LOG_DIR = "/tmp/orchestrator/tunnels"


@dataclass
class _TunnelEntry:
    """Internal state for a managed reverse tunnel."""

    proc: subprocess.Popen | _AdoptedProcess
    host: str
    session_name: str
    pid: int
    log_file: object | None = None  # open file handle for stderr
    started_at: float = field(default_factory=time.time)


class _AdoptedProcess:
    """Minimal stand-in for subprocess.Popen for adopted (orphaned) processes.

    We can't create a real Popen object for a process we didn't start,
    so this provides the minimal interface needed by ReverseTunnelManager.
    """

    def __init__(self, pid: int):
        self.pid = pid
        self._returncode: int | None = None

    def poll(self) -> int | None:
        if self._returncode is not None:
            return self._returncode
        try:
            os.kill(self.pid, 0)
            return None  # still alive
        except ProcessLookupError:
            self._returncode = -1
            return self._returncode
        except PermissionError:
            # Can't signal it — assume alive (conservative)
            return None

    def terminate(self):
        try:
            os.kill(self.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    def kill(self):
        try:
            os.kill(self.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def wait(self, timeout=None):
        """Best-effort wait — can't waitpid on non-child processes."""
        if timeout:
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    os.kill(self.pid, 0)
                except ProcessLookupError:
                    self._returncode = -1
                    return self._returncode
                except PermissionError:
                    return None
                time.sleep(0.2)
        return self._returncode


class ReverseTunnelManager:
    """Manages SSH reverse tunnels as direct subprocesses.

    Replaces the old approach of typing SSH commands into tmux windows,
    which was fragile due to shell initialization issues (oh-my-zsh prompts,
    etc.) and unreliable health checking via terminal output parsing.

    Each tunnel is a direct ``ssh -N -R`` subprocess with:
    - Deterministic health checking via ``proc.poll()`` (no string parsing)
    - ``start_new_session=True`` so tunnels survive orchestrator restarts
    - SIGTERM → SIGKILL escalation for reliable cleanup
    - Persistent PID storage in DB for cross-restart recovery

    Thread-safe: all dict mutations are protected by a lock.
    """

    # After this many consecutive startup failures, the tunnel monitor
    # should stop retrying and mark the session as error.
    MAX_CONSECUTIVE_FAILURES = 5

    def __init__(
        self,
        api_port: int = DEFAULT_API_PORT,
        log_dir: str = DEFAULT_LOG_DIR,
    ):
        self.api_port = api_port
        self.log_dir = log_dir
        self._tunnels: dict[str, _TunnelEntry] = {}  # session_id → entry
        self._lock = threading.Lock()
        self._failure_counts: dict[str, int] = {}
        self._last_errors: dict[str, str | None] = {}

        # Ensure log directory exists
        os.makedirs(self.log_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_tunnel(
        self,
        session_id: str,
        session_name: str,
        host: str,
        local_port: int | None = None,
        remote_port: int | None = None,
    ) -> int | None:
        """Start a reverse SSH tunnel for a session.

        Returns the PID on success, None on failure.
        """
        local_port = local_port or self.api_port
        remote_port = remote_port or self.api_port

        # Stop any existing tunnel for this session first (without clearing
        # failure tracking — that only resets on success or explicit stop).
        self._stop_tunnel_internal(session_id)

        log_path = os.path.join(self.log_dir, f"{session_name}.log")
        try:
            log_file = open(log_path, "a")
        except OSError as e:
            logger.error("Cannot open tunnel log %s: %s", log_path, e)
            log_file = None

        # NOTE: Do NOT use ClearAllForwardings=yes here. Despite the intent to
        # clear config-file LocalForward entries, it also clears the -R flag
        # from the command line (confirmed in OpenSSH 10.2). This causes the
        # tunnel to connect successfully but never set up port forwarding —
        # a silent failure where proc.poll() shows "alive" but the remote
        # port is never bound.
        #
        # NOTE: Do NOT use ExitOnForwardFailure=yes here. The rdev CLI writes
        # LocalForward entries into ~/.ssh/config.rdev (e.g. LocalForward
        # 0.0.0.0:8080 127.0.0.1:8080). SSH inherits these even for our
        # "ssh -N -R" command. If that local port is already bound by another
        # rdev's tunnel, the bind fails and ExitOnForwardFailure kills the
        # entire SSH session — including the -R reverse tunnel we need.
        # Instead, we check the log after startup to distinguish fatal errors
        # (remote forwarding failure) from non-fatal ones (inherited
        # LocalForward conflict).
        cmd = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
            "-N",
            "-R",
            f"{remote_port}:127.0.0.1:{local_port}",
            host,
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log_file if log_file else subprocess.DEVNULL,
                start_new_session=True,  # survive orchestrator restarts
            )
        except OSError as e:
            logger.error("Failed to start tunnel for %s: %s", session_name, e)
            if log_file:
                log_file.close()
            return None

        # Record log position before startup so we only inspect new output.
        log_start_pos = 0
        if log_path:
            try:
                log_start_pos = os.path.getsize(log_path)
            except OSError:
                log_start_pos = 0

        # Verify SSH survived startup. Hard failures (auth, connection
        # refused, etc.) cause the process to exit within ~1-2s.
        time.sleep(3)
        exit_code = proc.poll()
        if exit_code is not None:
            last_error = self._read_last_log_line(log_path)
            logger.error(
                "Tunnel for %s exited during startup (exit_code=%d, error=%s)",
                session_name,
                exit_code,
                last_error,
            )
            with self._lock:
                self._failure_counts[session_id] = self._failure_counts.get(session_id, 0) + 1
                self._last_errors[session_id] = last_error
            if log_file:
                log_file.close()
            return None

        # Process is alive — inspect the log for forwarding errors.
        new_log = self._read_log_since(log_path, log_start_pos)
        if new_log:
            if "remote port forwarding failed" in new_log.lower():
                # The -R forward we actually need has failed. Fatal.
                logger.error(
                    "Tunnel for %s: remote port forwarding failed, killing process (pid=%d)",
                    session_name,
                    proc.pid,
                )
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                with self._lock:
                    self._failure_counts[session_id] = self._failure_counts.get(session_id, 0) + 1
                    self._last_errors[session_id] = "remote port forwarding failed"
                if log_file:
                    log_file.close()
                return None

            if "could not request local forwarding" in new_log.lower():
                # An inherited LocalForward from ~/.ssh/config.rdev failed.
                # This is non-fatal — the -R reverse tunnel is fine.
                logger.warning(
                    "Tunnel for %s: inherited LocalForward failed (non-fatal, "
                    "reverse tunnel OK). Log: %s",
                    session_name,
                    new_log.strip()[:200],
                )

        entry = _TunnelEntry(
            proc=proc,
            host=host,
            session_name=session_name,
            pid=proc.pid,
            log_file=log_file,
        )

        with self._lock:
            self._tunnels[session_id] = entry
            # Successful start resets failure tracking
            self._failure_counts.pop(session_id, None)
            self._last_errors.pop(session_id, None)

        logger.info(
            "Started tunnel for %s (host=%s, pid=%d, port=%d→%d)",
            session_name,
            host,
            proc.pid,
            local_port,
            remote_port,
        )
        return proc.pid

    def _stop_tunnel_internal(self, session_id: str) -> bool:
        """Stop a tunnel without clearing failure tracking.

        Used by start_tunnel/restart_tunnel when replacing an existing tunnel.
        """
        with self._lock:
            entry = self._tunnels.pop(session_id, None)

        if entry is None:
            return False

        self._kill_entry(entry)
        logger.info("Stopped tunnel for %s (pid=%d)", entry.session_name, entry.pid)
        return True

    def stop_tunnel(self, session_id: str) -> bool:
        """Stop a tunnel with SIGTERM → wait → SIGKILL escalation.

        Returns True if a tunnel was stopped, False if none existed.
        Manual stop clears failure tracking so the next start is fresh.
        """
        result = self._stop_tunnel_internal(session_id)
        with self._lock:
            self._failure_counts.pop(session_id, None)
            self._last_errors.pop(session_id, None)
        return result

    def restart_tunnel(
        self,
        session_id: str,
        session_name: str,
        host: str,
    ) -> int | None:
        """Stop and restart a tunnel atomically. Returns new PID or None.

        Holds the internal lock across stop+start to prevent concurrent
        restarts from creating orphaned SSH processes.
        """
        # Pop existing entry under lock (same as stop_tunnel but inline)
        with self._lock:
            old_entry = self._tunnels.pop(session_id, None)

        if old_entry is not None:
            self._kill_entry(old_entry)
            logger.info(
                "Stopped tunnel for %s (pid=%d) before restart",
                old_entry.session_name,
                old_entry.pid,
            )

        return self.start_tunnel(session_id, session_name, host)

    def is_alive(self, session_id: str) -> bool:
        """Fast check: is the tunnel process still running?

        Uses proc.poll() which is instant and deterministic.
        No string parsing, no heuristics.
        """
        with self._lock:
            entry = self._tunnels.get(session_id)

        if entry is None:
            return False

        status = entry.proc.poll()
        if status is not None:
            # Process exited — log the exit code
            logger.info(
                "Tunnel for %s (pid=%d) exited with code %d",
                entry.session_name,
                entry.pid,
                status,
            )
            # Clean up the entry
            self._cleanup_dead_entry(session_id, entry)
            return False

        return True

    def check_connectivity(self, session_id: str) -> bool:
        """Definitive check: is the tunnel actually working?

        Two-tier: process alive AND active probe succeeds.
        """
        if not self.is_alive(session_id):
            return False

        with self._lock:
            entry = self._tunnels.get(session_id)
        if entry is None:
            return False

        return probe_tunnel_connectivity(entry.host, self.api_port)

    def get_pid(self, session_id: str) -> int | None:
        """Get the PID of a tunnel, or None if not managed."""
        with self._lock:
            entry = self._tunnels.get(session_id)
        return entry.pid if entry else None

    def get_host(self, session_id: str) -> str | None:
        """Get the host of a tunnel, or None if not managed."""
        with self._lock:
            entry = self._tunnels.get(session_id)
        return entry.host if entry else None

    def has_tunnel(self, session_id: str) -> bool:
        """Check if a tunnel is registered (alive or dead)."""
        with self._lock:
            return session_id in self._tunnels

    def get_failure_info(self, session_id: str) -> tuple[int, str | None]:
        """Return (failure_count, last_error) for a session's tunnel."""
        with self._lock:
            return (
                self._failure_counts.get(session_id, 0),
                self._last_errors.get(session_id),
            )

    def clear_failure_info(self, session_id: str) -> None:
        """Reset failure tracking for a session."""
        with self._lock:
            self._failure_counts.pop(session_id, None)
            self._last_errors.pop(session_id, None)

    def list_tunnels(self) -> list[dict]:
        """List all managed tunnels with their status."""
        with self._lock:
            entries = list(self._tunnels.items())

        result = []
        for sid, entry in entries:
            alive = entry.proc.poll() is None
            result.append(
                {
                    "session_id": sid,
                    "session_name": entry.session_name,
                    "host": entry.host,
                    "pid": entry.pid,
                    "alive": alive,
                    "uptime_seconds": time.time() - entry.started_at,
                }
            )
        return result

    # ------------------------------------------------------------------
    # Startup recovery
    # ------------------------------------------------------------------

    def recover_tunnel(
        self,
        session_id: str,
        session_name: str,
        host: str,
        stored_pid: int | None,
    ) -> int | None:
        """Recover a tunnel after orchestrator restart.

        If the stored PID is still alive and is actually a tunnel to the
        right host, adopt it. Otherwise, kill orphans and start fresh.

        Returns the (adopted or new) PID, or None on failure.
        """
        if stored_pid and self._try_adopt(session_id, session_name, host, stored_pid):
            logger.info("Adopted existing tunnel for %s (pid=%d)", session_name, stored_pid)
            return stored_pid

        # Stored PID is dead or wrong — clean up any orphans and start fresh
        self._kill_orphan_tunnels(host)
        pid = self.start_tunnel(session_id, session_name, host)
        if pid:
            logger.info("Started fresh tunnel for %s (pid=%d)", session_name, pid)
        return pid

    def _try_adopt(
        self,
        session_id: str,
        session_name: str,
        host: str,
        pid: int,
    ) -> bool:
        """Try to adopt an orphaned tunnel process.

        Validates that the PID is alive AND is actually an SSH tunnel
        for the given host (prevents PID recycling false positives).
        """
        # Check if PID is alive
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return False

        # Cross-validate: is this PID actually a reverse tunnel for this host?
        from orchestrator.session.health import find_tunnel_pids

        tunnel_pids = find_tunnel_pids(host)
        if pid not in tunnel_pids:
            logger.warning(
                "PID %d is alive but not a reverse tunnel for %s (found pids: %s)",
                pid,
                host,
                tunnel_pids,
            )
            return False

        entry = _TunnelEntry(
            proc=_AdoptedProcess(pid),
            host=host,
            session_name=session_name,
            pid=pid,
            log_file=None,
        )

        with self._lock:
            self._tunnels[session_id] = entry

        return True

    def _kill_orphan_tunnels(self, host: str):
        """Kill any orphaned SSH reverse tunnel processes for a host."""
        from orchestrator.session.health import find_tunnel_pids

        pids = find_tunnel_pids(host)
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
                logger.info("Sent SIGTERM to orphan tunnel pid %d for %s", pid, host)
            except (ProcessLookupError, PermissionError):
                pass

        if pids:
            time.sleep(1)
            for pid in pids:
                try:
                    os.kill(pid, 0)  # check if still alive
                    os.kill(pid, signal.SIGKILL)
                    logger.warning("Sent SIGKILL to stuck orphan pid %d", pid)
                except (ProcessLookupError, PermissionError):
                    pass

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def stop_all(self):
        """Stop all managed tunnels. Called during graceful shutdown."""
        with self._lock:
            session_ids = list(self._tunnels.keys())

        for sid in session_ids:
            try:
                self.stop_tunnel(sid)
            except Exception:
                logger.exception("Error stopping tunnel for session %s", sid)

        logger.info("All tunnels stopped (%d)", len(session_ids))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _kill_entry(self, entry: _TunnelEntry):
        """Kill a tunnel process with SIGTERM → wait → SIGKILL."""
        pid = entry.pid
        proc = entry.proc

        # Phase 1: SIGTERM
        try:
            if isinstance(proc, _AdoptedProcess):
                os.kill(pid, signal.SIGTERM)
            else:
                proc.terminate()
        except (ProcessLookupError, PermissionError):
            pass

        # Phase 2: Wait for graceful exit
        deadline = time.time() + _GRACEFUL_TIMEOUT
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            except PermissionError:
                break
            time.sleep(0.5)
        else:
            # Phase 3: SIGKILL
            try:
                if isinstance(proc, _AdoptedProcess):
                    os.kill(pid, signal.SIGKILL)
                else:
                    proc.kill()
                logger.warning("Sent SIGKILL to stuck tunnel pid %d", pid)
            except (ProcessLookupError, PermissionError):
                pass

        # Reap zombie if we have a real Popen
        if not isinstance(proc, _AdoptedProcess):
            try:
                proc.wait(timeout=2)
            except (subprocess.TimeoutExpired, ChildProcessError):
                pass

        # Close log file
        if entry.log_file:
            try:
                entry.log_file.close()
            except Exception:
                pass

    def _cleanup_dead_entry(self, session_id: str, entry: _TunnelEntry):
        """Clean up a dead tunnel entry (close log file, remove from dict)."""
        with self._lock:
            # Only remove if it's still the same entry (avoid race)
            current = self._tunnels.get(session_id)
            if current is entry:
                del self._tunnels[session_id]

        if entry.log_file:
            try:
                entry.log_file.close()
            except Exception:
                pass

        # Reap zombie
        if not isinstance(entry.proc, _AdoptedProcess):
            try:
                entry.proc.wait(timeout=1)
            except (subprocess.TimeoutExpired, ChildProcessError):
                pass

    @staticmethod
    def _read_log_since(log_path: str, start_pos: int) -> str | None:
        """Read log content written after *start_pos* bytes.

        Returns the new content as a string, or None if nothing was written
        (or the file is unreadable).
        """
        try:
            with open(log_path, "rb") as f:
                f.seek(0, 2)  # seek to end
                end_pos = f.tell()
                if end_pos <= start_pos:
                    return None
                f.seek(start_pos)
                data = f.read(end_pos - start_pos)
                return data.decode("utf-8", errors="replace")
        except OSError:
            return None

    @staticmethod
    def _read_last_log_line(log_path: str) -> str | None:
        """Read the last non-empty line from a tunnel log file.

        Opens a separate read-only FD (safe to call while the subprocess
        still has the file open for writing on POSIX).
        """
        try:
            with open(log_path, "rb") as f:
                f.seek(0, 2)  # seek to end
                size = f.tell()
                if size == 0:
                    return None
                read_size = min(size, 2048)
                f.seek(-read_size, 2)
                data = f.read(read_size).decode("utf-8", errors="replace")
                for line in reversed(data.splitlines()):
                    stripped = line.strip()
                    if stripped:
                        return stripped
        except OSError:
            pass
        return None
