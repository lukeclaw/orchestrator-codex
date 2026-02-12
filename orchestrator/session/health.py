"""Health check utilities for session management.

Functions to check the status of Claude processes, screen sessions,
SSH tunnels, and SSH connections for both local and rdev workers.
"""

import logging
import subprocess

from orchestrator.terminal.manager import capture_output

logger = logging.getLogger(__name__)


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


def check_tunnel_alive(tmux_sess: str, tunnel_win: str) -> bool:
    """Check if the tunnel window has an active SSH tunnel running.
    
    A dead tunnel will show error messages OR return to shell prompt.
    An alive tunnel shows no output (SSH is blocking, waiting for connection).
    
    Args:
        tmux_sess: tmux session name
        tunnel_win: tmux window name for the tunnel
        
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
        
        # Fallback: if we can't determine, assume alive
        logger.info("Tunnel check: uncertain status, assuming alive (output: %s)", last_line[:50])
        return True
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
