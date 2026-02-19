"""Backup API — trigger backups, manage settings, list snapshots."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.backup import list_backups, run_backup
from orchestrator.state.repositories import config as config_repo
from orchestrator.utils import utc_now_iso

router = APIRouter()

# Config key constants
_KEY_DIR = "backup.directory"
_KEY_PASSWORD = "backup.password"
_KEY_RETENTION = "backup.retention_count"
_KEY_LAST_RUN = "backup.last_run"
_KEY_LAST_STATUS = "backup.last_status"

_DEFAULT_RETENTION = 5


def _get_db_path(request: Request) -> str | None:
    """Resolve the live database path from app state."""
    return getattr(request.app.state, "db_path", None)


@router.post("/backup/run")
def trigger_backup(request: Request, db=Depends(get_db)):
    """Trigger a backup now."""
    db_path = _get_db_path(request)
    if not db_path:
        return {"ok": False, "error": "Database path not configured (in-memory DB?)"}

    backup_dir = config_repo.get_config_value(db, _KEY_DIR)
    if not backup_dir:
        return {"ok": False, "error": "Backup directory not configured. Set it via PUT /api/backup/settings."}

    password = config_repo.get_config_value(db, _KEY_PASSWORD)
    if not password:
        return {"ok": False, "error": "Backup password not configured. Set it via PUT /api/backup/settings."}

    retention = config_repo.get_config_value(db, _KEY_RETENTION, _DEFAULT_RETENTION)

    result = run_backup(
        db_path=db_path,
        password=password,
        backup_dir=backup_dir,
        retention=retention,
    )

    # Persist last run info
    config_repo.set_config(db, _KEY_LAST_RUN, utc_now_iso(), category="backup")
    status = "success" if result["ok"] else f"error: {result['error']}"
    config_repo.set_config(db, _KEY_LAST_STATUS, status, category="backup")

    return result


@router.get("/backup/settings")
def get_backup_settings(db=Depends(get_db)):
    """Get backup configuration."""
    backup_dir = config_repo.get_config_value(db, _KEY_DIR)
    has_password = config_repo.get_config_value(db, _KEY_PASSWORD) is not None
    retention = config_repo.get_config_value(db, _KEY_RETENTION, _DEFAULT_RETENTION)
    last_run = config_repo.get_config_value(db, _KEY_LAST_RUN)
    last_status = config_repo.get_config_value(db, _KEY_LAST_STATUS)

    return {
        "directory": backup_dir,
        "has_password": has_password,
        "retention_count": retention,
        "last_run": last_run,
        "last_status": last_status,
    }


class BackupSettingsUpdate(BaseModel):
    directory: str | None = None
    password: str | None = None
    retention_count: int | None = None


@router.put("/backup/settings")
def update_backup_settings(body: BackupSettingsUpdate, db=Depends(get_db)):
    """Update backup configuration."""
    updated = []

    if body.directory is not None:
        config_repo.set_config(db, _KEY_DIR, body.directory, category="backup")
        updated.append("directory")

    if body.password is not None:
        config_repo.set_config(db, _KEY_PASSWORD, body.password, category="backup")
        updated.append("password")

    if body.retention_count is not None:
        config_repo.set_config(db, _KEY_RETENTION, body.retention_count, category="backup")
        updated.append("retention_count")

    return {"ok": True, "updated": updated}


@router.get("/backup/list")
def get_backup_list(db=Depends(get_db)):
    """List available backup files."""
    backup_dir = config_repo.get_config_value(db, _KEY_DIR)
    if not backup_dir:
        return {"backups": [], "error": "Backup directory not configured."}

    backups = list_backups(backup_dir)
    return {"backups": backups}
