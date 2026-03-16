"""Health check utilities for session management.

Functions to check the status of Claude processes, screen sessions,
SSH tunnels, and SSH connections for both local and rdev workers.
"""

import asyncio
import logging
import os
import shlex
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC

from orchestrator.api.ws_terminal import is_user_active
from orchestrator.state.repositories import sessions as repo
from orchestrator.terminal.manager import ensure_window, kill_window, tmux_target, window_exists
from orchestrator.terminal.ssh import is_remote_host

logger = logging.getLogger(__name__)

# Default port used by the reverse tunnel for API access
DEFAULT_API_PORT = 8093


class _HostCircuitBreaker:
    """Per-host circuit breaker for health checks.

    CLOSED: normal operation, health checks proceed
    OPEN: host is unreachable, skip health checks instantly
    HALF_OPEN: cooldown expired, allow one probe to test recovery
    """

    FAILURE_THRESHOLD = 3
    COOLDOWN_SECONDS = 30.0

    def __init__(self):
        self._states: dict[str, str] = {}  # host -> "closed"|"open"|"half_open"
        self._failures: dict[str, int] = {}  # host -> consecutive failure count
        self._open_since: dict[str, float] = {}  # host -> time when OPEN was entered
        self._lock = threading.Lock()

    def should_skip(self, host: str) -> bool:
        """Return True if the host should be skipped (OPEN state)."""
        with self._lock:
            state = self._states.get(host, "closed")
            if state == "closed":
                return False
            if state == "open":
                elapsed = time.time() - self._open_since.get(host, 0)
                if elapsed >= self.COOLDOWN_SECONDS:
                    self._states[host] = "half_open"
                    return False  # allow one probe
                return True  # still in cooldown
            # half_open — allow the probe
            return False

    def record_success(self, host: str) -> None:
        with self._lock:
            self._states[host] = "closed"
            self._failures[host] = 0

    def record_failure(self, host: str) -> None:
        with self._lock:
            count = self._failures.get(host, 0) + 1
            self._failures[host] = count
            if count >= self.FAILURE_THRESHOLD:
                self._states[host] = "open"
                self._open_since[host] = time.time()

    def get_state(self, host: str) -> str:
        with self._lock:
            return self._states.get(host, "closed")


# Module-level singleton
_host_breaker = _HostCircuitBreaker()


class _ReconnectBackoff:
    """Per-session exponential backoff for auto-reconnect attempts.

    Prevents rapid oscillation between working/disconnected by enforcing
    increasing delays between reconnect attempts.  There is NO hard attempt
    limit — the delay caps at ``_MAX_DELAY`` seconds so that workers always
    eventually recover (e.g. after a VPN reconnect).
    """

    _BASE_DELAY = 15  # seconds
    _MAX_DELAY = 300  # 5 minutes

    def __init__(self):
        self._lock = threading.Lock()
        self._attempts: dict[str, int] = {}  # session_id → consecutive failures
        self._last_attempt: dict[str, float] = {}  # session_id → timestamp

    def should_skip(self, session_id: str) -> bool:
        """Return True if the session is still within its backoff window."""
        with self._lock:
            attempts = self._attempts.get(session_id, 0)
            if attempts == 0:
                return False
            delay = min(self._BASE_DELAY * (2 ** (attempts - 1)), self._MAX_DELAY)
            elapsed = time.time() - self._last_attempt.get(session_id, 0)
            return elapsed < delay

    def record_attempt(self, session_id: str):
        """Record that a reconnect attempt was started."""
        with self._lock:
            self._last_attempt[session_id] = time.time()

    def record_failure(self, session_id: str):
        """Record a reconnect failure (increments backoff)."""
        with self._lock:
            self._attempts[session_id] = self._attempts.get(session_id, 0) + 1

    def record_success(self, session_id: str):
        """Record a reconnect success (resets backoff)."""
        with self._lock:
            self._attempts.pop(session_id, None)
            self._last_attempt.pop(session_id, None)

    def cleanup(self, session_id: str):
        """Remove all tracking for a session (call on session delete)."""
        with self._lock:
            self._attempts.pop(session_id, None)
            self._last_attempt.pop(session_id, None)


_reconnect_backoff = _ReconnectBackoff()

# Guard to prevent concurrent health-check-all runs
_health_check_all_lock = threading.Lock()


def find_tunnel_pids(host: str) -> list[int]:
    """Find PIDs of SSH tunnel processes for a given host.

    Scans ps output for SSH processes with -N (no command) and -R (reverse tunnel)
    targeting the given host.

    Args:
        host: rdev host (e.g., "user/rdev-vm")

    Returns:
        List of matching PIDs
    """
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pids = []
        for line in result.stdout.split("\n"):
            if (
                "ssh" in line
                and "-N" in line
                and "-R" in line
                and host in line
                and "grep" not in line
            ):
                parts = line.split()
                if len(parts) > 1:
                    try:
                        pids.append(int(parts[1]))
                    except ValueError:
                        pass
        return pids
    except Exception as e:
        logger.warning("find_tunnel_pids error: %s", e)
        return []


def kill_tunnel_processes(host: str, graceful_timeout: float = 3.0) -> int:
    """Kill all SSH tunnel processes for a given host with SIGKILL escalation.

    1. Send SIGTERM to all matching tunnel PIDs
    2. Wait up to graceful_timeout seconds for them to exit
    3. Send SIGKILL to any that are still alive

    This handles the case where SSH -N processes get stuck and resist Ctrl-C/SIGTERM.

    Args:
        host: rdev host (e.g., "user/rdev-vm")
        graceful_timeout: Seconds to wait after SIGTERM before escalating to SIGKILL

    Returns:
        Number of processes killed
    """
    pids = find_tunnel_pids(host)
    if not pids:
        return 0

    logger.info("Killing %d tunnel processes for %s: %s", len(pids), host, pids)

    # Phase 1: SIGTERM
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            logger.debug("Sent SIGTERM to tunnel pid %d", pid)
        except ProcessLookupError:
            pass
        except PermissionError:
            logger.warning("Permission denied sending SIGTERM to pid %d", pid)

    # Phase 2: Wait for graceful exit
    deadline = time.time() + graceful_timeout
    still_alive = list(pids)
    while still_alive and time.time() < deadline:
        time.sleep(0.5)
        still_alive = [pid for pid in still_alive if _is_pid_alive(pid)]

    # Phase 3: SIGKILL any survivors
    killed = len(pids)
    for pid in still_alive:
        try:
            os.kill(pid, signal.SIGKILL)
            logger.warning("Sent SIGKILL to stuck tunnel pid %d for %s", pid, host)
        except ProcessLookupError:
            pass
        except PermissionError:
            logger.warning("Permission denied sending SIGKILL to pid %d", pid)
            killed -= 1

    return killed


def _is_pid_alive(pid: int) -> bool:
    """Check if a process is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _get_pane_pid(tmux_sess: str, tmux_win: str) -> int | None:
    """Get the PID of the shell running in a tmux pane.

    Returns the pane PID or None if the pane doesn't exist.
    """
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-t", f"{tmux_sess}:{tmux_win}", "-p", "#{pane_pid}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError):
        pass
    except Exception as e:
        logger.debug("Failed to get pane PID for %s:%s: %s", tmux_sess, tmux_win, e)
    return None


def _has_ssh_in_process_tree(root_pid: int) -> bool:
    """Check if there is an active ``ssh`` process descended from *root_pid*.

    Builds a parent→children map from ``ps -eo pid,ppid,comm`` and performs a
    BFS from *root_pid*.  Returns True as soon as any descendant's command
    name contains ``ssh``.
    """
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,ppid,comm"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False

        children: dict[int, list[int]] = {}
        commands: dict[int, str] = {}
        for line in result.stdout.strip().split("\n")[1:]:  # skip header
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            try:
                pid = int(parts[0])
                ppid = int(parts[1])
            except ValueError:
                continue
            comm = parts[2].strip()
            children.setdefault(ppid, []).append(pid)
            commands[pid] = comm

        # BFS from root_pid
        queue = children.get(root_pid, [])
        while queue:
            pid = queue.pop(0)
            comm = commands.get(pid, "")
            if "ssh" in comm:
                return True
            queue.extend(children.get(pid, []))

        return False
    except subprocess.TimeoutExpired:
        return False
    except Exception as e:
        logger.debug("_has_ssh_in_process_tree error: %s", e)
        return False


def check_tui_running_in_pane(tmux_sess: str, tmux_win: str) -> bool:
    """Check if the tmux pane is in alternate screen buffer mode (TUI running).

    This is a tmux QUERY (display-message), NOT send-keys.
    Completely non-intrusive. Safe to call anytime.

    Note: Claude Code does NOT use the alternate screen buffer — only GNU
    Screen does.  For remote workers, ``#{alternate_on}`` == "1" means the
    pane is attached to a screen session, while "0" means the pane is at
    a bare shell prompt (screen detached or not running).
    """
    try:
        target = f"{tmux_sess}:{tmux_win}"
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", target, "#{alternate_on}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() == "1"
    except (subprocess.TimeoutExpired, Exception) as e:
        logger.debug("check_tui_running_in_pane(%s:%s) error: %s", tmux_sess, tmux_win, e)
        return False


def check_worker_ssh_alive(tmux_sess: str, tmux_win: str, host: str) -> bool:
    """Check if the worker SSH session is still connected to rdev.

    Scoped to the specific tmux pane for this worker — avoids false positives
    from other terminals that may have their own ``rdev ssh`` to the same host
    (e.g. for auth handling).

    The check works by:
    1. Getting the pane PID (the shell process running in the tmux pane).
    2. Walking the process tree from that PID to look for an ``ssh`` descendant.
       When connected, the tree is: shell → rdev (python) → ssh.
       When disconnected, ``ssh`` (and often ``rdev``) are gone.

    Args:
        tmux_sess: tmux session name
        tmux_win: tmux window name
        host: The rdev host (e.g., "subs-mt/sleepy-franklin")

    Returns:
        True if the pane's process tree contains an active ssh process.
    """
    pane_pid = _get_pane_pid(tmux_sess, tmux_win)
    if pane_pid is None:
        logger.info("Worker SSH check: cannot get pane PID for %s:%s", tmux_sess, tmux_win)
        return False

    has_ssh = _has_ssh_in_process_tree(pane_pid)
    if has_ssh:
        logger.info("Worker SSH check: ssh descendant found under pane %d for %s", pane_pid, host)
    else:
        logger.info("Worker SSH check: no ssh descendant under pane %d for %s", pane_pid, host)
    return has_ssh


def probe_tunnel_connectivity(
    host: str, remote_port: int = DEFAULT_API_PORT, timeout: int = 8
) -> bool:
    """Actively test if the reverse tunnel works by SSHing to host and curling the tunneled port.

    This provides a definitive answer about tunnel health by testing actual connectivity,
    unlike the old check_tunnel_alive() which only inspected tmux output heuristics.

    Args:
        host: rdev host (e.g., "user/rdev-vm")
        remote_port: Port on remote that should be tunneled back to local
        timeout: Total timeout for the SSH+curl operation

    Returns:
        True if the tunnel is working (remote can reach local API), False otherwise
    """
    try:
        # curl the health endpoint through the tunnel from the remote side
        curl_cmd = (
            f"curl -s -o /dev/null -w '%{{http_code}}' "
            f"--connect-timeout 3 http://localhost:{remote_port}/api/health"
        )
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, curl_cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        http_code = result.stdout.strip().strip("'\"")
        if http_code == "200":
            logger.debug("Tunnel probe: %s port %d - healthy (HTTP 200)", host, remote_port)
            return True
        else:
            logger.info(
                "Tunnel probe: %s port %d - unhealthy (HTTP %s, stderr=%s)",
                host,
                remote_port,
                http_code,
                result.stderr.strip()[:100],
            )
            return False
    except subprocess.TimeoutExpired:
        logger.info("Tunnel probe: %s port %d - timed out", host, remote_port)
        return False
    except Exception as e:
        logger.warning("Tunnel probe: %s port %d - error: %s", host, remote_port, e)
        return False


def _has_claude_in_process_tree(root_pid: int) -> bool:
    """Check if there is a ``claude`` process descended from *root_pid*.

    Uses the full command args (``ps -eo pid,ppid,args``) instead of just
    the short command name because Claude Code runs via a Node.js wrapper
    and the ``comm`` field may show ``node`` instead of ``claude``.
    """
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,ppid,args"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False

        children: dict[int, list[int]] = {}
        commands: dict[int, str] = {}
        for line in result.stdout.strip().split("\n")[1:]:  # skip header
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            try:
                pid = int(parts[0])
                ppid = int(parts[1])
            except ValueError:
                continue
            args = parts[2].strip()
            children.setdefault(ppid, []).append(pid)
            commands[pid] = args

        # BFS from root_pid
        queue = children.get(root_pid, [])
        while queue:
            pid = queue.pop(0)
            args = commands.get(pid, "")
            # Match "claude" in command args but exclude grep/ps artifacts
            if "claude" in args.lower() and "grep" not in args:
                return True
            queue.extend(children.get(pid, []))

        return False
    except subprocess.TimeoutExpired:
        return False
    except Exception as e:
        logger.debug("_has_claude_in_process_tree error: %s", e)
        return False


def check_claude_process_local(session_id: str) -> tuple[bool, str]:
    """Check if Claude Code with given session_id is running locally via ps.

    Args:
        session_id: The session ID to search for in Claude's -r flag

    Returns:
        (alive: bool, reason: str) - whether Claude is running and why
    """
    try:
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5)

        # Look for claude process with our session_id
        for line in result.stdout.split("\n"):
            if "claude" in line.lower() and session_id in line and "grep" not in line:
                logger.debug("Found Claude process for session %s: %s", session_id, line[:100])
                return True, "Claude process running"

        return False, f"No Claude process found for session {session_id}"
    except subprocess.TimeoutExpired:
        return True, "Health check timed out"
    except Exception as e:
        logger.warning("Health check ps command failed: %s", e)
        return True, f"Health check error: {e}"


def check_claude_running_local(
    session_id: str,
    claude_session_id: str | None,
    tmux_sess: str,
    tmux_win: str,
) -> tuple[bool, str]:
    """Check if Claude Code is running for a local worker.

    Uses two complementary methods:
    1. Process tree walk from the tmux pane PID — reliable and does not
       depend on which session ID was passed at launch time.  This handles
       the common case where ``claude_session_id`` diverges from the
       command-line ``--session-id`` after ``/clear`` or ``/compact``.
    2. ``ps aux`` scan for known session IDs — fallback when the pane PID
       cannot be determined.

    Args:
        session_id: Orchestrator session ID (passed as ``--session-id`` at launch).
        claude_session_id: Claude's internal session ID (may differ after /clear).
        tmux_sess: tmux session name.
        tmux_win: tmux window name.

    Returns:
        (alive, reason)
    """
    # Primary: walk the pane's process tree for a claude descendant.
    pane_pid = _get_pane_pid(tmux_sess, tmux_win)
    if pane_pid is not None and _has_claude_in_process_tree(pane_pid):
        return True, "Claude process running in pane"

    # Fallback: ps aux with orchestrator ID (always present on initial launch).
    alive, reason = check_claude_process_local(session_id)
    if alive:
        return alive, reason

    # Try Claude's tracked ID if different (may be in cmd after reconnect).
    if claude_session_id and claude_session_id != session_id:
        alive, reason = check_claude_process_local(claude_session_id)
        if alive:
            return alive, reason

    return False, reason


# =============================================================================
# Tmp Directory Health — manifest-based verification and recovery
# =============================================================================


def ensure_tmp_dir_health(
    tmp_dir: str,
    session_id: str,
    api_base: str = "http://127.0.0.1:8093",
    cdp_port: int = 9222,
    browser_headless: bool = False,
    conn=None,
) -> dict:
    """Check if the local tmp dir has all required files; regenerate if any missing.

    Verification strategy (ordered by cost):
    1. Manifest missing → full regeneration (covers complete /tmp wipe)
    2. Any file in manifest missing → full regeneration (covers partial wipe)
    3. All files present → no-op

    Args:
        tmp_dir: Worker's tmp directory
        session_id: Worker's session ID
        api_base: API base URL
        cdp_port: CDP port for browser debugging
        browser_headless: Whether browser runs headless
        conn: Optional DB connection for reading skills when regenerating

    Returns:
        {"healthy": bool, "regenerated": bool, "missing": list[str]}
    """
    from orchestrator.agents.deploy import _read_manifest, deploy_worker_tmp_contents

    manifest = _read_manifest(tmp_dir)

    if manifest is None:
        # Manifest missing — whole dir was likely wiped
        logger.warning("Tmp dir manifest missing: %s — regenerating", tmp_dir)
        deploy_worker_tmp_contents(
            tmp_dir,
            session_id,
            api_base=api_base,
            cdp_port=cdp_port,
            browser_headless=browser_headless,
            conn=conn,
        )
        return {"healthy": False, "regenerated": True, "missing": ["<manifest>"]}

    # Check every file listed in the manifest
    missing = [p for p in manifest if not os.path.exists(os.path.join(tmp_dir, p))]
    if not missing:
        return {"healthy": True, "regenerated": False, "missing": []}

    logger.warning(
        "Tmp dir %s has %d missing file(s): %s — regenerating",
        tmp_dir,
        len(missing),
        ", ".join(missing[:5]),
    )
    deploy_worker_tmp_contents(
        tmp_dir,
        session_id,
        api_base=api_base,
        cdp_port=cdp_port,
        browser_headless=browser_headless,
        conn=conn,
    )
    return {"healthy": False, "regenerated": True, "missing": missing}


def ensure_brain_tmp_health(
    brain_dir: str,
    api_base: str = "http://127.0.0.1:8093",
    conn=None,
) -> dict:
    """Check if the brain tmp dir has all required files; regenerate if any missing.

    Same manifest-based verification as workers.

    Args:
        brain_dir: Brain's working directory (e.g., /tmp/orchestrator/brain)
        api_base: API base URL
        conn: Optional DB connection for reading skills when regenerating

    Returns:
        {"healthy": bool, "regenerated": bool, "missing": list[str]}
    """
    from orchestrator.agents.deploy import _read_manifest, deploy_brain_tmp_contents

    manifest = _read_manifest(brain_dir)

    if manifest is None:
        logger.warning("Brain tmp dir manifest missing: %s — regenerating", brain_dir)
        deploy_brain_tmp_contents(brain_dir, api_base=api_base, conn=conn)
        return {"healthy": False, "regenerated": True, "missing": ["<manifest>"]}

    missing = [p for p in manifest if not os.path.exists(os.path.join(brain_dir, p))]
    if not missing:
        return {"healthy": True, "regenerated": False, "missing": []}

    logger.warning(
        "Brain tmp dir %s has %d missing file(s): %s — regenerating",
        brain_dir,
        len(missing),
        ", ".join(missing[:5]),
    )
    deploy_brain_tmp_contents(brain_dir, api_base=api_base, conn=conn)
    return {"healthy": False, "regenerated": True, "missing": missing}


# =============================================================================
# High-Level Health Check Orchestration
# =============================================================================


def _recycle_frozen_pane(
    pane_preexisted: bool,
    tmux_sess: str,
    tmux_win: str,
    cwd: str,
    session_name: str,
) -> None:
    """Kill a pre-existing frozen tmux pane and recreate a fresh one.

    When a worker is dead (SSH died for remote, Claude exited for local),
    a pre-existing pane may be frozen — stuck with queued commands that
    never execute.  Killing and recreating gives a clean shell for
    reconnection.

    Skips if the pane was freshly created by the health check (not frozen).
    """
    if not pane_preexisted:
        return
    try:
        kill_window(tmux_sess, tmux_win)
        ensure_window(tmux_sess, tmux_win, cwd=cwd)
        logger.info("Health check: %s killed frozen tmux pane and recreated", session_name)
    except Exception:
        logger.debug(
            "Health check: %s failed to kill/recreate frozen pane",
            session_name,
            exc_info=True,
        )


def _check_rws_pty_health(db, session, tunnel_manager=None) -> dict:
    """Health check for sessions using RWS PTY architecture.

    Checks:
    1. Reverse tunnel (for API callbacks)
    2. RWS daemon PTY status
    3. Fallback: SSH subprocess check for Claude process
    """
    from orchestrator.terminal.remote_worker_server import _SCRIPT_HASH, _server_pool

    # 1. Check reverse tunnel
    tunnel_alive = tunnel_manager.is_alive(session.id) if tunnel_manager else False
    tunnel_reconnected = False
    if not tunnel_alive and tunnel_manager:
        new_pid = tunnel_manager.restart_tunnel(session.id, session.name, session.host)
        if new_pid:
            repo.update_session(db, session.id, tunnel_pid=new_pid)
            tunnel_alive = True
            tunnel_reconnected = True
            logger.info("Health check RWS: %s tunnel restarted (pid=%d)", session.name, new_pid)

    # 2. Check RWS PTY via daemon
    rws = _server_pool.get(session.host)
    # Fast-fail: if the forward tunnel process is dead, skip socket check.
    # poll() returns int (exit code) when dead, None when alive.
    if rws is not None and rws._tunnel_proc is not None and isinstance(rws._tunnel_proc.poll(), int):
        logger.info("Health check RWS: %s forward tunnel dead, skipping socket check", session.name)
        rws = None  # skip to SSH fallback
    if rws is not None:
        try:
            resp = rws.execute({"action": "pty_list"}, timeout=2, connect_timeout=3)
            ptys = resp.get("ptys", [])

            # Look up our PTY by ID first, then fall back to session_id
            # (handles cases where rws_pty_id was cleared but PTY is alive)
            our_pty = None
            if session.rws_pty_id:
                our_pty = next((p for p in ptys if p["pty_id"] == session.rws_pty_id), None)
            if not our_pty:
                our_pty = next(
                    (
                        p
                        for p in ptys
                        if p.get("session_id") == session.id
                        and p.get("alive")
                        and p.get("role") != "interactive-cli"
                    ),
                    None,
                )
                if our_pty and our_pty["pty_id"] != session.rws_pty_id:
                    # Re-attach: found alive PTY by session_id, restore rws_pty_id
                    repo.update_session(db, session.id, rws_pty_id=our_pty["pty_id"])
                    logger.info(
                        "Health check RWS: %s re-attached to PTY %s via session_id",
                        session.name,
                        our_pty["pty_id"],
                    )

            if our_pty and our_pty["alive"]:
                # PTY alive — all good
                # Check for work_dir detection
                if not session.work_dir:
                    from orchestrator.api.routes.files import _detect_remote_work_dir

                    detected = _detect_remote_work_dir(session.host, session.id)
                    if detected:
                        repo.update_session(db, session.id, work_dir=detected)

                # Ensure local+remote tmp dirs are healthy
                tmp_dir = f"/tmp/orchestrator/workers/{session.name}"
                api_base = f"http://127.0.0.1:{DEFAULT_API_PORT}"
                try:
                    tmp_result = ensure_tmp_dir_health(
                        tmp_dir,
                        session.id,
                        api_base=api_base,
                        cdp_port=9222,
                        browser_headless=True,
                        conn=db,
                    )
                    if tmp_result.get("regenerated"):
                        from orchestrator.session.reconnect import _copy_configs_to_remote

                        remote_tmp_dir = f"/tmp/orchestrator/workers/{session.name}"
                        try:
                            _copy_configs_to_remote(
                                session.host, tmp_dir, remote_tmp_dir, session.name
                            )
                        except Exception:
                            logger.warning(
                                "Health check RWS: %s failed to re-push configs",
                                session.name,
                                exc_info=True,
                            )
                except Exception:
                    logger.debug(
                        "Health check RWS: %s tmp dir check failed",
                        session.name,
                        exc_info=True,
                    )

                # Check RWS daemon version
                try:
                    info = rws.execute({"action": "server_info"}, timeout=2, connect_timeout=3)
                    daemon_version = info.get("version", "")
                    if daemon_version != _SCRIPT_HASH:
                        logger.warning(
                            "Health check RWS: %s daemon outdated, will upgrade on next reconnect",
                            session.name,
                        )
                except Exception:
                    pass

                current_status = session.status
                if current_status in ("error", "disconnected", "connecting"):
                    repo.update_session(db, session.id, status="waiting")
                    current_status = "waiting"

                result = {
                    "alive": True,
                    "status": current_status,
                    "reason": "RWS PTY alive",
                    "tunnel_alive": tunnel_alive,
                }
                if tunnel_reconnected:
                    result["tunnel_reconnected"] = True
                return result

            # PTY dead or gone
            logger.info(
                "Health check RWS: %s PTY %s is dead/gone, marking disconnected",
                session.name,
                session.rws_pty_id,
            )
            repo.update_session(db, session.id, status="disconnected", rws_pty_id=None)
            return {
                "alive": False,
                "status": "disconnected",
                "reason": "RWS PTY dead",
                "needs_reconnect": True,
            }
        except Exception:
            logger.debug(
                "Health check RWS: %s could not query daemon",
                session.name,
                exc_info=True,
            )

    # 3. Fallback: SSH subprocess check (Claude process alive?)
    try:
        check_cmd = (
            "ps aux | grep -v grep | grep -E 'claude (-r|--|--settings)'"
            f" | grep -q {shlex.quote(session.id)} && echo ALIVE || echo DEAD"
        )
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes", session.host, check_cmd],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if "ALIVE" in result.stdout:
            if session.status in ("disconnected", "error"):
                repo.update_session(db, session.id, status="waiting")
            return {
                "alive": True,
                "status": "waiting",
                "reason": "Claude alive (SSH fallback)",
                "tunnel_alive": tunnel_alive,
            }
    except Exception:
        logger.debug(
            "Health check RWS: %s SSH fallback failed",
            session.name,
            exc_info=True,
        )

    # Don't clear rws_pty_id — the PTY may still be alive on the remote host,
    # we just can't reach it (e.g. forward tunnel is down).  The reconnect flow
    # will re-establish the tunnel and check PTY status.
    repo.update_session(db, session.id, status="disconnected")
    return {
        "alive": False,
        "status": "disconnected",
        "reason": "RWS unavailable and SSH fallback failed",
        "needs_reconnect": True,
    }


def check_and_update_worker_health(db, session, tunnel_manager=None) -> dict:
    """Check a single worker's health and update its DB status accordingly.

    Replaces the inline decision tree previously in the ``health_check_session``
    route.  Returns a dict suitable for JSON response.

    Args:
        db: SQLite connection.
        session: Session model object.
        tunnel_manager: ReverseTunnelManager (for remote tunnel checks).

    Returns:
        {"alive": bool, "status": str, "reason": str, ...}
    """
    tmux_sess, tmux_win = tmux_target(session.name)

    worker_tmp_dir = f"/tmp/orchestrator/workers/{session.name}"

    # All remote sessions use RWS PTY health check (daemon + SSH fallback).
    # The check handles rws_pty_id being None by searching for PTY via session_id.
    if is_remote_host(session.host):
        return _check_rws_pty_health(db, session, tunnel_manager)

    # Check whether the pane exists.  If it does and the worker turns out
    # dead, the pane is likely frozen (dead SSH, queued commands) and needs
    # to be killed and recreated.  If the pane is missing, the worker is
    # clearly not running — skip straight to marking disconnected instead
    # of creating an empty shell that would just be left behind.
    try:
        pane_preexisted = window_exists(tmux_sess, tmux_win)
    except Exception:
        pane_preexisted = False
        logger.debug(
            "Health check: %s failed to check tmux window",
            session.name,
            exc_info=True,
        )

    if not pane_preexisted:
        if session.status != "disconnected":
            repo.update_session(db, session.id, status="disconnected")
            logger.info("Health check: %s has no tmux window, marking disconnected", session.name)
        return {
            "alive": False,
            "status": "disconnected",
            "reason": "no tmux window",
            "needs_reconnect": True,
        }

    # Local worker: use pane-based process tree detection (primary)
    # with ps aux fallback checking both orchestrator and Claude IDs.
    # After /clear or /compact, Claude's internal session ID changes
    # but the process command line retains the original --session-id,
    # so relying solely on claude_session_id causes false disconnects.
    alive, reason = check_claude_running_local(
        session.id,
        session.claude_session_id,
        tmux_sess,
        tmux_win,
    )
    if not alive:
        _recycle_frozen_pane(pane_preexisted, tmux_sess, tmux_win, worker_tmp_dir, session.name)

        if session.status != "disconnected":
            repo.update_session(db, session.id, status="disconnected")
            logger.info("Health check: %s marked as disconnected (%s)", session.name, reason)
        return {
            "alive": False,
            "status": "disconnected",
            "reason": reason,
            "needs_reconnect": True,
        }

    # --- Local alive: ensure tmp dir is healthy ---
    tmp_dir = f"/tmp/orchestrator/workers/{session.name}"
    try:
        tmp_result = ensure_tmp_dir_health(
            tmp_dir,
            session.id,
            api_base=f"http://127.0.0.1:{DEFAULT_API_PORT}",
            browser_headless=False,
            conn=db,
        )
        if tmp_result.get("regenerated"):
            logger.warning("Health check: %s regenerated local tmp dir", session.name)
    except Exception:
        logger.debug(
            "Health check: %s tmp dir check failed",
            session.name,
            exc_info=True,
        )

    if session.status in ("disconnected", "error"):
        repo.update_session(db, session.id, status="waiting")
        logger.info("Health check: %s recovered from %s to waiting", session.name, session.status)
        return {"alive": True, "status": "waiting", "reason": reason}
    return {"alive": True, "status": session.status, "reason": reason}


def check_all_workers_health(
    db,
    sessions,
    db_path: str | None = None,
    api_port: int = 8093,
    tunnel_manager=None,
) -> dict:
    """Check health of all worker sessions and auto-reconnect eligible ones.

    Replaces the inline logic previously in the ``health_check_all_sessions``
    route.  Iterates over all workers, updates statuses, and triggers
    reconnection for workers that have ``auto_reconnect`` enabled.

    Args:
        db: SQLite connection.
        sessions: List of Session model objects to check.
        db_path: DB file path (for background reconnect threads).
        api_port: Orchestrator API port.
        tunnel_manager: ReverseTunnelManager instance.

    Returns:
        {"checked": int, "disconnected": [...],
         "alive": [...], "skipped_active": [...],
         "auto_reconnected": [...]}
    """
    from datetime import datetime

    from orchestrator.session.reconnect import trigger_reconnect

    results = {
        "checked": 0,
        "disconnected": [],
        "alive": [],
        "skipped_active": [],
        "auto_reconnected": [],
        "deferred": [],
    }

    auto_reconnect_candidates = []

    # Separate sessions into groups: those needing health check vs skipped
    to_check = []  # (session, is_disconnected_precheck) tuples
    for s in sessions:
        if s.status == "connecting":
            # Check if stuck connecting for too long (>10 min)
            if s.last_status_changed_at:
                try:
                    ts = s.last_status_changed_at.replace("Z", "+00:00")
                    dt = datetime.fromisoformat(ts)
                    if dt.tzinfo is None:
                        dt = dt.astimezone()
                    elapsed = (datetime.now(UTC) - dt.astimezone(UTC)).total_seconds()
                    if elapsed > 600:  # 10 minutes
                        repo.update_session(db, s.id, status="disconnected")
                        logger.warning(
                            "Health check: %s stuck in connecting for %dm, marking disconnected",
                            s.name,
                            int(elapsed // 60),
                        )
                        results["disconnected"].append(s.name)
                        if s.auto_reconnect:
                            auto_reconnect_candidates.append(s)
                except Exception:
                    pass
            continue

        # Check circuit breaker for remote hosts
        host = getattr(s, "host", "localhost")
        if is_remote_host(host) and _host_breaker.should_skip(host):
            results["deferred"].append(s.name)
            continue

        if s.status == "disconnected":
            to_check.append((s, True))
        else:
            to_check.append((s, False))

    # Check workers in parallel (max 4 threads for remote, inline for local)
    def _check_one(session, is_precheck):
        """Run health check for a single session with its own DB connection."""
        from orchestrator.state.db import get_connection

        # Use a per-thread DB connection for thread safety (SQLite limitation)
        conn = get_connection(db_path) if db_path else db
        try:
            result = check_and_update_worker_health(conn, session, tunnel_manager)
            host = getattr(session, "host", "localhost")
            if is_remote_host(host):
                if result.get("alive"):
                    _host_breaker.record_success(host)
                else:
                    _host_breaker.record_failure(host)
            return session, result, is_precheck
        except Exception as e:
            host = getattr(session, "host", "localhost")
            if is_remote_host(host):
                _host_breaker.record_failure(host)
            return session, {"alive": True, "status": session.status, "reason": str(e)}, is_precheck
        finally:
            if db_path and conn is not db:
                conn.close()

    def _tally_result(s, result, is_precheck):
        """Tally a single health-check result into the results dict."""
        if is_precheck:
            if result.get("alive"):
                results["checked"] += 1
                results["alive"].append(s.name)
            else:
                if s.auto_reconnect:
                    auto_reconnect_candidates.append(s)
                else:
                    results["disconnected"].append(s.name)
        else:
            results["checked"] += 1
            if result.get("alive"):
                results["alive"].append(s.name)
            else:
                results["disconnected"].append(s.name)
                if s.auto_reconnect:
                    auto_reconnect_candidates.append(s)

    # Host-level deduplication: group remote sessions by host, probe one per host
    local_checks = []  # (session, is_precheck)
    host_groups: dict[str, list[tuple]] = {}  # host -> [(session, is_precheck), ...]
    for s, pre in to_check:
        host = getattr(s, "host", "localhost")
        if is_remote_host(host):
            host_groups.setdefault(host, []).append((s, pre))
        else:
            local_checks.append((s, pre))

    # Pick one probe per remote host; remaining are peers
    probe_checks = []  # (session, is_precheck) — one per host
    host_peers: dict[str, list[tuple]] = {}  # host -> remaining sessions
    for host, group in host_groups.items():
        probe_checks.append(group[0])
        if len(group) > 1:
            host_peers[host] = group[1:]

    first_pass = local_checks + probe_checks
    max_workers = min(4, len(first_pass)) if first_pass else 1

    # Track probe results per host for dedup
    host_probe_results: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_check_one, s, pre): (s, pre) for s, pre in first_pass}
        for future in as_completed(futures, timeout=15):
            try:
                s, result, is_precheck = future.result(timeout=12)
            except Exception:
                s, is_precheck = futures[future]
                result = {"alive": True, "status": s.status}

            _tally_result(s, result, is_precheck)

            # Record probe result for host dedup
            host = getattr(s, "host", "localhost")
            if is_remote_host(host) and host in host_peers:
                host_probe_results[host] = result

        # Second pass: for hosts where probe succeeded, check peers individually.
        # For hosts where probe failed, apply failure to all peers (skip checks).
        second_pass = []
        for host, peers in host_peers.items():
            probe_result = host_probe_results.get(host)
            if probe_result and not probe_result.get("alive"):
                # Host unreachable — apply failure to all peers without checking
                for s, pre in peers:
                    from orchestrator.state.db import get_connection

                    conn = get_connection(db_path) if db_path else db
                    try:
                        repo.update_session(conn, s.id, status="disconnected")
                    finally:
                        if db_path and conn is not db:
                            conn.close()
                    _host_breaker.record_failure(host)
                    fail_result = {
                        "alive": False,
                        "status": "disconnected",
                        "reason": f"Host {host} unreachable (dedup from probe)",
                    }
                    _tally_result(s, fail_result, pre)
            else:
                # Host reachable (or probe timed out) — check peers individually
                second_pass.extend(peers)

        if second_pass:
            peer_futures = {executor.submit(_check_one, s, pre): (s, pre) for s, pre in second_pass}
            for future in as_completed(peer_futures, timeout=15):
                try:
                    s, result, is_precheck = future.result(timeout=12)
                except Exception:
                    s, is_precheck = peer_futures[future]
                    result = {"alive": True, "status": s.status}
                _tally_result(s, result, is_precheck)

    # Auto-reconnect eligible workers
    for s in auto_reconnect_candidates:
        if is_user_active(s.id):
            logger.info(
                "Auto-reconnect: deferring %s — user active in pane",
                s.name,
            )
            results["deferred"].append(s.name)
            continue
        if _reconnect_backoff.should_skip(s.id):
            logger.debug("Auto-reconnect: backoff active for %s, skipping", s.name)
            continue
        # Re-read session from DB — background health-check threads may have
        # updated fields (rws_pty_id, status) since we built the candidate list.
        fresh = repo.get_session(db, s.id)
        if fresh is None or fresh.status not in ("disconnected", "error"):
            continue
        try:
            logger.info("Auto-reconnect: triggering reconnect for %s", s.name)
            trigger_reconnect(
                fresh,
                db,
                db_path=db_path,
                api_port=api_port,
                tunnel_manager=tunnel_manager,
            )
            results["auto_reconnected"].append(s.name)
        except Exception as e:
            logger.warning("Auto-reconnect: failed to start reconnect for %s: %s", s.name, e)

    return results


async def check_all_workers_health_async(
    sessions,
    db_path: str | None = None,
    api_port: int = 8093,
    tunnel_manager=None,
) -> dict:
    """Non-blocking async wrapper around check_all_workers_health.

    Uses an in-flight guard to reject concurrent calls, and offloads
    the blocking health check to the default executor so it doesn't
    block the uvicorn event loop.

    The executor thread creates its own DB connection from ``db_path``
    (same pattern as ``_check_one``).

    Returns:
        Health check results dict, or {"status": "in_progress", ...}
        if another health check is already running.
    """
    if not _health_check_all_lock.acquire(blocking=False):
        return {"status": "in_progress", "message": "Health check already running"}
    try:

        def _run():
            from orchestrator.state.db import get_connection

            conn = get_connection(db_path) if db_path else None
            if conn is None:
                raise RuntimeError("db_path required for async health check")
            try:
                return check_all_workers_health(
                    conn,
                    sessions,
                    db_path=db_path,
                    api_port=api_port,
                    tunnel_manager=tunnel_manager,
                )
            finally:
                conn.close()

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _run)
    finally:
        _health_check_all_lock.release()
