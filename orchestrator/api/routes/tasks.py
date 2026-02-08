"""Task CRUD + status updates + assignment."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import tasks as repo
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.state.repositories import projects as projects_repo
from orchestrator.state.repositories import activities as activities_repo

logger = logging.getLogger(__name__)

router = APIRouter()


class TaskCreate(BaseModel):
    project_id: str
    title: str
    description: str | None = None
    priority: int = 0


class TaskUpdate(BaseModel):
    status: str | None = None
    assigned_session_id: str | None = None
    priority: int | None = None
    title: str | None = None
    description: str | None = None


@router.get("/tasks")
def list_tasks(
    project_id: str | None = None,
    status: str | None = None,
    assigned_session_id: str | None = None,
    db=Depends(get_db),
):
    tasks = repo.list_tasks(db, project_id=project_id, status=status, assigned_session_id=assigned_session_id)
    return [
        {
            "id": t.id, "project_id": t.project_id, "title": t.title,
            "description": t.description, "status": t.status,
            "priority": t.priority, "assigned_session_id": t.assigned_session_id,
            "created_at": t.created_at, "started_at": t.started_at,
            "completed_at": t.completed_at,
        }
        for t in tasks
    ]


@router.get("/tasks/{task_id}")
def get_task(task_id: str, db=Depends(get_db)):
    t = repo.get_task(db, task_id)
    if t is None:
        raise HTTPException(404, "Task not found")
    return {
        "id": t.id, "project_id": t.project_id, "title": t.title,
        "description": t.description, "status": t.status,
        "priority": t.priority, "assigned_session_id": t.assigned_session_id,
        "created_at": t.created_at,
    }


@router.post("/tasks", status_code=201)
def create_task(body: TaskCreate, db=Depends(get_db)):
    t = repo.create_task(db, body.project_id, body.title, body.description, body.priority)
    return {"id": t.id, "title": t.title, "status": t.status}


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, body: TaskUpdate, request: Request, db=Depends(get_db)):
    t = repo.get_task(db, task_id)
    if t is None:
        raise HTTPException(404, "Task not found")

    old_assigned = t.assigned_session_id
    new_assigned = body.assigned_session_id

    updated = repo.update_task(
        db, task_id,
        status=body.status,
        assigned_session_id=new_assigned if new_assigned is not None else ...,
        priority=body.priority,
        title=body.title,
        description=body.description,
    )

    # Notify worker when a task is newly assigned
    if new_assigned and new_assigned != old_assigned:
        _notify_worker_of_assignment(db, updated, request)

    return {"id": updated.id, "status": updated.status}


def _notify_worker_of_assignment(db, task, request):
    """Send task context to the assigned worker via tmux."""
    try:
        from orchestrator.terminal.session import send_to_session

        session = sessions_repo.get_session(db, task.assigned_session_id)
        if not session:
            return

        # Get tmux session name from app config
        tmux_session = "orchestrator"
        if hasattr(request.app.state, "orchestrator"):
            tmux_session = request.app.state.orchestrator.tmux_session

        # Compose context message
        parts = [f"New task assigned: {task.title}"]
        if task.description:
            parts.append(f"\n{task.description}")

        # Include project context if available
        if task.project_id:
            project = projects_repo.get_project(db, task.project_id)
            if project:
                parts.append(f"\nProject: {project.name}")
                if project.description:
                    parts.append(f"Project context: {project.description[:500]}")

        message = "\n".join(parts)
        send_to_session(session.name, message, tmux_session)

        activities_repo.create_activity(
            db,
            event_type="task.assigned",
            session_id=session.id,
            task_id=task.id,
            event_data={"task_title": task.title, "session_name": session.name},
        )
        logger.info("Notified worker %s of task assignment: %s", session.name, task.title)
    except Exception:
        logger.exception("Failed to notify worker of task assignment")


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, db=Depends(get_db)):
    if not repo.delete_task(db, task_id):
        raise HTTPException(404, "Task not found")
    return {"ok": True}
