"""Task CRUD + status updates + assignment."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import projects as projects_repo
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.state.repositories import tasks as repo
from orchestrator.utils import derive_tag_from_url

logger = logging.getLogger(__name__)

router = APIRouter()


class TaskCreate(BaseModel):
    project_id: str
    title: str
    description: str | None = None
    priority: str = "M"  # H (High), M (Medium), L (Low)
    parent_task_id: str | None = None


class TaskUpdate(BaseModel):
    status: str | None = None
    assigned_session_id: str | None = None
    priority: str | None = None  # H (High), M (Medium), L (Low)
    title: str | None = None
    description: str | None = None
    notes: str | None = None
    links: list[dict] | None = None  # [{url, tag?}]


class TaskLinkAction(BaseModel):
    """For agent-driven link management."""
    action: str  # add, update, delete
    url: str
    tag: str | None = None  # optional free-form tag like "PR", "PRD", etc.


def _get_task_key(t, db) -> str | None:
    """Generate human-readable task key like UTI-1 or UTI-1-1 for subtasks."""
    if t.task_index is None:
        return None

    project = projects_repo.get_project(db, t.project_id)
    if not project or not project.task_prefix:
        return None

    if t.parent_task_id:
        # Subtask: get parent's index
        parent = repo.get_task(db, t.parent_task_id)
        if parent and parent.task_index is not None:
            return f"{project.task_prefix}-{parent.task_index}-{t.task_index}"

    return f"{project.task_prefix}-{t.task_index}"


def _serialize_task(t, include_subtask_stats: bool = False, db=None) -> dict:
    result = {
        "id": t.id, "project_id": t.project_id, "title": t.title,
        "description": t.description, "status": t.status,
        "priority": t.priority, "assigned_session_id": t.assigned_session_id,
        "parent_task_id": t.parent_task_id, "notes": t.notes,
        "links": t.links_list,
        "task_index": t.task_index,
        "task_key": _get_task_key(t, db) if db else None,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }
    if include_subtask_stats and db is not None:
        subtasks = repo.list_tasks(db, parent_task_id=t.id)
        result["subtask_stats"] = {
            "total": len(subtasks),
            "done": sum(1 for s in subtasks if s.status == "done"),
            "in_progress": sum(1 for s in subtasks if s.status == "in_progress"),
        }
    return result


@router.get("/tasks")
def list_tasks(
    project_id: str | None = None,
    status: str | None = None,
    exclude_status: str | None = None,
    assigned_session_id: str | None = None,
    parent_task_id: str | None = None,
    include_subtask_stats: bool = True,
    db=Depends(get_db),
):
    # Parse comma-separated status values
    status_list = status.split(",") if status else None
    exclude_list = exclude_status.split(",") if exclude_status else None

    kwargs = dict(
        project_id=project_id,
        status=status_list,
        exclude_status=exclude_list,
        assigned_session_id=assigned_session_id,
    )
    if parent_task_id is not None:
        kwargs["parent_task_id"] = parent_task_id
    tasks = repo.list_tasks(db, **kwargs)
    return [_serialize_task(t, include_subtask_stats=include_subtask_stats, db=db) for t in tasks]


@router.get("/tasks/{task_id}")
def get_task(task_id: str, db=Depends(get_db)):
    t = repo.get_task(db, task_id)
    if t is None:
        raise HTTPException(404, "Task not found")
    return _serialize_task(t)


@router.get("/tasks/{task_id}/subtasks")
def list_subtasks(task_id: str, db=Depends(get_db)):
    t = repo.get_task(db, task_id)
    if t is None:
        raise HTTPException(404, "Task not found")
    subtasks = repo.list_tasks(db, parent_task_id=task_id)
    return [_serialize_task(s) for s in subtasks]


@router.post("/tasks", status_code=201)
def create_task(body: TaskCreate, db=Depends(get_db)):
    # If creating a subtask, inherit project_id from parent
    project_id = body.project_id
    if body.parent_task_id:
        parent = repo.get_task(db, body.parent_task_id)
        if parent is None:
            raise HTTPException(404, "Parent task not found")
        if not project_id:
            project_id = parent.project_id
    t = repo.create_task(
        db, project_id, body.title, body.description, body.priority,
        parent_task_id=body.parent_task_id,
    )
    return _serialize_task(t)


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, body: TaskUpdate, request: Request, db=Depends(get_db)):
    t = repo.get_task(db, task_id)
    if t is None:
        raise HTTPException(404, "Task not found")

    old_assigned = t.assigned_session_id

    # Check if assigned_session_id was explicitly set in the request (including to null)
    assigned_session_explicitly_set = "assigned_session_id" in body.model_fields_set
    new_assigned = body.assigned_session_id if assigned_session_explicitly_set else old_assigned

    # Auto-transition: assigning a task moves it to in_progress
    effective_status = body.status
    if new_assigned and new_assigned != old_assigned and t.status == "todo" and not body.status:
        effective_status = "in_progress"

    # Handle links update — auto-derive tags from URLs when not provided
    links_json = None
    if "links" in body.model_fields_set:
        if body.links:
            for link in body.links:
                if not link.get("tag"):
                    derived = derive_tag_from_url(link.get("url", ""))
                    if derived:
                        link["tag"] = derived
            links_json = json.dumps(body.links)
        else:
            links_json = None

    updated = repo.update_task(
        db, task_id,
        status=effective_status,
        assigned_session_id=new_assigned if assigned_session_explicitly_set else ...,
        priority=body.priority,
        title=body.title,
        description=body.description,
        notes=body.notes if "notes" in body.model_fields_set else ...,
        links=links_json if "links" in body.model_fields_set else ...,
    )

    # Notify worker when a task is newly assigned
    if new_assigned and new_assigned != old_assigned:
        _notify_worker_of_assignment(db, updated, request)

    return _serialize_task(updated)


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

        # Keep the notification concise — the worker's system prompt
        # instructs it to gather full context via CLI commands.
        message = f"Task assigned: {task.title}. Follow your workflow to review the task and get started."
        send_to_session(session.name, message, tmux_session)
        logger.info("Notified worker %s of task assignment: %s", session.name, task.title)
    except Exception:
        logger.exception("Failed to notify worker of task assignment")


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, db=Depends(get_db)):
    task = repo.get_task(db, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")

    # Collect all session IDs to unassign (this task + subtasks)
    sessions_to_unassign = set()

    def collect_assigned_sessions(tid: str):
        t = repo.get_task(db, tid)
        if t and t.assigned_session_id:
            sessions_to_unassign.add(t.assigned_session_id)
        # Check subtasks recursively
        subtasks = repo.list_tasks(db, parent_task_id=tid)
        for st in subtasks:
            collect_assigned_sessions(st.id)

    collect_assigned_sessions(task_id)

    # Unassign workers and set them to idle (keep them alive for reuse)
    for session_id in sessions_to_unassign:
        try:
            sessions_repo.update_session(
                db, session_id,
                status="idle",
            )
            logger.info("Unassigned worker session %s (now idle) for deleted task %s", session_id, task_id)
        except Exception:
            logger.warning("Could not unassign worker session %s", session_id, exc_info=True)

    # Now delete the task (and subtasks via cascading delete)
    repo.delete_task(db, task_id)
    return {"ok": True, "unassigned_sessions": list(sessions_to_unassign)}


@router.post("/tasks/{task_id}/links")
def manage_task_link(task_id: str, body: TaskLinkAction, db=Depends(get_db)):
    """Agent-driven link management: add, update, or delete links."""
    t = repo.get_task(db, task_id)
    if t is None:
        raise HTTPException(404, "Task not found")

    links = t.links_list.copy()

    if body.action == "add":
        # Check if link already exists
        existing = next((l for l in links if l.get("url") == body.url), None)
        if existing:
            raise HTTPException(400, f"Link already exists: {body.url}")
        tag = body.tag or derive_tag_from_url(body.url)
        new_link: dict = {"url": body.url}
        if tag:
            new_link["tag"] = tag
        links.append(new_link)
    elif body.action == "update":
        existing = next((l for l in links if l.get("url") == body.url), None)
        if not existing:
            raise HTTPException(404, f"Link not found: {body.url}")
        if body.title:
            existing["title"] = body.title
        if body.link_type:
            existing["type"] = body.link_type
    elif body.action == "delete":
        original_len = len(links)
        links = [l for l in links if l.get("url") != body.url]
        if len(links) == original_len:
            raise HTTPException(404, f"Link not found: {body.url}")
    else:
        raise HTTPException(400, f"Invalid action: {body.action}")

    updated = repo.update_task(db, task_id, links=json.dumps(links))
    return _serialize_task(updated)
