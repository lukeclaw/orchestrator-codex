"""Skills CRUD API — manages custom skills and lists built-in skills."""

import os
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.agents.deploy import get_brain_skills_dir, get_worker_skills_dir
from orchestrator.state.repositories import skills as repo

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class SkillCreate(BaseModel):
    name: str
    target: str = "worker"
    content: str = ""
    description: str | None = None


class SkillUpdate(BaseModel):
    name: str | None = None
    target: str | None = None
    content: str | None = None
    description: str | None = None


# ---------------------------------------------------------------------------
# Built-in skill helpers
# ---------------------------------------------------------------------------

def _parse_skill_file(path: str) -> dict:
    """Parse a skill markdown file, extracting YAML frontmatter."""
    with open(path) as f:
        text = f.read()
    if text.startswith('---'):
        parts = text.split('---\n', 2)
        if len(parts) >= 3:
            meta = yaml.safe_load(parts[1]) or {}
            body = parts[2]
            return {
                "name": meta.get("name", Path(path).stem),
                "description": meta.get("description", ""),
                "content": body,
            }
    return {"name": Path(path).stem, "description": "", "content": text}


def _list_builtin_skills(target: str | None = None) -> list[dict]:
    """List built-in skills from the filesystem."""
    results = []
    targets = [target] if target else ["brain", "worker"]

    for t in targets:
        if t == "brain":
            skills_dir = get_brain_skills_dir()
        else:
            skills_dir = get_worker_skills_dir()

        if not skills_dir or not os.path.isdir(skills_dir):
            continue

        for filename in sorted(os.listdir(skills_dir)):
            if not filename.endswith(".md"):
                continue
            filepath = os.path.join(skills_dir, filename)
            parsed = _parse_skill_file(filepath)
            stat = os.stat(filepath)
            content = parsed["content"]
            line_count = content.count('\n') + (1 if content and not content.endswith('\n') else 0)

            from datetime import datetime
            mtime = datetime.fromtimestamp(stat.st_mtime).isoformat()

            results.append({
                "id": f"builtin:{t}:{parsed['name']}",
                "name": parsed["name"],
                "target": t,
                "type": "built_in",
                "description": parsed["description"],
                "content": None,  # Not included in list view
                "line_count": line_count,
                "created_at": mtime,
                "updated_at": mtime,
            })

    return results


def _serialize(skill, include_content: bool = True) -> dict:
    """Serialize a custom skill from DB."""
    content = skill.content if include_content else None
    line_count = skill.content.count('\n') + (1 if skill.content and not skill.content.endswith('\n') else 0)
    return {
        "id": skill.id,
        "name": skill.name,
        "target": skill.target,
        "type": "custom",
        "description": skill.description,
        "content": content,
        "line_count": line_count,
        "created_at": skill.created_at,
        "updated_at": skill.updated_at,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/skills")
def list_skills(
    target: str | None = None,
    search: str | None = None,
    db=Depends(get_db),
):
    """List all skills (built-in + custom). Content is excluded from list view."""
    # Built-in skills from filesystem
    builtin = _list_builtin_skills(target)

    # Filter built-in by search if provided
    if search:
        search_lower = search.lower()
        builtin = [
            s for s in builtin
            if search_lower in s["name"].lower()
            or (s["description"] and search_lower in s["description"].lower())
        ]

    # Custom skills from DB
    custom = repo.list_skills(db, target=target, search=search)
    custom_serialized = [_serialize(s, include_content=False) for s in custom]

    # Built-in first, then custom sorted by updated_at desc
    return builtin + custom_serialized


@router.get("/skills/builtin/{target}/{name}")
def get_builtin_skill(target: str, name: str):
    """Get a built-in skill with full content."""
    if target == "brain":
        skills_dir = get_brain_skills_dir()
    elif target == "worker":
        skills_dir = get_worker_skills_dir()
    else:
        raise HTTPException(400, "Target must be 'brain' or 'worker'")

    if not skills_dir:
        raise HTTPException(404, "Skills directory not found")

    filepath = os.path.join(skills_dir, f"{name}.md")
    if not os.path.exists(filepath):
        raise HTTPException(404, f"Built-in skill '{name}' not found")

    parsed = _parse_skill_file(filepath)
    stat = os.stat(filepath)
    content = parsed["content"]
    line_count = content.count('\n') + (1 if content and not content.endswith('\n') else 0)

    from datetime import datetime
    mtime = datetime.fromtimestamp(stat.st_mtime).isoformat()

    return {
        "id": f"builtin:{target}:{parsed['name']}",
        "name": parsed["name"],
        "target": target,
        "type": "built_in",
        "description": parsed["description"],
        "content": content,
        "line_count": line_count,
        "created_at": mtime,
        "updated_at": mtime,
    }


@router.get("/skills/{skill_id}")
def get_skill(skill_id: str, db=Depends(get_db)):
    """Get a single custom skill with full content."""
    skill = repo.get_skill(db, skill_id)
    if skill is None:
        raise HTTPException(404, "Skill not found")
    return _serialize(skill)


@router.post("/skills", status_code=201)
def create_skill(body: SkillCreate, db=Depends(get_db)):
    """Create a custom skill."""
    try:
        skill = repo.create_skill(
            db,
            name=body.name,
            target=body.target,
            content=body.content,
            description=body.description,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Publish event
    from orchestrator.core.events import Event, publish
    publish(Event(type="skill.changed", data={"action": "created", "name": body.name, "target": body.target}))

    return _serialize(skill)


@router.patch("/skills/{skill_id}")
def update_skill(skill_id: str, body: SkillUpdate, db=Depends(get_db)):
    """Update a custom skill."""
    existing = repo.get_skill(db, skill_id)
    if existing is None:
        raise HTTPException(404, "Skill not found")

    kwargs = {}
    data = body.model_dump(exclude_unset=True)
    for field in ("name", "target", "content", "description"):
        if field in data:
            kwargs[field] = data[field]

    try:
        updated = repo.update_skill(db, skill_id, **kwargs)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Publish event
    from orchestrator.core.events import Event, publish
    publish(Event(type="skill.changed", data={"action": "updated", "name": updated.name, "target": updated.target}))

    return _serialize(updated)


@router.delete("/skills/{skill_id}")
def delete_skill(skill_id: str, db=Depends(get_db)):
    """Delete a custom skill."""
    existing = repo.get_skill(db, skill_id)
    if existing is None:
        raise HTTPException(404, "Skill not found")

    if not repo.delete_skill(db, skill_id):
        raise HTTPException(404, "Skill not found")

    # Publish event
    from orchestrator.core.events import Event, publish
    publish(Event(type="skill.changed", data={"action": "deleted", "name": existing.name, "target": existing.target}))

    return {"ok": True}
