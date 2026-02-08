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
