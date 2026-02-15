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


# --- rdev helpers ---

def is_rdev_host(host: str) -> bool:
    """Return True if host looks like an rdev session (MP_NAME/SESSION_NAME)."""
    parts = host.split("/")
    return len(parts) == 2 and all(parts)


def rdev_connect(session_name: str, window_name: str, host: str) -> bool:
    """Connect to an rdev VM via `rdev ssh`."""
    return send_keys(session_name, window_name, f"rdev ssh {host} --non-tmux")


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
