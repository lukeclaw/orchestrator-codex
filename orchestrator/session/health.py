"""Health check utilities for session management.

Functions to check the status of Claude processes, screen sessions,
SSH tunnels, and SSH connections for both local and rdev workers.
"""

import logging
import os
import signal
import subprocess
import time

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
            if "ssh" in line and "-N" in line and "-R" in line and host in line and "grep" not in line:
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

    Claude Code (built on Ink/React) and GNU Screen both use the alternate
    screen buffer, so ``#{alternate_on}`` == "1" means a TUI is active and
    we must not send shell commands to the pane.
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


def probe_tunnel_connectivity(host: str, remote_port: int = DEFAULT_API_PORT, timeout: int = 8) -> bool:
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
                host, remote_port, http_code, result.stderr.strip()[:100],
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
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        # Look for claude process with our session_id
        for line in result.stdout.split('\n'):
            if 'claude' in line.lower() and session_id in line and 'grep' not in line:
                logger.debug("Found Claude process for session %s: %s", session_id, line[:100])
                return True, f"Claude process running"
        
        return False, f"No Claude process found for session {session_id}"
    except subprocess.TimeoutExpired:
        return True, "Health check timed out"
    except Exception as e:
        logger.warning("Health check ps command failed: %s", e)
        return True, f"Health check error: {e}"


def check_screen_and_claude_remote(
    host: str,
    session_id: str,
    tmux_sess: str = None,
    tmux_win: str = None
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
            logger.info("Health check: Worker SSH appears disconnected for %s - marking as dead", host)
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
        #   2. `ps aux | grep "screen -S <name>"` (process-based — always works)
        # Either match counts as "screen exists".
        check_cmd = (
            f"screen -ls 2>/dev/null | grep -q '{screen_name}' && echo 'SCREEN_EXISTS' || echo 'NO_SCREEN'; "
            f"ps aux | grep -v grep | grep -q '[s]creen -S {screen_name}' && echo 'SCREEN_PS_EXISTS' || echo 'NO_SCREEN_PS'; "
            f"ps aux | grep -v grep | grep -E 'claude (-r|--|--settings)' | grep -q '{session_id}' && echo 'CLAUDE_RUNNING' || echo 'NO_CLAUDE'"
        )
        
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, check_cmd],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0 and "Permission denied" in result.stderr:
            return "screen_detached", f"SSH auth failed - screen may still be running: {result.stderr.strip()}"

        if result.returncode != 0 and ("Connection refused" in result.stderr or "Connection timed out" in result.stderr):
            return "screen_detached", f"SSH connection failed - screen may still be running: {result.stderr.strip()}"

        # Catch-all for other SSH transport failures (host key errors, DNS, etc.)
        # If SSH itself failed (exit 255) and stdout is empty, it's a connection issue.
        if result.returncode == 255 and not result.stdout.strip():
            return "screen_detached", f"SSH connection failed - screen may still be running: {result.stderr.strip()[:200]}"
        
        output = result.stdout
        stderr = result.stderr
        screen_exists_ls = "SCREEN_EXISTS" in output
        screen_exists_ps = "SCREEN_PS_EXISTS" in output
        screen_exists = screen_exists_ls or screen_exists_ps
        claude_running = "CLAUDE_RUNNING" in output

        # Debug logging to diagnose false negatives
        logger.debug(
            "SSH health check for %s (screen=%s): returncode=%d, screen_ls=%s, screen_ps=%s, claude=%s, stdout=%r, stderr=%r",
            host, screen_name, result.returncode, screen_exists_ls, screen_exists_ps, claude_running, output[:200], stderr[:100]
        )
        if screen_exists_ps and not screen_exists_ls:
            logger.info(
                "SSH health check for %s: screen -ls missed session '%s' but ps found it "
                "(likely SCREENDIR mismatch between interactive and BatchMode SSH)",
                host, screen_name,
            )
        
        if screen_exists and claude_running:
            return "alive", "Screen session exists and Claude is running"
        elif screen_exists and not claude_running:
            return "screen_only", "Screen session exists but Claude not running"
        else:
            # Log at warning level when marking as dead - helps diagnose false negatives
            logger.warning(
                "SSH health check marking %s as DEAD: screen_name=%s, stdout=%r, stderr=%r",
                host, screen_name, output, stderr
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
