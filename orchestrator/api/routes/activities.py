"""Activity timeline endpoints."""

from fastapi import APIRouter, Depends

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import activities as repo

router = APIRouter()


@router.get("/activities")
def list_activities(
    project_id: str | None = None,
    session_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
    db=Depends(get_db),
):
    acts = repo.list_activities(
        db,
        project_id=project_id,
        session_id=session_id,
        event_type=event_type,
        limit=limit,
    )
    return [
        {
            "id": a.id,
            "event_type": a.event_type,
            "project_id": a.project_id,
            "task_id": a.task_id,
            "session_id": a.session_id,
            "event_data": a.event_data,
            "actor": a.actor,
            "created_at": a.created_at,
        }
        for a in acts
    ]
