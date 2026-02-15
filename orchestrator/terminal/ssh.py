"""SSH wrapper for connecting to remote hosts via tmux."""

from __future__ import annotations

import logging
import time

from orchestrator.terminal.manager import capture_output, send_keys

logger = logging.getLogger(__name__)

# Patterns that indicate a live SSH shell prompt
PROMPT_PATTERNS = ["$", "#", "%", "❯", "➜"]


def connect(session_name: str, window_name: str, host: str) -> bool:
    """Send an SSH command to a tmux window."""
    return send_keys(session_name, window_name, f"ssh {host}")


def health_check(session_name: str, window_name: str) -> bool:
    """Check if an SSH connection appears alive by detecting a shell prompt."""
    output = capture_output(session_name, window_name, lines=5)
    if not output:
        return False

    last_lines = output.strip().split("\n")[-3:]
    for line in last_lines:
        stripped = line.strip()
        if any(stripped.endswith(p) for p in PROMPT_PATTERNS):
            return True
    return False


def setup_tunnel(
    session_name: str,
    window_name: str,
    host: str,
    local_port: int,
    remote_port: int,
) -> bool:
    """Set up a reverse SSH tunnel for API access from remote to local.

    This creates a tunnel so the remote session can reach the orchestrator's
    API at localhost:remote_port, which maps to local_port on the local machine.
    """
    tunnel_cmd = f"ssh -R {remote_port}:127.0.0.1:{local_port} -N -f {host}"
    return send_keys(session_name, window_name, tunnel_cmd)


# --- rdev helpers ---

def is_rdev_host(host: str) -> bool:
    """Return True if host looks like an rdev session (MP_NAME/SESSION_NAME)."""
    parts = host.split("/")
    return len(parts) == 2 and all(parts)


def rdev_connect(session_name: str, window_name: str, host: str) -> bool:
    """Connect to an rdev VM via `rdev ssh`."""
    return send_keys(session_name, window_name, f"rdev ssh {host} --non-tmux")


def setup_rdev_tunnel(
    session_name: str,
    window_name: str,
    host: str,
    local_port: int,
    remote_port: int,
) -> bool:
    """Start a reverse SSH tunnel in a dedicated tmux window (foreground).

    Unlike setup_tunnel() which backgrounds with -f, this runs in the
    foreground of its own window so the process is trackable and killable.
    
    Uses StrictHostKeyChecking=no because rdev VMs are ephemeral and their
    host keys change frequently when VMs are recycled.
    """
    # -o StrictHostKeyChecking=no: Accept any host key (rdev VMs are ephemeral)
    # -o UserKnownHostsFile=/dev/null: Don't pollute known_hosts with ephemeral keys
    # -o ServerAliveInterval=30: Send keepalive every 30s to detect dead connections
    # -o ServerAliveCountMax=3: Exit after 3 missed keepalives (~90s of dead connection)
    # -o ExitOnForwardFailure=yes: Exit immediately if port forwarding setup fails
    cmd = (
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
        f" -o ServerAliveInterval=30 -o ServerAliveCountMax=3"
        f" -o ExitOnForwardFailure=yes"
        f" -N -R {remote_port}:127.0.0.1:{local_port} {host}"
    )
    return send_keys(session_name, window_name, cmd)


def wait_for_prompt(
    session_name: str,
    window_name: str,
    timeout: float = 30.0,
    interval: float = 2.0,
) -> bool:
    """Poll until a shell prompt is detected or timeout is reached."""
    elapsed = 0.0
    while elapsed < timeout:
        if health_check(session_name, window_name):
            return True
        time.sleep(interval)
        elapsed += interval
    return False
