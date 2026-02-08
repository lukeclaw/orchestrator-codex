"""Decision queue: list, respond, dismiss."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import decisions as repo

router = APIRouter()


class DecisionCreate(BaseModel):
    question: str
    project_id: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    options: list[str] | None = None
    context: str | None = None
    urgency: str = "normal"


class DecisionRespond(BaseModel):
    response: str
    resolved_by: str = "user"


@router.get("/decisions")
def list_decisions(
    status: str | None = None,
    project_id: str | None = None,
    db=Depends(get_db),
):
    decisions = repo.list_decisions(db, status=status, project_id=project_id)
    return [
        {
            "id": d.id, "question": d.question, "options": d.options_list,
            "context": d.context, "urgency": d.urgency, "status": d.status,
            "response": d.response, "session_id": d.session_id,
            "project_id": d.project_id, "task_id": d.task_id,
            "created_at": d.created_at, "resolved_at": d.resolved_at,
        }
        for d in decisions
    ]


@router.get("/decisions/pending")
def list_pending(db=Depends(get_db)):
    pending = repo.list_pending(db)
    return [
        {
            "id": d.id, "question": d.question, "options": d.options_list,
            "context": d.context, "urgency": d.urgency, "status": d.status,
            "session_id": d.session_id, "created_at": d.created_at,
        }
        for d in pending
    ]


@router.post("/decisions", status_code=201)
def create_decision(body: DecisionCreate, db=Depends(get_db)):
    d = repo.create_decision(
        db, body.question,
        project_id=body.project_id, task_id=body.task_id,
        session_id=body.session_id, options=body.options,
        context=body.context, urgency=body.urgency,
    )
    return {"id": d.id, "question": d.question, "status": d.status}


@router.post("/decisions/{decision_id}/respond")
def respond_decision(decision_id: str, body: DecisionRespond, db=Depends(get_db)):
    d = repo.get_decision(db, decision_id)
    if d is None:
        raise HTTPException(404, "Decision not found")
    if d.status != "pending":
        raise HTTPException(400, f"Decision already {d.status}")
    responded = repo.respond_decision(db, decision_id, body.response, body.resolved_by)
    return {"id": responded.id, "status": responded.status, "response": responded.response}


@router.post("/decisions/{decision_id}/dismiss")
def dismiss_decision(decision_id: str, db=Depends(get_db)):
    d = repo.get_decision(db, decision_id)
    if d is None:
        raise HTTPException(404, "Decision not found")
    dismissed = repo.dismiss_decision(db, decision_id)
    return {"id": dismissed.id, "status": dismissed.status}
