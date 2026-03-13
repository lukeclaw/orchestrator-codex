"""Interactive CLI lifecycle manager.

Provides in-memory registry for per-worker auxiliary terminals
(picture-in-picture interactive CLI).

Backends:
  - **tmux** (local workers): tmux window per CLI
  - **RWS PTY** (remote workers): daemon PTY session via Remote Worker Server
"""

from __future__ import annotations

import logging
import sqlite3
import threading
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

    # Kill any orphaned tmux windows with the same name (duplicates can
    # accumulate if the server restarted while a window was alive).
    while tmux.window_exists(tmux_session, icli_window):
        tmux.kill_window(tmux_session, icli_window)

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
    pty_id = rws.create_pty(
        cmd="/bin/bash",
        cwd=cwd,
        cols=cols,
        rows=rows,
        session_id=session_id,
        role="interactive-cli",
    )

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


def recover_cli(
    session_id: str,
    db_conn: sqlite3.Connection,
    tmux_session: str = "orchestrator",
) -> InteractiveCLI | None:
    """Recover a single interactive CLI from a surviving tmux window or remote PTY.

    After a server restart the in-memory ``_active_clis`` registry is empty,
    but the underlying terminal (tmux window or daemon PTY) may still be alive.
    This function checks for a surviving backend and re-registers it so the
    frontend can reconnect without seeing "No active interactive CLI".

    Returns the recovered ``InteractiveCLI``, or ``None`` if nothing to recover.
    """
    from orchestrator.state.repositories import sessions as repo

    # Already registered — nothing to do
    if session_id in _active_clis:
        return _active_clis[session_id]

    session = repo.get_session(db_conn, session_id)
    if session is None:
        return None

    # --- Local (tmux) recovery ---
    if session.host == "localhost":
        icli_window = f"{session.name}-icli"
        if not tmux.window_exists(tmux_session, icli_window):
            return None
        cli = InteractiveCLI(
            session_id=session_id,
            window_name=icli_window,
            status="active",
            created_at=datetime.now(UTC).isoformat(),
        )
        _active_clis[session_id] = cli
        logger.info("Recovered local interactive CLI for session %s", session_id)
        return cli

    # --- Remote (RWS PTY) recovery ---
    try:
        from orchestrator.terminal.remote_worker_server import get_remote_worker_server

        rws = get_remote_worker_server(session.host)
        ptys = rws.list_ptys()
    except Exception:
        logger.debug(
            "Cannot reach RWS daemon on %s for session %s recovery",
            session.host,
            session_id,
            exc_info=True,
        )
        return None

    for pty in ptys:
        if (
            pty.get("session_id") == session_id
            and pty.get("alive")
            and pty.get("role") == "interactive-cli"
        ):
            cli = InteractiveCLI(
                session_id=session_id,
                window_name=f"rws-{pty['pty_id']}",
                status="active",
                created_at=pty.get("created_at", datetime.now(UTC).isoformat()),
                remote_pty_id=pty["pty_id"],
                rws_host=session.host,
            )
            _active_clis[session_id] = cli
            logger.info(
                "Recovered remote interactive CLI for session %s (pty_id=%s, host=%s)",
                session_id,
                pty["pty_id"],
                session.host,
            )
            return cli

    return None


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
            return rws.capture_pty(cli.remote_pty_id, lines=lines)
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


def restore_icli_windows(
    conn: sqlite3.Connection,
    tmux_session: str = "orchestrator",
) -> int:
    """Restore interactive CLI state from surviving tmux windows.

    On server restart the in-memory ``_active_clis`` registry is empty, but
    tmux windows named ``{worker}-icli`` may still be alive.  This scans for
    them, looks up the matching session in the database, and repopulates the
    registry so the frontend can reconnect seamlessly.

    Windows with no matching session are killed as truly orphaned.
    """
    from orchestrator.state.repositories import sessions as repo

    windows = tmux.list_windows(tmux_session)
    restored = 0
    killed = 0
    # Group -icli windows by worker name to detect duplicates
    icli_by_worker: dict[str, list[tmux.TmuxWindow]] = {}
    for w in windows:
        if not w.name.endswith("-icli"):
            continue
        worker_name = w.name[: -len("-icli")]
        icli_by_worker.setdefault(worker_name, []).append(w)

    for worker_name, icli_windows in icli_by_worker.items():
        session = repo.get_session_by_name(conn, worker_name)
        if session is None:
            # No matching session — truly orphaned, kill all (by index to
            # handle duplicate window names safely)
            for w in icli_windows:
                tmux.kill_window(tmux_session, str(w.index))
                killed += 1
            continue

        # Kill duplicates — keep only the first, kill the rest by index
        if len(icli_windows) > 1:
            for w in icli_windows[1:]:
                tmux.kill_window(tmux_session, str(w.index))
                killed += 1
            logger.info(
                "Killed %d duplicate -icli windows for worker %s",
                len(icli_windows) - 1,
                worker_name,
            )

        if session.id in _active_clis:
            continue  # Already registered

        cli = InteractiveCLI(
            session_id=session.id,
            window_name=icli_windows[0].name,
            status="active",
            created_at=datetime.now(UTC).isoformat(),
        )
        _active_clis[session.id] = cli
        restored += 1

    if restored:
        logger.info("Restored %d interactive CLI sessions from tmux", restored)
    if killed:
        logger.info("Killed %d orphaned interactive CLI windows (no matching session)", killed)

    # Discover remote PTY-backed CLIs in the background (connecting to
    # each daemon can take seconds — don't block server startup).
    remote_sessions = [
        s
        for s in repo.list_sessions(conn, session_type="worker")
        if s.host != "localhost" and s.id not in _active_clis
    ]
    if remote_sessions:
        threading.Thread(
            target=_restore_remote_iclis,
            args=(remote_sessions,),
            daemon=True,
        ).start()

    return restored


def _restore_remote_iclis(sessions: list) -> None:
    """Reconnect to alive PTY sessions on remote daemons using session_id.

    The daemon tracks which session_id each PTY belongs to, so we can
    directly map PTYs back to orchestrator sessions.  Orphaned PTYs
    (no session_id or session_id not in our list) are destroyed.

    Runs in a background thread so it doesn't block server startup.
    """
    from orchestrator.terminal.remote_worker_server import get_remote_worker_server

    session_by_id = {s.id: s for s in sessions}
    # Group sessions by host so we only query each daemon once
    by_host: dict[str, list] = {}
    for s in sessions:
        by_host.setdefault(s.host, []).append(s)

    for host, host_sessions in by_host.items():
        try:
            rws = get_remote_worker_server(host)
        except RuntimeError:
            # Daemon not ready yet — will be picked up by ensure_rws_starting later
            continue

        try:
            ptys = rws.list_ptys()
        except Exception:
            continue

        alive_ptys = [p for p in ptys if p.get("alive")]
        if not alive_ptys:
            continue

        # Match PTYs to sessions by session_id (only interactive-cli role)
        matched_session_ids: set[str] = set()
        for pty in alive_ptys:
            sid = pty.get("session_id")
            if not sid or sid not in session_by_id or sid in _active_clis:
                continue
            if pty.get("role") != "interactive-cli":
                continue

            cli = InteractiveCLI(
                session_id=sid,
                window_name=f"rws-{pty['pty_id']}",
                status="active",
                created_at=pty.get("created_at", datetime.now(UTC).isoformat()),
                remote_pty_id=pty["pty_id"],
                rws_host=host,
            )
            _active_clis[sid] = cli
            matched_session_ids.add(sid)
            logger.info(
                "Restored remote interactive CLI for session %s (pty_id=%s, host=%s)",
                sid,
                pty["pty_id"],
                host,
            )

        # Destroy orphaned PTYs (no session_id, or session not in our list)
        for pty in alive_ptys:
            if pty.get("role") == "main":
                continue  # Never destroy main Claude PTYs from ICLI restore
            sid = pty.get("session_id")
            if sid and sid in matched_session_ids:
                continue  # Just restored — keep it
            if sid and sid in _active_clis:
                continue  # Already registered before this run
            try:
                rws.destroy_pty(pty["pty_id"])
                logger.info(
                    "Destroyed orphaned remote PTY %s on %s",
                    pty["pty_id"],
                    host,
                )
            except Exception:
                pass
