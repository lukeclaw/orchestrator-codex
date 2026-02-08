"""Project CRUD + task management."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import projects as repo

router = APIRouter()


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    target_date: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    target_date: str | None = None


@router.get("/projects")
def list_projects(status: str | None = None, db=Depends(get_db)):
    projects = repo.list_projects(db, status=status)
    return [
        {
            "id": p.id, "name": p.name, "description": p.description,
            "status": p.status, "target_date": p.target_date,
            "created_at": p.created_at,
        }
        for p in projects
    ]


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
    p = repo.create_project(db, body.name, body.description, body.target_date)
    return {"id": p.id, "name": p.name, "status": p.status}


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
    if not repo.delete_project(db, project_id):
        raise HTTPException(404, "Project not found")
    return {"ok": True}
