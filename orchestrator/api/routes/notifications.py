"""Notifications CRUD API."""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import notifications as repo

router = APIRouter()


class NotificationCreate(BaseModel):
    message: str
    task_id: str | None = None
    session_id: str | None = None
    notification_type: str = "info"
    link_url: str | None = None
    metadata: dict | None = None


class DismissAllRequest(BaseModel):
    task_id: str | None = None
    session_id: str | None = None


class BatchDeleteRequest(BaseModel):
    ids: list[str]


def _serialize(n):
    metadata = None
    if n.metadata:
        try:
            metadata = json.loads(n.metadata)
        except (json.JSONDecodeError, TypeError):
            metadata = None
    return {
        "id": n.id,
        "task_id": n.task_id,
        "session_id": n.session_id,
        "message": n.message,
        "notification_type": n.notification_type,
        "link_url": n.link_url,
        "metadata": metadata,
        "created_at": n.created_at,
        "dismissed": n.dismissed,
        "dismissed_at": n.dismissed_at,
    }


@router.get("/notifications")
def list_notifications(
    task_id: str | None = None,
    session_id: str | None = None,
    dismissed: bool | None = None,
    limit: int | None = None,
    db=Depends(get_db),
):
    """List notifications with optional filters."""
    items = repo.list_notifications(
        db,
        task_id=task_id,
        session_id=session_id,
        dismissed=dismissed,
        limit=limit,
    )
    return [_serialize(n) for n in items]


@router.get("/notifications/count")
def count_notifications(
    task_id: str | None = None,
    days: int | None = 7,
    db=Depends(get_db),
):
    """Get count of active (non-dismissed) notifications.

    Args:
        days: Only count notifications from the past N days. Default 7.
              Pass days=0 to count all.
    """
    if task_id:
        count = repo.count_notifications_for_task(db, task_id)
    else:
        since_days = days if days and days > 0 else None
        count = repo.count_active_notifications(db, since_days=since_days)
    return {"count": count}


@router.get("/notifications/{notification_id}")
def get_notification(notification_id: str, db=Depends(get_db)):
    n = repo.get_notification(db, notification_id)
    if n is None:
        raise HTTPException(404, "Notification not found")
    return _serialize(n)


@router.post("/notifications", status_code=201)
def create_notification(body: NotificationCreate, db=Depends(get_db)):
    try:
        n = repo.create_notification(
            db,
            message=body.message,
            task_id=body.task_id,
            session_id=body.session_id,
            notification_type=body.notification_type,
            link_url=body.link_url,
            metadata=json.dumps(body.metadata) if body.metadata else None,
        )
        return _serialize(n)
    except Exception as e:
        # Foreign key constraint failure - session_id or task_id doesn't exist
        if "FOREIGN KEY constraint failed" in str(e):
            raise HTTPException(400, f"Invalid task_id or session_id: {e}")
        raise


@router.post("/notifications/{notification_id}/dismiss")
def dismiss_notification(notification_id: str, db=Depends(get_db)):
    n = repo.get_notification(db, notification_id)
    if n is None:
        raise HTTPException(404, "Notification not found")
    updated = repo.dismiss_notification(db, notification_id)
    return _serialize(updated)


@router.post("/notifications/dismiss-all")
def dismiss_all_notifications(body: DismissAllRequest | None = None, db=Depends(get_db)):
    """Dismiss all notifications, optionally filtered by task_id or session_id."""
    task_id = body.task_id if body else None
    session_id = body.session_id if body else None
    count = repo.dismiss_all_notifications(db, task_id=task_id, session_id=session_id)
    return {"dismissed": count}


@router.delete("/notifications/batch")
def batch_delete_notifications(body: BatchDeleteRequest, db=Depends(get_db)):
    """Delete multiple notifications by IDs."""
    count = repo.delete_notifications_by_ids(db, body.ids)
    return {"deleted": count}


@router.delete("/notifications/{notification_id}")
def delete_notification(notification_id: str, db=Depends(get_db)):
    if not repo.delete_notification(db, notification_id):
        raise HTTPException(404, "Notification not found")
    return {"ok": True}


@router.post("/notifications/{notification_id}/undismiss")
def undismiss_notification(notification_id: str, db=Depends(get_db)):
    """Restore a dismissed notification back to active."""
    n = repo.get_notification(db, notification_id)
    if n is None:
        raise HTTPException(404, "Notification not found")
    updated = repo.undismiss_notification(db, notification_id)
    return _serialize(updated)


@router.delete("/notifications/dismissed/all")
def delete_all_dismissed(db=Depends(get_db)):
    """Permanently delete all dismissed notifications."""
    count = repo.delete_dismissed_notifications(db)
    return {"deleted": count}
