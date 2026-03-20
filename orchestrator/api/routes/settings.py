"""Settings CRUD — read/write config keys."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.config_defaults import SETTING_DEFAULTS
from orchestrator.state.repositories import config as config_repo

router = APIRouter()


@router.get("/settings")
def get_settings(category: str | None = None, db=Depends(get_db)):
    configs = config_repo.list_config(db, category=category)
    db_keys = {c.key for c in configs}
    entries = [
        {
            "key": c.key,
            "value": c.parsed_value,
            "description": c.description,
            "category": c.category,
            "updated_at": c.updated_at,
        }
        for c in configs
    ]

    # Merge defaults for keys not in DB
    for key, default_value in SETTING_DEFAULTS.items():
        if key in db_keys:
            continue
        cat = key.rsplit(".", 1)[0] if "." in key else "general"
        if category and cat != category:
            continue
        entries.append(
            {
                "key": key,
                "value": default_value,
                "description": None,
                "category": cat,
                "updated_at": "",
            }
        )

    return entries


class SettingsUpdate(BaseModel):
    settings: dict[str, object]


@router.put("/settings")
def update_settings(body: SettingsUpdate, db=Depends(get_db)):
    updated = []
    for key, value in body.settings.items():
        # Infer category from key prefix (e.g., "auto_approve.tool_calls" → "auto_approve")
        category = key.split(".")[0] if "." in key else "general"
        config_repo.set_config(db, key, value, category=category)
        updated.append(key)
    return {"ok": True, "updated": updated}
