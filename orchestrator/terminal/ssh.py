"""SSH wrapper for connecting to remote hosts via tmux."""

from __future__ import annotations

import logging
import os
import subprocess
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


_LOCAL_HOSTS = {"localhost", "local", "127.0.0.1", "::1"}


def is_remote_host(host: str) -> bool:
    """Return True for any remote host (rdev or generic SSH)."""
    return host.lower() not in _LOCAL_HOSTS


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


# --- rdev SSH config bootstrap ---

_RDEV_SSH_CONFIG = os.path.expanduser("~/.ssh/config.rdev")


def _rdev_ssh_config_has_host(host: str) -> bool:
    """Check if ~/.ssh/config.rdev contains an entry for the given rdev host."""
    try:
        with open(_RDEV_SSH_CONFIG) as f:
            for line in f:
                if line.startswith("Host ") and host in line.split():
                    return True
    except (FileNotFoundError, OSError):
        pass
    return False


def ensure_rdev_ssh_config(host: str, timeout: int = 30) -> bool:
    """Ensure ~/.ssh/config.rdev has an entry for the given rdev host.

    The rdev CLI creates SSH config entries on first ``rdev ssh`` connection.
    For brand-new rdevs this entry won't exist, causing plain ``ssh host``
    commands to fail with "Could not resolve hostname".

    If the entry is missing, briefly runs ``rdev ssh --non-tmux`` to trigger
    config generation, then terminates the connection.

    Returns True if the config entry exists (or was successfully created).
    """
    if not is_rdev_host(host):
        return True

    if _rdev_ssh_config_has_host(host):
        return True

    logger.info("SSH config missing for %s, running rdev ssh to bootstrap", host)
    _remove_stale_known_hosts_old()

    try:
        proc = subprocess.Popen(
            ["rdev", "ssh", host, "--non-tmux"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError) as e:
        logger.error("Failed to run rdev ssh for %s: %s", host, e)
        return False

    # Wait for the config entry to appear (rdev writes it before connecting)
    deadline = time.time() + timeout
    created = False
    while time.time() < deadline:
        time.sleep(1)
        if _rdev_ssh_config_has_host(host):
            created = True
            break
        if proc.poll() is not None:
            break

    # Terminate the SSH session — we only needed the config entry
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=2)
        except Exception:
            pass

    if created:
        logger.info("SSH config bootstrapped for %s", host)
    else:
        logger.error("Failed to bootstrap SSH config for %s within %ds", host, timeout)
    return created
