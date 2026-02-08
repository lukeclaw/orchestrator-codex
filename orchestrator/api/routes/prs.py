"""PR listing + dependency graph."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import pull_requests as repo

router = APIRouter()


@router.get("/prs")
def list_prs(
    task_id: str | None = None,
    session_id: str | None = None,
    status: str | None = None,
    db=Depends(get_db),
):
    prs = repo.list_pull_requests(db, task_id=task_id, session_id=session_id, status=status)
    return [
        {
            "id": pr.id, "url": pr.url, "number": pr.number,
            "title": pr.title, "status": pr.status,
            "task_id": pr.task_id, "session_id": pr.session_id,
            "created_at": pr.created_at, "merged_at": pr.merged_at,
        }
        for pr in prs
    ]


@router.get("/prs/{pr_id}")
def get_pr(pr_id: str, db=Depends(get_db)):
    pr = repo.get_pull_request(db, pr_id)
    if pr is None:
        raise HTTPException(404, "PR not found")
    deps = repo.get_pr_dependencies(db, pr_id)
    return {
        "id": pr.id, "url": pr.url, "number": pr.number,
        "title": pr.title, "status": pr.status,
        "depends_on": [d.depends_on_pr_id for d in deps],
    }


class PrUpdate(BaseModel):
    status: str | None = None
    title: str | None = None


@router.patch("/prs/{pr_id}")
def update_pr(pr_id: str, body: PrUpdate, db=Depends(get_db)):
    pr = repo.get_pull_request(db, pr_id)
    if pr is None:
        raise HTTPException(404, "PR not found")
    updated = repo.update_pull_request(db, pr_id, status=body.status, title=body.title)
    return {"id": updated.id, "status": updated.status}
