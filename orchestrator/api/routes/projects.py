"""Project CRUD + task management."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import context as context_repo
from orchestrator.state.repositories import projects as repo
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.state.repositories import tasks as tasks_repo

router = APIRouter()


def _get_project_stats(db, project_id: str) -> dict:
    """Get aggregated stats for a project."""
    # Task stats (excluding subtasks for top-level counts)
    all_tasks = tasks_repo.list_tasks(db, project_id=project_id, parent_task_id=None)
    task_stats = {
        "total": len(all_tasks),
        "todo": len([t for t in all_tasks if t.status == "todo"]),
        "in_progress": len([t for t in all_tasks if t.status == "in_progress"]),
        "done": len([t for t in all_tasks if t.status == "done"]),
        "blocked": len([t for t in all_tasks if t.status == "blocked"]),
    }

    # Subtask stats - all tasks with a parent_task_id
    all_subtasks = tasks_repo.list_tasks(db, project_id=project_id, has_parent=True)
    subtask_stats = {
        "total": len(all_subtasks),
        "done": len([t for t in all_subtasks if t.status == "done"]),
    }

    # Worker stats - sessions assigned to tasks in this project
    assigned_session_ids = set(t.assigned_session_id for t in all_tasks if t.assigned_session_id)
    workers = []
    for sid in assigned_session_ids:
        session = sessions_repo.get_session(db, sid)
        if session:
            workers.append({"id": session.id, "name": session.name, "status": session.status})

    worker_stats = {
        "total": len(workers),
        "working": len([w for w in workers if w["status"] == "working"]),
        "idle": len([w for w in workers if w["status"] == "idle"]),
        "waiting": len([w for w in workers if w["status"] == "waiting"]),
        "details": workers,  # Include individual worker details
    }

    # Context stats
    context_items = context_repo.list_context(db, project_id=project_id)
    context_stats = {
        "total": len(context_items),
    }

    return {
        "tasks": task_stats,
        "subtasks": subtask_stats,
        "workers": worker_stats,
        "context": context_stats,
    }


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    target_date: str | None = None
    task_prefix: str | None = None  # Auto-generated if not provided


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    target_date: str | None = None


@router.get("/projects")
def list_projects(status: str | None = None, include_stats: bool = True, db=Depends(get_db)):
    projects = repo.list_projects(db, status=status)
    result = []
    for p in projects:
        item = {
            "id": p.id, "name": p.name, "description": p.description,
            "status": p.status, "target_date": p.target_date,
            "created_at": p.created_at,
        }
        if include_stats:
            item["stats"] = _get_project_stats(db, p.id)
        result.append(item)
    return result


@router.get("/projects/{project_id}")
def get_project(project_id: str, db=Depends(get_db)):
    p = repo.get_project(db, project_id)
    if p is None:
        raise HTTPException(404, "Project not found")
    return {
        "id": p.id, "name": p.name, "description": p.description,
        "status": p.status, "target_date": p.target_date,
        "created_at": p.created_at,
    }


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, db=Depends(get_db)):
    p = repo.create_project(db, body.name, body.description, body.target_date, body.task_prefix)
    return {"id": p.id, "name": p.name, "status": p.status, "task_prefix": p.task_prefix}


@router.patch("/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdate, db=Depends(get_db)):
    p = repo.get_project(db, project_id)
    if p is None:
        raise HTTPException(404, "Project not found")
    updated = repo.update_project(
        db, project_id,
        name=body.name,
        description=body.description,
        status=body.status,
    )
    return {"id": updated.id, "name": updated.name, "status": updated.status}


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, db=Depends(get_db)):
    p = repo.get_project(db, project_id)
    if p is None:
        raise HTTPException(404, "Project not found")

    # Cascade delete: first delete all tasks (which will cascade to subtasks)
    project_tasks = tasks_repo.list_tasks(db, project_id=project_id, parent_task_id=None)
    for task in project_tasks:
        tasks_repo.delete_task(db, task.id)  # This cascades to subtasks

    # Delete all context items for this project
    project_context = context_repo.list_context(db, project_id=project_id)
    for ctx in project_context:
        context_repo.delete_context_item(db, ctx.id)

    # Finally delete the project itself
    repo.delete_project(db, project_id)
    return {"ok": True, "deleted_tasks": len(project_tasks), "deleted_context": len(project_context)}
