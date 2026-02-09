"""Reporting endpoints — used by remote sessions to report back."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import sessions

router = APIRouter()


class ReportEvent(BaseModel):
    session: str
    event: str
    data: dict = {}


class HookEvent(BaseModel):
    session: str
    hook_type: str
    payload: dict = {}


@router.post("/report")
def report(body: ReportEvent, db=Depends(get_db)):
    """Receive reports from worker sessions (progress, PR, error, etc.)."""
    session = sessions.get_session_by_name(db, body.session)

    # Update session last_activity
    if session:
        sessions.update_session(db, session.id, last_activity=datetime.now().isoformat())

    return {"ok": True}


@router.get("/guidance")
def get_guidance(session: str, db=Depends(get_db)):
    """Worker session checks for pending guidance/instructions."""
    s = sessions.get_session_by_name(db, session)
    if s is None:
        raise HTTPException(404, "Session not found")

    return {
        "session": session,
    }


@router.post("/hook")
def handle_hook(body: HookEvent, db=Depends(get_db)):
    """Receive hook events from worker sessions."""
    session = sessions.get_session_by_name(db, body.session)

    # Update session last_activity
    if session:
        sessions.update_session(db, session.id, last_activity=datetime.now().isoformat())

    return {"ok": True}
