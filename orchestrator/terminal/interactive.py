"""Interactive CLI lifecycle manager.

Provides in-memory registry and tmux operations for per-worker auxiliary
terminals (picture-in-picture interactive CLI).
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from orchestrator.state.models import InteractiveCLI
from orchestrator.terminal import manager as tmux
from orchestrator.terminal import ssh

logger = logging.getLogger(__name__)

# In-memory registry: session_id -> InteractiveCLI
_active_clis: dict[str, InteractiveCLI] = {}


def open_interactive_cli(
    tmux_session: str,
    window_name: str,
    session_id: str,
    command: str | None = None,
    cwd: str | None = None,
) -> InteractiveCLI:
    """Open an interactive CLI for a local worker.

    Creates a new tmux window named '{window_name}-icli'.
    """
    if session_id in _active_clis:
        raise ValueError(f"Interactive CLI already active for session {session_id}")

    icli_window = f"{window_name}-icli"

    # Create a new tmux window
    tmux.create_window(tmux_session, icli_window, cwd=cwd)

    if command:
        tmux.send_keys(tmux_session, icli_window, command, enter=True)

    cli = InteractiveCLI(
        session_id=session_id,
        window_name=icli_window,
        status="active",
        created_at=datetime.now(UTC).isoformat(),
        initial_command=command,
    )
    _active_clis[session_id] = cli
    return cli


def open_interactive_cli_remote(
    tmux_session: str,
    window_name: str,
    session_id: str,
    host: str,
    command: str | None = None,
    cwd: str | None = None,
) -> InteractiveCLI:
    """Open an interactive CLI for a remote worker.

    Creates a new tmux window, SSHs into the remote host, and optionally
    runs a command.
    """
    if session_id in _active_clis:
        raise ValueError(f"Interactive CLI already active for session {session_id}")

    icli_window = f"{window_name}-icli"

    # Create new tmux window
    tmux.create_window(tmux_session, icli_window)

    # SSH into the remote host
    ssh.remote_connect(tmux_session, icli_window, host)

    # Wait for shell prompt
    if not ssh.wait_for_prompt(tmux_session, icli_window, timeout=30):
        tmux.kill_window(tmux_session, icli_window)
        raise RuntimeError(f"SSH to {host} timed out for interactive CLI")

    if cwd:
        tmux.send_keys(tmux_session, icli_window, f"cd {cwd}", enter=True)
        time.sleep(0.3)

    if command:
        tmux.send_keys(tmux_session, icli_window, command, enter=True)

    cli = InteractiveCLI(
        session_id=session_id,
        window_name=icli_window,
        status="active",
        created_at=datetime.now(UTC).isoformat(),
        initial_command=command,
    )
    _active_clis[session_id] = cli
    return cli


def close_interactive_cli(session_id: str, tmux_session: str = "orchestrator") -> bool:
    """Close the interactive CLI window for a worker."""
    cli = _active_clis.pop(session_id, None)
    if not cli:
        return False

    tmux.kill_window(tmux_session, cli.window_name)
    cli.status = "closed"
    return True


def get_active_cli(session_id: str) -> InteractiveCLI | None:
    """Get the active interactive CLI for a session, or None."""
    return _active_clis.get(session_id)


def capture_interactive_cli(
    session_id: str, tmux_session: str = "orchestrator", lines: int = 30
) -> str | None:
    """Capture recent output from the interactive CLI."""
    cli = _active_clis.get(session_id)
    if not cli:
        return None
    return tmux.capture_output(tmux_session, cli.window_name, lines=lines)


def send_to_interactive_cli(
    session_id: str, tmux_session: str = "orchestrator", text: str = "", enter: bool = True
) -> bool:
    """Send input to the interactive CLI."""
    cli = _active_clis.get(session_id)
    if not cli:
        return False
    return tmux.send_keys(tmux_session, cli.window_name, text, enter=enter)


def check_interactive_cli_alive(session_id: str, tmux_session: str = "orchestrator") -> bool:
    """Check if the interactive CLI window still exists in tmux."""
    cli = _active_clis.get(session_id)
    if not cli:
        return False
    if not tmux.window_exists(tmux_session, cli.window_name):
        _active_clis.pop(session_id, None)
        return False
    return True


def cleanup_orphaned_icli_windows(
    tmux_session: str = "orchestrator",
) -> int:
    """Kill any orphaned *-icli tmux windows from a previous server run."""
    windows = tmux.list_windows(tmux_session)
    killed = 0
    for w in windows:
        if w.name.endswith("-icli"):
            tmux.kill_window(tmux_session, w.name)
            killed += 1
    if killed:
        logger.info("Cleaned up %d orphaned interactive CLI windows", killed)
    return killed
