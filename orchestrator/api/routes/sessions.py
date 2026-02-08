"""Session CRUD + send/takeover/release."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import sessions as repo
from orchestrator.terminal.manager import ensure_window, kill_window

logger = logging.getLogger(__name__)

router = APIRouter()


class SessionCreate(BaseModel):
    name: str
    host: str
    mp_path: str | None = None


class SessionUpdate(BaseModel):
    status: str | None = None
    takeover_mode: bool | None = None


class SendMessage(BaseModel):
    message: str


@router.get("/sessions")
def list_sessions(status: str | None = None, db=Depends(get_db)):
    sessions = repo.list_sessions(db, status=status)
    return [
        {
            "id": s.id, "name": s.name, "host": s.host,
            "mp_path": s.mp_path, "tmux_window": s.tmux_window,
            "status": s.status, "takeover_mode": s.takeover_mode,
            "current_task_id": s.current_task_id,
            "created_at": s.created_at, "last_activity": s.last_activity,
        }
        for s in sessions
    ]


@router.get("/sessions/{session_id}")
def get_session(session_id: str, db=Depends(get_db)):
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    return {
        "id": s.id, "name": s.name, "host": s.host,
        "mp_path": s.mp_path, "tmux_window": s.tmux_window,
        "status": s.status, "takeover_mode": s.takeover_mode,
        "current_task_id": s.current_task_id,
        "created_at": s.created_at, "last_activity": s.last_activity,
    }


@router.post("/sessions", status_code=201)
def create_session(body: SessionCreate, db=Depends(get_db)):
    # Create tmux window for the session
    tmux_session_name = "orchestrator"
    tmux_window = None
    try:
        target = ensure_window(tmux_session_name, body.name)
        tmux_window = target
        logger.info("Created tmux window for session %s: %s", body.name, target)
    except Exception:
        logger.warning("Could not create tmux window for session %s", body.name, exc_info=True)

    s = repo.create_session(db, body.name, body.host, body.mp_path, tmux_window=tmux_window)
    return {"id": s.id, "name": s.name, "status": s.status}


@router.patch("/sessions/{session_id}")
def update_session(session_id: str, body: SessionUpdate, db=Depends(get_db)):
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    updated = repo.update_session(
        db, session_id,
        status=body.status,
        takeover_mode=body.takeover_mode,
    )
    return {"id": updated.id, "status": updated.status}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, db=Depends(get_db)):
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    # Kill the tmux window if it exists
    if s.tmux_window:
        if ":" in s.tmux_window:
            tmux_sess, tmux_win = s.tmux_window.split(":", 1)
        else:
            tmux_sess, tmux_win = "orchestrator", s.tmux_window
        try:
            kill_window(tmux_sess, tmux_win)
            logger.info("Killed tmux window %s:%s for session %s", tmux_sess, tmux_win, s.name)
        except Exception:
            logger.warning("Could not kill tmux window for session %s", s.name, exc_info=True)

    repo.delete_session(db, session_id)
    return {"ok": True}


@router.post("/sessions/{session_id}/send")
def send_message(session_id: str, body: SendMessage, request: Request, db=Depends(get_db)):
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    from orchestrator.terminal.session import send_to_session
    config = getattr(request.app.state, "config", {})
    tmux_session = config.get("tmux", {}).get("session_name", "orchestrator")

    success = send_to_session(s.name, body.message, tmux_session)
    if not success:
        raise HTTPException(500, "Failed to send message")
    return {"ok": True, "session": s.name}
