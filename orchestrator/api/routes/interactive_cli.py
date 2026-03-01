"""API routes for interactive CLI (picture-in-picture terminal)."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.core.events import Event, publish
from orchestrator.state.repositories import sessions as repo
from orchestrator.terminal.interactive import (
    capture_interactive_cli,
    check_interactive_cli_alive,
    close_interactive_cli,
    get_active_cli,
    open_interactive_cli,
    open_interactive_cli_via_rws,
    send_to_interactive_cli,
)
from orchestrator.terminal.manager import tmux_target
from orchestrator.terminal.ssh import is_remote_host

logger = logging.getLogger(__name__)

router = APIRouter()


class OpenCLIRequest(BaseModel):
    command: str | None = None
    cwd: str | None = None


class SendRequest(BaseModel):
    message: str | None = None
    keys: str | None = None


@router.post("/sessions/{session_id}/interactive-cli")
def open_interactive_cli_endpoint(session_id: str, body: OpenCLIRequest, db=Depends(get_db)):
    """Open an interactive CLI for a worker session."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    # Check if already active
    existing = get_active_cli(session_id)
    if existing:
        raise HTTPException(409, "Interactive CLI already active for this session")

    tmux_sess, tmux_win = tmux_target(s.name)
    cwd = body.cwd or s.work_dir

    try:
        if is_remote_host(s.host):
            cli = open_interactive_cli_via_rws(
                session_id,
                host=s.host,
                command=body.command,
                cwd=cwd,
            )
        else:
            cli = open_interactive_cli(
                tmux_sess,
                s.name,
                session_id,
                command=body.command,
                cwd=cwd,
            )
    except ValueError as e:
        raise HTTPException(409, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))

    # Broadcast event
    publish(
        Event(
            type="interactive_cli_opened",
            data={
                "session_id": session_id,
                "session_name": s.name,
                "window_name": cli.window_name,
                "command": body.command,
            },
        )
    )

    return {"ok": True, "window_name": cli.window_name}


@router.delete("/sessions/{session_id}/interactive-cli")
def close_interactive_cli_endpoint(session_id: str, db=Depends(get_db)):
    """Close the interactive CLI for a worker session."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    tmux_sess, _ = tmux_target(s.name)
    closed = close_interactive_cli(session_id, tmux_sess)
    if not closed:
        raise HTTPException(404, "No active interactive CLI for this session")

    # Broadcast event
    publish(
        Event(
            type="interactive_cli_closed",
            data={
                "session_id": session_id,
            },
        )
    )

    return {"ok": True}


@router.get("/sessions/{session_id}/interactive-cli")
def get_interactive_cli_status(session_id: str, db=Depends(get_db)):
    """Get the status of the interactive CLI."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    cli = get_active_cli(session_id)
    if not cli:
        return {"active": False}

    # Verify it's still alive
    tmux_sess, _ = tmux_target(s.name)
    if not check_interactive_cli_alive(session_id, tmux_sess):
        return {"active": False}

    return {
        "active": True,
        "window_name": cli.window_name,
        "created_at": cli.created_at,
        "initial_command": cli.initial_command,
    }


@router.post("/sessions/{session_id}/interactive-cli/send")
def send_to_interactive_cli_endpoint(session_id: str, body: SendRequest, db=Depends(get_db)):
    """Send input to the interactive CLI."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    cli = get_active_cli(session_id)
    if not cli:
        raise HTTPException(404, "No active interactive CLI for this session")

    tmux_sess, _ = tmux_target(s.name)

    if body.message is not None:
        ok = send_to_interactive_cli(session_id, tmux_sess, body.message, enter=True)
    elif body.keys is not None:
        ok = send_to_interactive_cli(session_id, tmux_sess, body.keys, enter=False)
    else:
        raise HTTPException(400, "Either 'message' or 'keys' must be provided")

    if not ok:
        raise HTTPException(500, "Failed to send input to interactive CLI")

    return {"ok": True}


@router.post("/sessions/{session_id}/interactive-cli/capture")
def capture_interactive_cli_endpoint(session_id: str, lines: int = 30, db=Depends(get_db)):
    """Capture recent output from the interactive CLI."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    cli = get_active_cli(session_id)
    if not cli:
        raise HTTPException(404, "No active interactive CLI for this session")

    tmux_sess, _ = tmux_target(s.name)
    output = capture_interactive_cli(session_id, tmux_sess, lines=lines)

    return {"output": output or "", "lines": lines}


@router.post("/sessions/{session_id}/interactive-cli/minimize")
def minimize_interactive_cli(session_id: str, db=Depends(get_db)):
    """Minimize the interactive CLI overlay (UI-only, no tmux changes)."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    cli = get_active_cli(session_id)
    if not cli:
        raise HTTPException(404, "No active interactive CLI for this session")

    publish(
        Event(
            type="interactive_cli_minimized",
            data={"session_id": session_id},
        )
    )

    return {"ok": True}


@router.post("/sessions/{session_id}/interactive-cli/restore")
def restore_interactive_cli(session_id: str, db=Depends(get_db)):
    """Restore the interactive CLI overlay from minimized state."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    cli = get_active_cli(session_id)
    if not cli:
        raise HTTPException(404, "No active interactive CLI for this session")

    publish(
        Event(
            type="interactive_cli_restored",
            data={"session_id": session_id},
        )
    )

    return {"ok": True}
