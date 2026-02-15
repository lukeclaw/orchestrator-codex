"""Health check utilities for session management.

Functions to check the status of Claude processes, screen sessions,
SSH tunnels, and SSH connections for both local and rdev workers.
"""

import logging
import os
import signal
import subprocess
import time
from typing import Optional

from orchestrator.terminal.manager import capture_output

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


def check_worker_ssh_alive(tmux_sess: str, tmux_win: str, host: str) -> bool:
    """Check if the worker SSH session is still connected to rdev.
    
    This verifies the SSH connection by checking for a running `rdev ssh` process
    that contains the host name and --non-tmux flag. This is more reliable than
    checking tmux window content which may have stale output in scrollback.
    
    Args:
        tmux_sess: tmux session name (unused, kept for API compatibility)
        tmux_win: tmux window name (unused, kept for API compatibility)
        host: The rdev host (e.g., "subs-mt/sleepy-franklin")
        
    Returns:
        True if rdev SSH process is running, False otherwise
    """
    try:
        # Extract rdev VM name from host (e.g., "subs-mt/sleepy-franklin" -> "sleepy-franklin")
        rdev_vm_name = host.split('/')[-1] if '/' in host else host
        
        # Check for a process with "rdev", the host name, and "--non-tmux" all in command line
        # This is the actual `rdev ssh <host> --non-tmux` process
        result = subprocess.run(
            ["bash", "-c", f"ps aux | grep -E 'rdev.*{rdev_vm_name}.*--non-tmux' | grep -v grep"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0 and result.stdout.strip():
            logger.info("Worker SSH check: found rdev process for %s", rdev_vm_name)
            logger.debug("Worker SSH check process: %s", result.stdout.strip()[:200])
            return True
        
        logger.info("Worker SSH check: no rdev process found for %s", rdev_vm_name)
        return False
        
    except subprocess.TimeoutExpired:
        logger.warning("Worker SSH check: timeout checking for rdev process")
        return False
    except Exception as e:
        logger.warning("Worker SSH check error for %s: %s", host, e)
        return False


def probe_tunnel_connectivity(host: str, remote_port: int = DEFAULT_API_PORT, timeout: int = 8) -> bool:
    """Actively test if the reverse tunnel works by SSHing to host and curling the tunneled port.

    This provides a definitive answer about tunnel health by testing actual connectivity,
    unlike check_tunnel_alive() which only inspects tmux output heuristics.

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


def check_tunnel_alive(
    tmux_sess: str,
    tunnel_win: str,
    host: Optional[str] = None,
    remote_port: int = DEFAULT_API_PORT,
) -> bool:
    """Check if the tunnel window has an active SSH tunnel running.

    Two-stage check:
    1. Fast check: inspect tmux window output for error indicators or shell prompt.
       If errors/prompt found, tunnel is definitely dead → return False.
    2. If the fast check is inconclusive AND host is provided, do an active probe
       by SSHing to the remote host and curling through the tunnel.

    Args:
        tmux_sess: tmux session name
        tunnel_win: tmux window name for the tunnel
        host: Optional rdev host for active probing (e.g., "user/rdev-vm")
        remote_port: Port used by the reverse tunnel (default 8093)

    Returns:
        True if tunnel appears alive, False otherwise
    """
    try:
        output = capture_output(tmux_sess, tunnel_win, lines=10)
        if not output:
            logger.info("Tunnel check: no output from window - assuming dead")
            return False

        output_lower = output.lower()
        logger.debug("Tunnel check output: %s", output[:200])

        # Check for common SSH failure indicators
        error_indicators = [
            "Connection closed",
            "Connection refused",
            "Connection timed out",
            "Connection reset",
            "broken pipe",
            "Host key verification failed",
            "Permission denied",
            "Could not resolve hostname",
            "Network is unreachable",
            # Host key changed - SSH connects but disables port forwarding
            "REMOTE HOST IDENTIFICATION HAS CHANGED",
            "Port forwarding is disabled",
        ]
        for indicator in error_indicators:
            if indicator.lower() in output_lower:
                logger.info("Tunnel check: found error indicator '%s'", indicator)
                return False

        # Check for shell prompt - indicates tunnel command has exited
        lines = output.strip().split('\n')
        last_line = lines[-1].strip() if lines else ""

        # Shell prompt patterns (tunnel exited, back to shell)
        shell_prompt_indicators = ['$ ', '% ', '> ', 'bash-', '# ']
        for prompt in shell_prompt_indicators:
            if last_line.endswith(prompt.strip()) or prompt in last_line:
                # Check if it's just a shell prompt (tunnel exited)
                # vs ssh command still running (which wouldn't show prompt)
                if not ('ssh' in output_lower and '-L' in output):
                    logger.info("Tunnel check: shell prompt detected, tunnel likely dead: '%s'", last_line)
                    return False

        # If output contains active SSH tunnel command and no errors, likely alive
        if 'ssh' in output_lower and ('-L' in output or '-R' in output):
            logger.info("Tunnel check: SSH tunnel command visible, appears alive")
            return True

        # Inconclusive from tmux output — fall through to active probe or fail safe
        logger.info("Tunnel check: inconclusive from tmux output (last_line: %s)", last_line[:50])

        # If host provided, do an active probe to get a definitive answer
        if host:
            logger.info("Tunnel check: running active probe to %s:%d", host, remote_port)
            return probe_tunnel_connectivity(host, remote_port)

        # No host for active probe — fail safe (trigger reconnect rather than ignore dead tunnel)
        logger.info("Tunnel check: no host for active probe, assuming dead")
        return False
    except Exception as e:
        logger.warning("Tunnel check failed: %s", e)
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


def check_screen_and_claude_rdev(
    host: str, 
    session_id: str, 
    tmux_sess: str = None, 
    tmux_win: str = None
) -> tuple[str, str]:
    """Check screen session and Claude process status on rdev host.
    
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
        check_cmd = (
            f"screen -ls 2>/dev/null | grep -q '{screen_name}' && echo 'SCREEN_EXISTS' || echo 'NO_SCREEN'; "
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
        
        output = result.stdout
        stderr = result.stderr
        screen_exists = "SCREEN_EXISTS" in output
        claude_running = "CLAUDE_RUNNING" in output
        
        # Debug logging to diagnose false negatives
        logger.debug(
            "SSH health check for %s (screen=%s): returncode=%d, screen_exists=%s, claude_running=%s, stdout=%r, stderr=%r",
            host, screen_name, result.returncode, screen_exists, claude_running, output[:200], stderr[:100]
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


def check_claude_process_rdev(host: str, session_id: str) -> tuple[bool, str]:
    """Check if Claude Code with given session_id is running on rdev host via SSH.
    
    This is a simplified wrapper around check_screen_and_claude_rdev that returns
    a boolean alive status.
    
    Args:
        host: rdev host (e.g., "user/rdev-vm")
        session_id: Session ID to check for
        
    Returns:
        (alive: bool, reason: str)
    """
    status, reason = check_screen_and_claude_rdev(host, session_id)
    
    if status == "alive":
        return True, reason
    elif status == "screen_detached":
        # SSH failed but screen might be running - report as alive to avoid false positives
        return True, reason
    else:
        return False, reason
