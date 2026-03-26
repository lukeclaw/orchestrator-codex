"""Context items CRUD API."""

import re
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.providers import get_provider
from orchestrator.state.repositories import context as repo

router = APIRouter()

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ContextCreate(BaseModel):
    title: str
    content: str
    description: str | None = None
    scope: str = "global"
    provider: str | None = None
    project_id: str | None = None
    category: str | None = None
    source: str | None = None
    metadata: str | None = None


class ContextUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    description: str | None = None
    scope: str | None = None
    provider: str | None = None
    project_id: str | None = None
    category: str | None = None
    source: str | None = None
    metadata: str | None = None


def _validate_provider(provider: str | None) -> str | None:
    if provider is None:
        return None
    try:
        get_provider(provider)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc
    return provider


def _resolve_worker_uuid(source: str | None, db: sqlite3.Connection) -> str | None:
    """Resolve 'worker:<uuid>' to 'worker:<name>' for DB storage."""
    if not source or not source.startswith("worker:"):
        return source
    ref = source[len("worker:") :]
    if not _UUID_RE.match(ref):
        return source  # already a name
    row = db.execute("SELECT name FROM sessions WHERE id = ?", (ref,)).fetchone()
    if row:
        return f"worker:{row['name']}"
    return source


def _display_source(source: str | None, db: sqlite3.Connection) -> str | None:
    """Convert stored source to a display-friendly string.

    'worker:<uuid>' → 'worker:<name>' (resolved from sessions table),
    everything else passes through unchanged.
    """
    if not source or not source.startswith("worker:"):
        return source
    ref = source[len("worker:") :]
    if not _UUID_RE.match(ref):
        return source  # already worker:<name>
    row = db.execute("SELECT name FROM sessions WHERE id = ?", (ref,)).fetchone()
    if row:
        return f"worker:{row['name']}"
    return source  # deleted session, show raw value


def _serialize(c, db: sqlite3.Connection, include_content: bool = True):
    """Serialize context item. Set include_content=False for list views."""
    result = {
        "id": c.id,
        "scope": c.scope,
        "provider": c.provider,
        "project_id": c.project_id,
        "title": c.title,
        "description": c.description,
        "category": c.category,
        "source": _display_source(c.source, db),
        "metadata": c.metadata,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }
    if include_content:
        result["content"] = c.content
    return result


@router.get("/context")
def list_context(
    scope: str | None = None,
    provider: str | None = None,
    project_id: str | None = None,
    category: str | None = None,
    search: str | None = None,
    include_content: bool = False,
    include_shared: bool = True,
    db=Depends(get_db),
):
    """List context items. By default returns only title/description (no content).
    Set include_content=true to get full content (for backward compatibility or specific needs).
    """
    items = repo.list_context(
        db,
        scope=scope,
        provider=_validate_provider(provider),
        project_id=project_id,
        category=category,
        search=search,
        include_shared=include_shared,
    )
    return [_serialize(c, db, include_content=include_content) for c in items]


@router.get("/context/{item_id}")
def get_context_item(item_id: str, db=Depends(get_db)):
    c = repo.get_context_item(db, item_id)
    if c is None:
        raise HTTPException(404, "Context item not found")
    return _serialize(c, db)


@router.post("/context", status_code=201)
def create_context_item(body: ContextCreate, db=Depends(get_db)):
    # Resolve worker:<uuid> to worker:<name> at creation time so the name
    # persists even after the worker session is deleted.
    source = _resolve_worker_uuid(body.source, db)
    c = repo.create_context_item(
        db,
        title=body.title,
        content=body.content,
        description=body.description,
        scope=body.scope,
        provider=_validate_provider(body.provider),
        project_id=body.project_id,
        category=body.category,
        source=source,
        metadata=body.metadata,
    )
    return _serialize(c, db)


@router.patch("/context/{item_id}")
def update_context_item(item_id: str, body: ContextUpdate, db=Depends(get_db)):
    existing = repo.get_context_item(db, item_id)
    if existing is None:
        raise HTTPException(404, "Context item not found")

    kwargs = {}
    data = body.model_dump(exclude_unset=True)
    for field in (
        "title",
        "content",
        "description",
        "scope",
        "provider",
        "project_id",
        "category",
        "source",
        "metadata",
    ):
        if field in data:
            kwargs[field] = _validate_provider(data[field]) if field == "provider" else data[field]

    updated = repo.update_context_item(db, item_id, **kwargs)
    return _serialize(updated, db)


@router.delete("/context/{item_id}")
def delete_context_item(item_id: str, db=Depends(get_db)):
    if not repo.delete_context_item(db, item_id):
        raise HTTPException(404, "Context item not found")
    return {"ok": True}
