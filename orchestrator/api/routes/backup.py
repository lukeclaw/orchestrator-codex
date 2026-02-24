"""Backup API — trigger backups, manage settings, list snapshots, restore, schedule."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.backup import list_backups, restore_backup, run_backup
from orchestrator.core.events import Event, publish
from orchestrator.state.db import get_connection
from orchestrator.state.repositories import config as config_repo
from orchestrator.utils import utc_now_iso

logger = logging.getLogger(__name__)

router = APIRouter()

# Config key constants
_KEY_DIR = "backup.directory"
_KEY_PASSWORD = "backup.password"
_KEY_RETENTION = "backup.retention_count"
_KEY_LAST_RUN = "backup.last_run"
_KEY_LAST_STATUS = "backup.last_status"
_KEY_SCHEDULE_HOURS = "backup.schedule_hours"

_DEFAULT_RETENTION = 5

# Scheduled backup state
_backup_schedule_task: asyncio.Task | None = None
_backup_db_path: str | None = None


def _get_db_path(request: Request) -> str | None:
    """Resolve the live database path from app state."""
    return getattr(request.app.state, "db_path", None)


# ---------------------------------------------------------------------------
# Scheduled backup loop
# ---------------------------------------------------------------------------

async def _scheduled_backup_loop(db_path: str) -> None:
    """Background loop that runs backups on a configurable schedule.

    Sleeps in 60-second increments for responsive shutdown and config
    change detection.  Each cycle reads the schedule interval and last-run
    timestamp from config via a fresh DB connection.
    """
    logger.info("Scheduled backup loop started (db_path=%s)", db_path)

    while True:
        # Sleep in small increments so cancellation is responsive
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info("Scheduled backup loop cancelled")
            return

        try:
            conn = get_connection(db_path)
            try:
                schedule_hours = config_repo.get_config_value(conn, _KEY_SCHEDULE_HOURS, 0)
                if not schedule_hours or schedule_hours <= 0:
                    continue

                last_run_str = config_repo.get_config_value(conn, _KEY_LAST_RUN)
                if last_run_str:
                    from datetime import datetime
                    try:
                        last_run = datetime.fromisoformat(last_run_str)
                        now = datetime.now(UTC)
                        elapsed_hours = (now - last_run).total_seconds() / 3600
                        if elapsed_hours < schedule_hours:
                            continue
                    except (ValueError, TypeError):
                        pass  # Invalid timestamp — run backup

                # Time to run a backup
                backup_dir = config_repo.get_config_value(conn, _KEY_DIR)
                password = config_repo.get_config_value(conn, _KEY_PASSWORD)
                retention = config_repo.get_config_value(conn, _KEY_RETENTION, _DEFAULT_RETENTION)
            finally:
                conn.close()

            if not backup_dir or not password:
                logger.debug("Scheduled backup skipped: directory or password not configured")
                continue

            publish(Event(type="backup.started", data={"trigger": "scheduled"}))

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, run_backup, db_path, password, backup_dir, retention,
            )

            # Persist last-run info via fresh connection
            conn = get_connection(db_path)
            try:
                config_repo.set_config(conn, _KEY_LAST_RUN, utc_now_iso(), category="backup")
                status = "success" if result["ok"] else f"error: {result['error']}"
                config_repo.set_config(conn, _KEY_LAST_STATUS, status, category="backup")
            finally:
                conn.close()

            if result["ok"]:
                publish(Event(type="backup.completed", data={"trigger": "scheduled", "filename": result["filename"]}))
                logger.info("Scheduled backup completed: %s", result["filename"])
            else:
                publish(Event(type="backup.error", data={"trigger": "scheduled", "error": result["error"]}))
                logger.error("Scheduled backup failed: %s", result["error"])

        except asyncio.CancelledError:
            logger.info("Scheduled backup loop cancelled")
            return
        except Exception:
            logger.exception("Error in scheduled backup loop")


def start_backup_schedule(db_path: str) -> None:
    """Start the scheduled backup background task."""
    global _backup_schedule_task, _backup_db_path
    _backup_db_path = db_path
    if _backup_schedule_task is None or _backup_schedule_task.done():
        _backup_schedule_task = asyncio.create_task(_scheduled_backup_loop(db_path))
        logger.info("Started backup schedule task")


async def stop_backup_schedule() -> None:
    """Stop the scheduled backup background task."""
    global _backup_schedule_task
    if _backup_schedule_task and not _backup_schedule_task.done():
        _backup_schedule_task.cancel()
        try:
            await _backup_schedule_task
        except asyncio.CancelledError:
            pass
        logger.info("Stopped backup schedule task")


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

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
    schedule_hours = config_repo.get_config_value(db, _KEY_SCHEDULE_HOURS, 0)

    return {
        "directory": backup_dir,
        "has_password": has_password,
        "retention_count": retention,
        "last_run": last_run,
        "last_status": last_status,
        "schedule_hours": schedule_hours,
    }


class BackupSettingsUpdate(BaseModel):
    directory: str | None = None
    password: str | None = None
    retention_count: int | None = None
    schedule_hours: int | None = None


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

    if body.schedule_hours is not None:
        config_repo.set_config(db, _KEY_SCHEDULE_HOURS, body.schedule_hours, category="backup")
        updated.append("schedule_hours")

    return {"ok": True, "updated": updated}


@router.get("/backup/list")
def get_backup_list(db=Depends(get_db)):
    """List available backup files."""
    backup_dir = config_repo.get_config_value(db, _KEY_DIR)
    if not backup_dir:
        return {"backups": [], "error": "Backup directory not configured."}

    backups = list_backups(backup_dir)
    return {"backups": backups}


class BackupRestoreRequest(BaseModel):
    filename: str


@router.post("/backup/restore")
async def restore_from_backup(body: BackupRestoreRequest, request: Request):
    """Restore the database from a backup file.

    This endpoint deliberately does NOT use Depends(get_db).  Every open
    SQLite connection must be closed before the database file is replaced,
    otherwise stale file-descriptors cause "disk I/O error" on subsequent
    queries.

    Connections that must be closed:
      - The lifespan connection (app.state.conn)
      - The Orchestrator's read connection (same object, but also captured by
        the monitor_loop and tunnel_health_loop background tasks)

    We therefore:
      1. Read config via a short-lived connection, then close it.
      2. Stop the Orchestrator background tasks (which hold ``conn``).
      3. Stop the scheduled backup loop (reads the DB periodically).
      4. Close the lifespan connection.
      5. Replace the database file (restore_backup).
      6. Re-open the lifespan connection.
      7. Restart the Orchestrator tasks with the new connection.
      8. Restart the scheduled backup loop.
    """
    db_path = _get_db_path(request)
    if not db_path:
        return {"ok": False, "error": "Database path not configured (in-memory DB?)"}

    # 1. Read config via a short-lived connection, then close it
    cfg_conn = get_connection(db_path)
    try:
        backup_dir = config_repo.get_config_value(cfg_conn, _KEY_DIR)
        password = config_repo.get_config_value(cfg_conn, _KEY_PASSWORD)
    finally:
        cfg_conn.close()

    if not backup_dir:
        return {"ok": False, "error": "Backup directory not configured."}
    if not password:
        return {"ok": False, "error": "Backup password not configured."}

    # 2. Stop Orchestrator background tasks (they hold the lifespan conn)
    orch = getattr(request.app.state, "orchestrator", None)
    if orch:
        await orch.stop()

    # 3. Stop the scheduled backup loop
    await stop_backup_schedule()

    # 4. Close the lifespan DB connection
    lifespan_conn = getattr(request.app.state, "conn", None)
    if lifespan_conn:
        try:
            lifespan_conn.close()
        except Exception:
            logger.warning("Failed to close lifespan connection before restore", exc_info=True)

    # 5. Replace the database file — no open handles at this point
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, restore_backup, body.filename, backup_dir, password, db_path,
    )

    # 6. Re-open the lifespan connection on the restored database
    try:
        new_conn = get_connection(db_path)
        request.app.state.conn = new_conn
    except Exception:
        logger.exception("Failed to re-open DB connection after restore")
        new_conn = None

    # 7. Restart Orchestrator with the new connection
    if orch and new_conn:
        await orch.replace_connection(new_conn)

    # 8. Restart the scheduled backup loop
    if db_path:
        start_backup_schedule(db_path)

    if result["ok"]:
        publish(Event(type="backup.restored", data={"filename": body.filename}))

    return result
