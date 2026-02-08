"""tmux operations for managing terminal sessions."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TmuxWindow:
    index: int
    name: str
    active: bool


def _run_tmux(*args: str, check: bool = True, timeout: int = 10) -> subprocess.CompletedProcess:
    """Run a tmux command and return the result."""
    cmd = ["tmux"] + list(args)
    logger.debug("Running: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


def is_tmux_available() -> bool:
    """Check if tmux is installed and runnable."""
    try:
        result = subprocess.run(
            ["tmux", "-V"], capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def session_exists(session_name: str) -> bool:
    """Check if a tmux session exists."""
    result = _run_tmux("has-session", "-t", session_name, check=False)
    return result.returncode == 0


def create_session(session_name: str, cols: int = 80, rows: int = 24) -> bool:
    """Create a new tmux session (detached) if it doesn't exist.

    Sets an initial size so shell output is formatted for a reasonable width
    before a browser client connects and sends its actual dimensions.
    """
    if session_exists(session_name):
        return False
    _run_tmux("new-session", "-d", "-s", session_name, "-x", str(cols), "-y", str(rows))
    logger.info("Created tmux session: %s (size %dx%d)", session_name, cols, rows)
    return True


def create_window(session_name: str, window_name: str) -> str:
    """Create a new window in the session. Returns the tmux target."""
    # Ensure session exists
    if not session_exists(session_name):
        create_session(session_name)

    _run_tmux("new-window", "-t", session_name, "-n", window_name)
    target = f"{session_name}:{window_name}"
    logger.info("Created tmux window: %s", target)
    return target


def list_windows(session_name: str) -> list[TmuxWindow]:
    """List all windows in a tmux session."""
    if not session_exists(session_name):
        return []

    result = _run_tmux(
        "list-windows", "-t", session_name,
        "-F", "#{window_index}:#{window_name}:#{window_active}",
        check=False,
    )
    if result.returncode != 0:
        return []

    windows = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split(":")
        if len(parts) >= 3:
            windows.append(TmuxWindow(
                index=int(parts[0]),
                name=parts[1],
                active=parts[2] == "1",
            ))
    return windows


def window_exists(session_name: str, window_name: str) -> bool:
    """Check if a specific window exists in a tmux session."""
    if not session_exists(session_name):
        return False
    windows = list_windows(session_name)
    return any(w.name == window_name for w in windows)


def ensure_window(session_name: str, window_name: str) -> str:
    """Ensure a tmux session and window exist, creating them if needed.

    Returns the tmux target string (session:window).
    """
    if not session_exists(session_name):
        create_session(session_name)

    if not window_exists(session_name, window_name):
        # Check if this is the default first window — rename it instead of creating new
        windows = list_windows(session_name)
        if len(windows) == 1 and windows[0].name in ("bash", "zsh", "0"):
            # Rename the default window
            target = f"{session_name}:{windows[0].name}"
            _run_tmux("rename-window", "-t", target, window_name, check=False)
        else:
            create_window(session_name, window_name)

    return f"{session_name}:{window_name}"


def kill_window(session_name: str, window_name: str) -> bool:
    """Kill a specific window."""
    target = f"{session_name}:{window_name}"
    result = _run_tmux("kill-window", "-t", target, check=False)
    if result.returncode == 0:
        logger.info("Killed tmux window: %s", target)
        return True
    return False


def capture_output(session_name: str, window_name: str, lines: int = 50) -> str:
    """Capture visible pane content from a window."""
    target = f"{session_name}:{window_name}"
    result = _run_tmux(
        "capture-pane", "-p", "-t", target, "-S", f"-{lines}",
        check=False,
    )
    if result.returncode != 0:
        logger.warning("Failed to capture output from %s: %s", target, result.stderr)
        return ""
    return result.stdout


def send_keys(session_name: str, window_name: str, text: str, enter: bool = True) -> bool:
    """Send keystrokes to a tmux window."""
    target = f"{session_name}:{window_name}"
    args = ["send-keys", "-t", target, text]
    if enter:
        args.append("Enter")
    result = _run_tmux(*args, check=False)
    if result.returncode == 0:
        logger.debug("Sent keys to %s: %s", target, text[:80])
        return True
    logger.warning("Failed to send keys to %s: %s", target, result.stderr)
    return False


def send_keys_literal(session_name: str, window_name: str, text: str) -> bool:
    """Send literal keystrokes to a tmux window (no special key handling)."""
    target = f"{session_name}:{window_name}"
    result = _run_tmux("send-keys", "-l", "-t", target, text, check=False)
    if result.returncode == 0:
        return True
    logger.warning("Failed to send literal keys to %s: %s", target, result.stderr)
    return False


def capture_pane_with_escapes(session_name: str, window_name: str, lines: int = 0) -> str:
    """Capture pane content including ANSI escape sequences.

    Args:
        lines: Number of scrollback lines to include. 0 means capture only
               the visible pane area (no scrollback).
    """
    target = f"{session_name}:{window_name}"
    cmd = ["capture-pane", "-p", "-e", "-t", target]
    if lines > 0:
        cmd += ["-S", f"-{lines}"]
    result = _run_tmux(*cmd, check=False)
    if result.returncode != 0:
        return ""
    return result.stdout


def resize_pane(session_name: str, window_name: str, cols: int, rows: int) -> bool:
    """Resize a tmux pane."""
    target = f"{session_name}:{window_name}"
    result = _run_tmux(
        "resize-window", "-t", target, "-x", str(cols), "-y", str(rows),
        check=False,
    )
    return result.returncode == 0


def clear_pane(session_name: str, window_name: str) -> bool:
    """Clear the pane screen and scrollback history.

    Sends Ctrl-L to redraw the prompt, then clears the scrollback buffer
    so stale content formatted at the wrong width is discarded.
    """
    target = f"{session_name}:{window_name}"
    # Send C-l (clear screen / redraw prompt)
    result = _run_tmux("send-keys", "-t", target, "C-l", check=False)
    # Also clear scrollback history so old wide content isn't recaptured
    _run_tmux("clear-history", "-t", target, check=False)
    return result.returncode == 0


def kill_session(session_name: str) -> bool:
    """Kill an entire tmux session."""
    result = _run_tmux("kill-session", "-t", session_name, check=False)
    if result.returncode == 0:
        logger.info("Killed tmux session: %s", session_name)
        return True
    return False
