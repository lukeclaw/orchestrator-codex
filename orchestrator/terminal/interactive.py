"""Interactive CLI lifecycle manager.

Provides in-memory registry for per-worker auxiliary terminals
(picture-in-picture interactive CLI).

Backends:
  - **tmux** (local workers): tmux window per CLI
  - **RWS PTY** (remote workers): daemon PTY session via Remote Worker Server
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from orchestrator.state.models import InteractiveCLI
from orchestrator.terminal import manager as tmux

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


def open_interactive_cli_via_rws(
    session_id: str,
    host: str,
    command: str | None = None,
    cwd: str | None = None,
    cols: int = 80,
    rows: int = 24,
) -> InteractiveCLI:
    """Open an interactive CLI for a remote worker via RWS daemon PTY.

    Creates a PTY session on the remote daemon instead of a tmux window.
    Much faster (~50ms vs 10-30s) and survives SSH disconnects.
    """
    from orchestrator.terminal.remote_worker_server import get_remote_worker_server

    if session_id in _active_clis:
        raise ValueError(f"Interactive CLI already active for session {session_id}")

    rws = get_remote_worker_server(host)
    pty_id = rws.create_pty(cmd="/bin/bash", cwd=cwd, cols=cols, rows=rows)

    # Send the initial command if provided
    if command:
        rws.execute(
            {
                "action": "pty_input",
                "pty_id": pty_id,
                "data": command + "\n",
            }
        )

    cli = InteractiveCLI(
        session_id=session_id,
        window_name=f"rws-{pty_id}",  # Synthetic name for identification
        status="active",
        created_at=datetime.now(UTC).isoformat(),
        initial_command=command,
        remote_pty_id=pty_id,
        rws_host=host,
    )
    _active_clis[session_id] = cli
    logger.info(
        "Opened interactive CLI via RWS for session %s (pty_id=%s, host=%s)",
        session_id,
        pty_id,
        host,
    )
    return cli


def close_interactive_cli(session_id: str, tmux_session: str = "orchestrator") -> bool:
    """Close the interactive CLI window for a worker.

    Handles both tmux-based and RWS PTY-based CLIs.
    """
    cli = _active_clis.pop(session_id, None)
    if not cli:
        return False

    if cli.remote_pty_id and cli.rws_host:
        # RWS PTY-based: destroy the remote PTY
        try:
            from orchestrator.terminal.remote_worker_server import get_remote_worker_server

            rws = get_remote_worker_server(cli.rws_host)
            rws.destroy_pty(cli.remote_pty_id)
        except Exception:
            logger.warning(
                "Failed to destroy remote PTY %s on %s",
                cli.remote_pty_id,
                cli.rws_host,
                exc_info=True,
            )
    else:
        # tmux-based: kill the tmux window
        tmux.kill_window(tmux_session, cli.window_name)

    cli.status = "closed"
    return True


def get_active_cli(session_id: str) -> InteractiveCLI | None:
    """Get the active interactive CLI for a session, or None."""
    return _active_clis.get(session_id)


def capture_interactive_cli(
    session_id: str, tmux_session: str = "orchestrator", lines: int = 30
) -> str | None:
    """Capture recent output from the interactive CLI.

    For RWS PTY-based CLIs, uses the daemon's pty_capture command.
    For tmux-based CLIs, uses tmux capture_output.
    """
    cli = _active_clis.get(session_id)
    if not cli:
        return None

    if cli.remote_pty_id and cli.rws_host:
        try:
            from orchestrator.terminal.remote_worker_server import get_remote_worker_server

            rws = get_remote_worker_server(cli.rws_host)
            resp = rws.execute(
                {
                    "action": "pty_capture",
                    "pty_id": cli.remote_pty_id,
                    "lines": lines,
                }
            )
            return resp.get("output", "")
        except Exception:
            logger.warning("Failed to capture remote PTY output", exc_info=True)
            return None

    return tmux.capture_output(tmux_session, cli.window_name, lines=lines)


def send_to_interactive_cli(
    session_id: str, tmux_session: str = "orchestrator", text: str = "", enter: bool = True
) -> bool:
    """Send input to the interactive CLI.

    For RWS PTY-based CLIs, sends via the daemon's pty_input command.
    For tmux-based CLIs, uses tmux send_keys.
    """
    cli = _active_clis.get(session_id)
    if not cli:
        return False

    if cli.remote_pty_id and cli.rws_host:
        try:
            from orchestrator.terminal.remote_worker_server import get_remote_worker_server

            data = text + ("\n" if enter else "")
            rws = get_remote_worker_server(cli.rws_host)
            rws.execute(
                {
                    "action": "pty_input",
                    "pty_id": cli.remote_pty_id,
                    "data": data,
                }
            )
            return True
        except Exception:
            logger.warning("Failed to send to remote PTY", exc_info=True)
            return False

    return tmux.send_keys(tmux_session, cli.window_name, text, enter=enter)


def check_interactive_cli_alive(session_id: str, tmux_session: str = "orchestrator") -> bool:
    """Check if the interactive CLI is still alive.

    For RWS PTY-based CLIs, checks via the daemon's pty_list command.
    For tmux-based CLIs, checks if the tmux window still exists.
    """
    cli = _active_clis.get(session_id)
    if not cli:
        return False

    if cli.remote_pty_id and cli.rws_host:
        try:
            from orchestrator.terminal.remote_worker_server import get_remote_worker_server

            rws = get_remote_worker_server(cli.rws_host)
            resp = rws.execute({"action": "pty_list"})
            ptys = resp.get("ptys", [])
            alive = any(p["pty_id"] == cli.remote_pty_id and p["alive"] for p in ptys)
            if not alive:
                _active_clis.pop(session_id, None)
            return alive
        except Exception:
            # RWS not reachable — keep the CLI entry (daemon may still be alive)
            return True

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
