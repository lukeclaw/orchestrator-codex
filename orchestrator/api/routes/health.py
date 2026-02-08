"""System health + comm channel status."""

from fastapi import APIRouter, Depends

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import sessions

router = APIRouter()


@router.get("/health")
def health_check(db=Depends(get_db)):
    """System health check."""
    all_sessions = sessions.list_sessions(db)

    status_counts = {}
    for s in all_sessions:
        status_counts[s.status] = status_counts.get(s.status, 0) + 1

    return {
        "status": "ok",
        "sessions": {
            "total": len(all_sessions),
            "by_status": status_counts,
        },
    }
