"""SSH wrapper for connecting to remote hosts via tmux."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
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

# Per-host locking and cooldown for SSH config refresh
_refresh_locks: dict[str, threading.Lock] = {}
_refresh_lock_registry = threading.Lock()
_last_refresh: dict[str, float] = {}  # host -> time.monotonic()
_REFRESH_COOLDOWN = 120.0  # seconds between refreshes for the same host


def _get_refresh_lock(host: str) -> threading.Lock:
    """Return a per-host lock for SSH config refresh, creating if needed."""
    with _refresh_lock_registry:
        if host not in _refresh_locks:
            _refresh_locks[host] = threading.Lock()
        return _refresh_locks[host]


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


def _remove_rdev_ssh_config_host(host: str) -> bool:
    """Remove the SSH config block for *host* from ~/.ssh/config.rdev.

    A block starts with a ``Host`` line containing *host* and extends to the
    next ``Host`` line (or EOF).  Returns True if a block was removed.
    """
    try:
        with open(_RDEV_SSH_CONFIG) as f:
            lines = f.readlines()
    except (FileNotFoundError, OSError):
        return False

    new_lines: list[str] = []
    skipping = False
    removed = False

    for line in lines:
        if line.startswith("Host "):
            if host in line.split():
                skipping = True
                removed = True
                continue
            else:
                skipping = False
        if not skipping:
            new_lines.append(line)

    if not removed:
        return False

    try:
        with open(_RDEV_SSH_CONFIG, "w") as f:
            f.writelines(new_lines)
    except OSError as e:
        logger.warning("Could not update %s: %s", _RDEV_SSH_CONFIG, e)
        return False

    logger.info("Removed stale SSH config entry for %s", host)
    return True


def _kill_control_sockets_for_host(host: str) -> None:
    """Close the ControlMaster socket for *host*, if any.

    Must be called **before** removing the SSH config entry — the host alias
    is needed to compute the correct ControlPath hash (``%C``).
    """
    try:
        proc = subprocess.Popen(
            [
                "ssh",
                "-O",
                "exit",
                "-o",
                "ControlPath=/tmp/orchestrator-ssh-%C",
                host,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.wait(timeout=5)
        logger.debug("Closed ControlMaster socket for %s", host)
    except Exception:
        pass  # No socket, already closed, or timeout — all fine


def refresh_rdev_ssh_config(host: str, timeout: int = 30) -> bool:
    """Force-refresh the SSH config entry for an rdev host.

    Removes the existing (stale) entry from ``~/.ssh/config.rdev``, kills
    any cached ControlMaster socket, then re-runs ``rdev ssh --non-tmux`` to
    generate a fresh entry with the current HostName/Port.

    Thread-safe: uses a per-host lock so concurrent callers don't clobber
    each other.  Applies a cooldown (``_REFRESH_COOLDOWN`` seconds) to avoid
    rapid refresh loops when the host is genuinely unreachable.

    Returns True if the config entry exists after refresh, False on failure.
    """
    if not is_rdev_host(host):
        return True

    lock = _get_refresh_lock(host)
    if not lock.acquire(timeout=timeout + 5):
        logger.warning("refresh_rdev_ssh_config: lock timeout for %s", host)
        return _rdev_ssh_config_has_host(host)

    try:
        # Cooldown: skip if we just refreshed this host
        last = _last_refresh.get(host)
        if last is not None and (time.monotonic() - last) < _REFRESH_COOLDOWN:
            logger.debug("refresh_rdev_ssh_config: cooldown active for %s, skipping", host)
            return _rdev_ssh_config_has_host(host)

        logger.info("Refreshing SSH config for %s (removing stale entry)", host)

        # 1. Kill ControlMaster BEFORE removing config (needs host alias)
        _kill_control_sockets_for_host(host)

        # 2. Remove stale config entry
        _remove_rdev_ssh_config_host(host)

        # 3. Clean up known_hosts.old (same as bootstrap)
        _remove_stale_known_hosts_old()

        # 4. Spawn rdev ssh to regenerate the config entry
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

        # 5. Poll for the new config entry
        deadline = time.time() + timeout
        created = False
        while time.time() < deadline:
            time.sleep(1)
            if _rdev_ssh_config_has_host(host):
                created = True
                break
            if proc.poll() is not None:
                break

        # 6. Terminate the SSH session — we only needed the config entry
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
            _last_refresh[host] = time.monotonic()
            logger.info("SSH config refreshed for %s", host)
        else:
            logger.error("Failed to refresh SSH config for %s within %ds", host, timeout)

        return created
    finally:
        lock.release()


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
