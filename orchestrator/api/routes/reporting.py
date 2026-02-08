"""Reporting endpoints — used by remote sessions to report back."""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import activities, decisions, pull_requests, sessions

router = APIRouter()


class ReportEvent(BaseModel):
    session: str
    event: str
    data: dict = {}


class DecisionRequest(BaseModel):
    session: str
    question: str
    options: list[str] | None = None
    context: str | None = None
    urgency: str = "normal"


class HookEvent(BaseModel):
    session: str
    hook_type: str
    payload: dict = {}


@router.post("/report")
def report(body: ReportEvent, db=Depends(get_db)):
    """Receive reports from worker sessions (progress, PR, error, etc.)."""
    session = sessions.get_session_by_name(db, body.session)
    session_id = session.id if session else None

    # Log as activity
    activity = activities.log_activity(
        db,
        event_type=body.event,
        session_id=session_id,
        event_data=json.dumps(body.data),
        actor=body.session,
    )

    # Handle specific event types
    if body.event == "pr_created" and "url" in body.data:
        pull_requests.create_pull_request(
            db,
            url=body.data["url"],
            session_id=session_id,
            title=body.data.get("title"),
        )

    # Update session last_activity
    if session:
        from datetime import datetime
        sessions.update_session(db, session.id, last_activity=datetime.now().isoformat())

    return {"ok": True, "activity_id": activity.id}


@router.post("/decision")
def request_decision(body: DecisionRequest, db=Depends(get_db)):
    """Worker session requests a decision from the user."""
    session = sessions.get_session_by_name(db, body.session)
    session_id = session.id if session else None

    d = decisions.create_decision(
        db, body.question,
        session_id=session_id,
        options=body.options,
        context=body.context,
        urgency=body.urgency,
    )

    return {"ok": True, "decision_id": d.id}


@router.get("/guidance")
def get_guidance(session: str, db=Depends(get_db)):
    """Worker session checks for pending guidance/instructions."""
    s = sessions.get_session_by_name(db, session)
    if s is None:
        raise HTTPException(404, "Session not found")

    # Get pending decisions for this session that have been responded
    responded = decisions.list_decisions(db, status="responded", session_id=s.id)

    # Get recent activities targeted at this session
    recent = activities.list_activities(db, session_id=s.id, limit=5)

    return {
        "session": session,
        "pending_responses": [
            {"id": d.id, "question": d.question, "response": d.response}
            for d in responded
        ],
        "recent_activities": [
            {"type": a.event_type, "data": a.event_data, "at": a.created_at}
            for a in recent
        ],
    }


@router.post("/hook")
def handle_hook(body: HookEvent, db=Depends(get_db)):
    """Receive hook events from worker sessions."""
    session = sessions.get_session_by_name(db, body.session)
    session_id = session.id if session else None

    activities.log_activity(
        db,
        event_type=f"hook.{body.hook_type}",
        session_id=session_id,
        event_data=json.dumps(body.payload),
        actor=body.session,
    )

    return {"ok": True}
