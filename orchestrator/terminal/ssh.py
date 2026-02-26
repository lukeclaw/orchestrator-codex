"""SSH wrapper for connecting to remote hosts via tmux."""

from __future__ import annotations

import logging
import os
import time

from orchestrator.terminal.manager import capture_output, send_keys

logger = logging.getLogger(__name__)

# Path to the stale backup that ssh-keygen -R creates
_KNOWN_HOSTS_OLD = os.path.expanduser("~/.ssh/known_hosts.old")

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


# --- remote host helpers ---


def is_remote_host(host: str) -> bool:
    """Return True for any remote host (rdev or generic SSH)."""
    return host != "localhost"


def is_rdev_host(host: str) -> bool:
    """Return True if host looks like an rdev session (MP_NAME/SESSION_NAME)."""
    parts = host.split("/")
    return len(parts) == 2 and all(parts)


def _remove_stale_known_hosts_old() -> None:
    """Remove ~/.ssh/known_hosts.old if it exists.

    ``rdev ssh`` runs ``ssh-keygen -R <host>`` which renames known_hosts to
    known_hosts.old via a hard link.  If known_hosts.old already exists from
    a previous invocation, the link() call fails with "File exists" and
    ssh-keygen exits 255, aborting the entire ``rdev ssh`` connection.

    Removing the stale backup proactively prevents this.
    """
    try:
        os.remove(_KNOWN_HOSTS_OLD)
        logger.debug("Removed stale %s", _KNOWN_HOSTS_OLD)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("Could not remove %s: %s", _KNOWN_HOSTS_OLD, e)


def remote_connect(session_name: str, window_name: str, host: str) -> bool:
    """Connect to a remote host. Uses `rdev ssh` for rdev hosts, plain `ssh` otherwise."""
    if is_rdev_host(host):
        _remove_stale_known_hosts_old()
        return send_keys(session_name, window_name, f"rdev ssh {host} --non-tmux")
    return send_keys(session_name, window_name, f"ssh {host}")


def rdev_connect(session_name: str, window_name: str, host: str) -> bool:
    """Connect to an rdev VM via `rdev ssh`. Alias for backward compat."""
    return remote_connect(session_name, window_name, host)


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
