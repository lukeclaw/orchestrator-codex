"""Context items CRUD API."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import context as repo

router = APIRouter()


class ContextCreate(BaseModel):
    title: str
    content: str
    scope: str = "global"
    project_id: str | None = None
    category: str | None = None
    source: str | None = None
    metadata: str | None = None


class ContextUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    scope: str | None = None
    project_id: str | None = None
    category: str | None = None
    source: str | None = None
    metadata: str | None = None


def _serialize(c):
    return {
        "id": c.id,
        "scope": c.scope,
        "project_id": c.project_id,
        "title": c.title,
        "content": c.content,
        "category": c.category,
        "source": c.source,
        "metadata": c.metadata,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


@router.get("/context")
def list_context(
    scope: str | None = None,
    project_id: str | None = None,
    category: str | None = None,
    search: str | None = None,
    db=Depends(get_db),
):
    items = repo.list_context(db, scope=scope, project_id=project_id, category=category, search=search)
    return [_serialize(c) for c in items]


@router.get("/context/{item_id}")
def get_context_item(item_id: str, db=Depends(get_db)):
    c = repo.get_context_item(db, item_id)
    if c is None:
        raise HTTPException(404, "Context item not found")
    return _serialize(c)


@router.post("/context", status_code=201)
def create_context_item(body: ContextCreate, db=Depends(get_db)):
    c = repo.create_context_item(
        db,
        title=body.title,
        content=body.content,
        scope=body.scope,
        project_id=body.project_id,
        category=body.category,
        source=body.source,
        metadata=body.metadata,
    )
    return _serialize(c)


@router.patch("/context/{item_id}")
def update_context_item(item_id: str, body: ContextUpdate, db=Depends(get_db)):
    existing = repo.get_context_item(db, item_id)
    if existing is None:
        raise HTTPException(404, "Context item not found")

    kwargs = {}
    data = body.model_dump(exclude_unset=True)
    for field in ("title", "content", "scope", "project_id", "category", "source", "metadata"):
        if field in data:
            kwargs[field] = data[field]

    updated = repo.update_context_item(db, item_id, **kwargs)
    return _serialize(updated)


@router.delete("/context/{item_id}")
def delete_context_item(item_id: str, db=Depends(get_db)):
    if not repo.delete_context_item(db, item_id):
        raise HTTPException(404, "Context item not found")
    return {"ok": True}
