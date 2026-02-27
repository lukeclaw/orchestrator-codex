"""Health check utilities for session management.

Functions to check the status of Claude processes, screen sessions,
SSH tunnels, and SSH connections for both local and rdev workers.
"""

import logging
import os
import signal
import subprocess
import time
from datetime import UTC

from orchestrator.api.ws_terminal import is_user_active
from orchestrator.state.repositories import sessions as repo
from orchestrator.terminal.manager import tmux_target
from orchestrator.terminal.ssh import is_remote_host

logger = logging.getLogger(__name__)

# Default port used by the reverse tunnel for API access
DEFAULT_API_PORT = 8093


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


def get_screen_session_name(session_id: str) -> str:
    """Get the screen session name for a worker session."""
    return f"claude-{session_id}"


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


def check_screen_and_claude_remote(
    host: str, session_id: str, tmux_sess: str = None, tmux_win: str = None
) -> tuple[str, str]:
    """Check screen session and Claude process status on a remote host.

    Uses subprocess SSH (fresh connection) to check status. Does NOT use tmux send-keys
    because that would type commands into Claude if it's running.

    Also checks the worker tmux window to verify the SSH connection is actually alive.

    Args:
        host: rdev host (e.g., "user/rdev-vm")
        session_id: Session ID to check for
        tmux_sess: tmux session name (used for SSH alive check)
        tmux_win: tmux window name (used for SSH alive check)

    Returns:
        (status: str, reason: str) where status is one of:
        - "alive": Screen exists and Claude is running AND SSH connection alive
        - "screen_only": Screen exists but Claude not running
        - "screen_detached": SSH connection failed but screen may still be running
        - "dead": No screen session found OR SSH connection dead
    """
    screen_name = get_screen_session_name(session_id)

    # First check if the worker SSH session is still connected by checking tmux window content
    # This catches the case where the user's SSH session has died but
    # the remote screen/Claude might still be running
    if tmux_sess and tmux_win:
        ssh_alive = check_worker_ssh_alive(tmux_sess, tmux_win, host)
        if not ssh_alive:
            logger.info(
                "Health check: Worker SSH appears disconnected for %s - marking as dead", host
            )
            return "dead", f"Worker SSH session appears disconnected (not on rdev host '{host}')"

    # Check remote screen/Claude status via subprocess SSH
    # (separate from worker tmux window to avoid interfering with Claude)
    try:
        # Note: Check for 'claude -r' or 'claude --' to avoid matching screen session names
        # which contain 'claude-<session_id>'. The actual Claude CLI is invoked with flags.
        #
        # Screen detection uses two methods:
        #   1. `screen -ls` (socket-based — can fail if SCREENDIR differs between
        #      interactive and BatchMode SSH sessions)
        #   2. `ps aux | grep "screen .* <name>"` (process-based — always works,
        #      matches both `screen -S` from initial creation and `screen -rd` from reconnect)
        # Either match counts as "screen exists".
        #
        # NOTE: The ps grep uses case-insensitive match (-i) because GNU Screen
        # renames the session manager process to uppercase "SCREEN".  It also
        # drops the space before {screen_name} and uses `.*` to bridge both
        # `screen -S <name>` (space-separated) and `screen -rd <pid>.<name>`
        # (dot-separated) forms.
        check_cmd = (
            f"screen -ls 2>/dev/null | grep -q '{screen_name}' && echo 'SCREEN_EXISTS' || echo 'NO_SCREEN'; "
            f"ps aux | grep -v grep | grep -qi '[s]creen.*{screen_name}' && echo 'SCREEN_PS_EXISTS' || echo 'NO_SCREEN_PS'; "
            f"ps aux | grep -v grep | grep -E 'claude (-r|--|--settings)' | grep -q '{session_id}' && echo 'CLAUDE_RUNNING' || echo 'NO_CLAUDE'"
        )

        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, check_cmd],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0 and "Permission denied" in result.stderr:
            return (
                "screen_detached",
                f"SSH auth failed - screen may still be running: {result.stderr.strip()}",
            )

        if result.returncode != 0 and (
            "Connection refused" in result.stderr or "Connection timed out" in result.stderr
        ):
            return (
                "screen_detached",
                f"SSH connection failed - screen may still be running: {result.stderr.strip()}",
            )

        # Catch-all for other SSH transport failures (host key errors, DNS, etc.)
        # If SSH itself failed (exit 255) and stdout is empty, it's a connection issue.
        if result.returncode == 255 and not result.stdout.strip():
            return (
                "screen_detached",
                f"SSH connection failed - screen may still be running: {result.stderr.strip()[:200]}",
            )

        output = result.stdout
        stderr = result.stderr
        screen_exists_ls = "SCREEN_EXISTS" in output
        screen_exists_ps = "SCREEN_PS_EXISTS" in output
        screen_exists = screen_exists_ls or screen_exists_ps
        claude_running = "CLAUDE_RUNNING" in output

        # Debug logging to diagnose false negatives
        logger.debug(
            "SSH health check for %s (screen=%s): returncode=%d, screen_ls=%s, screen_ps=%s, claude=%s, stdout=%r, stderr=%r",
            host,
            screen_name,
            result.returncode,
            screen_exists_ls,
            screen_exists_ps,
            claude_running,
            output[:200],
            stderr[:100],
        )
        if screen_exists_ps and not screen_exists_ls:
            logger.info(
                "SSH health check for %s: screen -ls missed session '%s' but ps found it "
                "(likely SCREENDIR mismatch between interactive and BatchMode SSH)",
                host,
                screen_name,
            )

        if screen_exists and claude_running:
            return "alive", "Screen session exists and Claude is running"
        elif screen_exists and not claude_running:
            return "screen_only", "Screen session exists but Claude not running"
        elif not screen_exists and claude_running:
            # Claude is running but screen session not found. This happens when the
            # screen parent process died (OOM, system cleanup) but Claude survived as
            # an orphaned process with its PTY intact. Treat as alive since Claude is
            # still functional.
            logger.warning(
                "SSH health check for %s: Claude running but screen not found "
                "(screen_name=%s) — likely orphaned after screen parent died. "
                "stdout=%r, stderr=%r",
                host,
                screen_name,
                output[:200],
                stderr[:100],
            )
            return (
                "alive",
                f"Claude is running without screen (screen session '{screen_name}' not found — likely orphaned)",
            )
        else:
            # Log at warning level when marking as dead - helps diagnose false negatives
            logger.warning(
                "SSH health check marking %s as DEAD: screen_name=%s, stdout=%r, stderr=%r",
                host,
                screen_name,
                output,
                stderr,
            )
            return "dead", f"No screen session found (looked for '{screen_name}')"

    except subprocess.TimeoutExpired:
        return "screen_detached", "SSH connection timed out - screen may still be running"
    except Exception as e:
        logger.warning("Health check SSH command failed: %s", e)
        return "screen_detached", f"Health check error: {e}"


def check_claude_process_remote(host: str, session_id: str) -> tuple[bool, str]:
    """Check if Claude Code with given session_id is running on a remote host via SSH.

    This is a simplified wrapper around check_screen_and_claude_remote that returns
    a boolean alive status.

    Args:
        host: Remote host (rdev or generic SSH)
        session_id: Session ID to check for

    Returns:
        (alive: bool, reason: str)
    """
    status, reason = check_screen_and_claude_remote(host, session_id)

    if status == "alive":
        return True, reason
    elif status == "screen_detached":
        # SSH failed but screen might be running - report as alive to avoid false positives
        return True, reason
    else:
        return False, reason


# Backward-compat aliases
check_screen_and_claude_rdev = check_screen_and_claude_remote
check_claude_process_rdev = check_claude_process_remote


# =============================================================================
# High-Level Health Check Orchestration
# =============================================================================


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

    if is_remote_host(session.host):
        screen_status, reason = check_screen_and_claude_remote(
            session.host,
            session.id,
            tmux_sess,
            tmux_win,
        )

        tunnel_alive = tunnel_manager.is_alive(session.id) if tunnel_manager else False

        if screen_status == "alive":
            # --- Ensure tunnel is alive ---
            tunnel_reconnected = False
            if not tunnel_alive:
                logger.info(
                    "Health check: %s has Claude running but tunnel dead, restarting tunnel",
                    session.name,
                )
                if tunnel_manager:
                    new_pid = tunnel_manager.restart_tunnel(session.id, session.name, session.host)
                    if new_pid:
                        repo.update_session(db, session.id, tunnel_pid=new_pid)
                        logger.info(
                            "Health check: %s tunnel restarted (pid=%d)", session.name, new_pid
                        )
                        tunnel_alive = True
                        tunnel_reconnected = True
                        # Don't return — fall through to TUI check

                if not tunnel_alive:
                    # Tunnel dead and could not be restarted
                    tunnel_failures = 0
                    tunnel_error = None
                    if tunnel_manager:
                        tunnel_failures, tunnel_error = tunnel_manager.get_failure_info(session.id)
                    error_detail = f" ({tunnel_error})" if tunnel_error else ""
                    reason = f"{reason}, but tunnel is dead and restart failed{error_detail}"
                    if session.status not in ("screen_detached", "connecting"):
                        repo.update_session(db, session.id, status="screen_detached")
                    return {
                        "alive": False,
                        "status": "screen_detached",
                        "reason": reason,
                        "screen_status": screen_status,
                        "tunnel_alive": False,
                        "needs_reconnect": True,
                        "tunnel_failures": tunnel_failures,
                        "tunnel_error": tunnel_error,
                    }

            # --- Tunnel alive. Check if pane is attached to screen ---
            # On remote workers, alternate_on=1 means inside GNU Screen,
            # alternate_on=0 means at shell prompt (screen detached).
            tui_active = check_tui_running_in_pane(tmux_sess, tmux_win)
            if not tui_active:
                if session.status not in ("screen_detached", "connecting"):
                    repo.update_session(db, session.id, status="screen_detached")
                    logger.info(
                        "Health check: %s alive but pane not attached to screen, "
                        "marking screen_detached for auto-reattach",
                        session.name,
                    )
                return {
                    "alive": False,
                    "status": "screen_detached",
                    "reason": f"{reason} — pane not attached to screen",
                    "screen_status": screen_status,
                    "tunnel_alive": tunnel_alive,
                    "needs_reconnect": True,
                    "tunnel_reconnected": tunnel_reconnected,
                }

            # --- All good: screen + Claude alive, tunnel alive, pane attached ---
            if session.status in ("screen_detached", "error", "disconnected"):
                repo.update_session(db, session.id, status="waiting")
                logger.info(
                    "Health check: %s recovered from %s to waiting", session.name, session.status
                )
            result = {
                "alive": True,
                "status": session.status,
                "reason": reason,
                "screen_status": screen_status,
                "tunnel_alive": True,
            }
            if tunnel_reconnected:
                result["tunnel_reconnected"] = True
                result["status"] = "waiting"
            return result

        elif screen_status == "screen_detached":
            if session.status not in ("screen_detached", "connecting"):
                repo.update_session(db, session.id, status="screen_detached")
                logger.info("Health check: %s marked as screen_detached (%s)", session.name, reason)
            return {
                "alive": False,
                "status": "screen_detached",
                "reason": reason,
                "screen_status": screen_status,
                "needs_reconnect": True,
            }

        elif screen_status == "screen_only":
            if session.status != "error":
                repo.update_session(db, session.id, status="error")
                logger.info(
                    "Health check: %s marked as error - Claude crashed in screen (%s)",
                    session.name,
                    reason,
                )
            return {
                "alive": False,
                "status": "error",
                "reason": reason,
                "screen_status": screen_status,
                "needs_reconnect": True,
            }

        else:  # dead
            if session.status != "disconnected":
                repo.update_session(db, session.id, status="disconnected")
                logger.info("Health check: %s marked as disconnected (%s)", session.name, reason)
            return {
                "alive": False,
                "status": "disconnected",
                "reason": reason,
                "screen_status": screen_status,
                "needs_reconnect": True,
            }
    else:
        # Use claude_session_id when available — after /clear or /compact,
        # Claude may be running with a different session ID than session.id.
        local_check_id = session.claude_session_id or session.id
        alive, reason = check_claude_process_local(local_check_id)
        if not alive:
            if session.status != "disconnected":
                repo.update_session(db, session.id, status="disconnected")
                logger.info("Health check: %s marked as disconnected (%s)", session.name, reason)
            return {
                "alive": False,
                "status": "disconnected",
                "reason": reason,
                "needs_reconnect": True,
            }
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
        {"checked": int, "disconnected": [...], "screen_detached": [...],
         "error": [...], "alive": [...], "skipped_active": [...],
         "auto_reconnected": [...]}
    """
    from datetime import datetime

    from orchestrator.session.reconnect import trigger_reconnect

    results = {
        "checked": 0,
        "disconnected": [],
        "screen_detached": [],
        "error": [],
        "alive": [],
        "skipped_active": [],
        "auto_reconnected": [],
        "deferred": [],
    }

    auto_reconnect_candidates = []

    for s in sessions:
        if s.status == "disconnected":
            if s.auto_reconnect:
                auto_reconnect_candidates.append(s)
            continue
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

        results["checked"] += 1

        try:
            result = check_and_update_worker_health(db, s, tunnel_manager)

            if result.get("alive"):
                results["alive"].append(s.name)
            elif result["status"] == "screen_detached":
                results["screen_detached"].append(s.name)
                if s.auto_reconnect:
                    auto_reconnect_candidates.append(s)
            elif result["status"] == "error":
                results["error"].append(s.name)
                if s.auto_reconnect:
                    auto_reconnect_candidates.append(s)
            else:  # disconnected
                results["disconnected"].append(s.name)
                if s.auto_reconnect:
                    auto_reconnect_candidates.append(s)
        except Exception as e:
            logger.warning("Health check failed for %s: %s", s.name, e)
            results["alive"].append(s.name)

    # Auto-reconnect eligible workers
    for s in auto_reconnect_candidates:
        if is_user_active(s.id):
            logger.info(
                "Auto-reconnect: deferring %s — user active in pane",
                s.name,
            )
            results["deferred"].append(s.name)
            continue
        try:
            logger.info("Auto-reconnect: triggering reconnect for %s", s.name)
            trigger_reconnect(
                s,
                db,
                db_path=db_path,
                api_port=api_port,
                tunnel_manager=tunnel_manager,
            )
            results["auto_reconnected"].append(s.name)
        except Exception as e:
            logger.warning("Auto-reconnect: failed to start reconnect for %s: %s", s.name, e)

    return results
