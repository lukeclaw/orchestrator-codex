"""Rdev management: list, create, delete, restart, stop."""

import asyncio
import logging
import subprocess
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import sessions as repo

logger = logging.getLogger(__name__)

router = APIRouter()

# Cache for rdev list (1 hour TTL, refreshed every 30 min in background)
_rdev_cache: dict[str, Any] = {"data": [], "timestamp": 0}
RDEV_CACHE_TTL = 3600  # 1 hour in seconds
RDEV_BACKGROUND_REFRESH_INTERVAL = 1800  # 30 minutes in seconds

# In-memory store for create-job results so the frontend can poll for errors.
# Keys are job IDs (str); values are dicts with status/error/name.
# Entries are cleaned up after 10 minutes.
_create_jobs: dict[str, dict[str, Any]] = {}
_CREATE_JOB_TTL = 600  # 10 minutes

# Background task handle
_background_task: asyncio.Task | None = None


def refresh_rdev_cache() -> None:
    """Refresh the rdev cache synchronously."""
    global _rdev_cache
    try:
        rdevs = _fetch_rdev_list()
        _rdev_cache["data"] = rdevs
        _rdev_cache["timestamp"] = time.time()
        logger.info("Background refresh: updated rdev cache (%d instances)", len(rdevs))
    except Exception:
        logger.warning("Background refresh: failed to update rdev cache", exc_info=True)


async def _background_refresh_loop() -> None:
    """Background loop that refreshes rdev cache every 30 minutes.

    Does an initial refresh on startup, then refreshes every 30 minutes.
    """
    # Initial refresh on startup
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, refresh_rdev_cache)
        logger.info("Initial rdev cache refresh completed")
    except Exception:
        logger.warning("Initial rdev cache refresh failed", exc_info=True)

    # Then refresh every 30 minutes
    while True:
        await asyncio.sleep(RDEV_BACKGROUND_REFRESH_INTERVAL)
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, refresh_rdev_cache)
        except asyncio.CancelledError:
            logger.info("Rdev background refresh task cancelled")
            break
        except Exception:
            logger.warning("Rdev background refresh error", exc_info=True)


def start_background_refresh() -> None:
    """Start the background refresh task."""
    global _background_task
    if _background_task is None or _background_task.done():
        _background_task = asyncio.create_task(_background_refresh_loop())
        logger.info(
            "Started rdev background refresh task (interval: %ds)", RDEV_BACKGROUND_REFRESH_INTERVAL
        )


async def stop_background_refresh() -> None:
    """Stop the background refresh task."""
    global _background_task
    if _background_task and not _background_task.done():
        _background_task.cancel()
        try:
            await _background_task
        except asyncio.CancelledError:
            pass
        logger.info("Stopped rdev background refresh task")


class RdevCreate(BaseModel):
    mp_name: str
    rdev_name: str | None = None
    branch: str | None = None
    flavor: str | None = None


def _fetch_rdev_list() -> list[dict]:
    """Fetch rdev list from CLI command."""
    rdevs = []
    try:
        result = subprocess.run(
            ["rdev", "list"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout

        # Parse the table output
        # Format: Name | State | Cluster Name | Created | Last Accessed | Server URL
        lines = output.strip().split("\n")
        for line in lines:
            # Skip header, separator lines, and info messages
            if "|" not in line or line.startswith("-") or "Name" in line and "State" in line:
                continue

            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2:
                name = parts[0].strip()
                state = parts[1].strip() if len(parts) > 1 else ""

                # Skip empty or invalid entries
                if not name or "/" not in name:
                    continue

                rdevs.append(
                    {
                        "name": name,
                        "state": state,
                        "cluster": parts[2].strip() if len(parts) > 2 else "",
                        "created": parts[3].strip() if len(parts) > 3 else "",
                        "last_accessed": parts[4].strip() if len(parts) > 4 else "",
                    }
                )
    except subprocess.TimeoutExpired:
        logger.warning("rdev list command timed out")
    except FileNotFoundError:
        logger.warning("rdev command not found")
    except Exception:
        logger.warning("Failed to run rdev list", exc_info=True)

    return rdevs


@router.get("/rdevs")
def list_rdevs(refresh: bool = False, db=Depends(get_db)):
    """List available rdev instances and show which ones have workers assigned.

    Uses server-side cache with 1 hour TTL. Pass refresh=true to force refresh.
    """
    global _rdev_cache

    now = time.time()
    cache_age = now - _rdev_cache["timestamp"]

    # Use cache if valid and not forcing refresh
    if not refresh and cache_age < RDEV_CACHE_TTL and _rdev_cache["data"]:
        rdevs = _rdev_cache["data"]
        logger.debug("Using cached rdev list (age: %.0fs)", cache_age)
    else:
        # Fetch fresh data
        rdevs = _fetch_rdev_list()
        _rdev_cache["data"] = rdevs
        _rdev_cache["timestamp"] = now
        logger.info("Refreshed rdev list cache (%d instances)", len(rdevs))

    # Always check current session state for in_use status
    sessions = repo.list_sessions(db)
    used_hosts = {s.host for s in sessions}

    # Return copies with in_use status and worker info (don't modify cache)
    result = []
    for rdev in rdevs:
        item = dict(rdev)
        item["in_use"] = rdev["name"] in used_hosts
        # Find the worker name, status, and id if in use
        for s in sessions:
            if s.host == rdev["name"]:
                item["worker_name"] = s.name
                item["worker_status"] = s.status
                item["worker_id"] = s.id
                break
        result.append(item)

    return result


def _purge_old_jobs() -> None:
    """Remove create-job entries older than _CREATE_JOB_TTL."""
    now = time.time()
    expired = [
        jid for jid, j in _create_jobs.items() if now - j.get("updated", 0) > _CREATE_JOB_TTL
    ]
    for jid in expired:
        _create_jobs.pop(jid, None)


def _run_create_rdev(cmd: list[str], rdev_full_name: str, job_id: str) -> None:
    """Run rdev create in a background thread. Logs result and invalidates cache."""
    global _rdev_cache
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # 2 minute timeout for create
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            logger.error("rdev create failed: %s", error_msg)
            _create_jobs[job_id] = {
                "status": "failed",
                "error": error_msg,
                "name": rdev_full_name,
                "updated": time.time(),
            }
        else:
            created_name = result.stdout.strip() or rdev_full_name
            logger.info("Created rdev: %s", created_name)
            _create_jobs[job_id] = {"status": "done", "name": created_name, "updated": time.time()}

    except subprocess.TimeoutExpired:
        logger.error("rdev create timed out for %s", rdev_full_name)
        _create_jobs[job_id] = {
            "status": "failed",
            "error": "rdev create timed out (>120s)",
            "name": rdev_full_name,
            "updated": time.time(),
        }
    except Exception as exc:
        logger.exception("rdev create failed for %s", rdev_full_name)
        _create_jobs[job_id] = {
            "status": "failed",
            "error": str(exc),
            "name": rdev_full_name,
            "updated": time.time(),
        }
    finally:
        # Invalidate cache so next list refresh picks up the new rdev
        _rdev_cache["timestamp"] = 0


@router.post("/rdevs", status_code=202)
async def create_rdev(body: RdevCreate):
    """Create a new rdev instance.

    Kicks off `rdev create` in the background and returns immediately.
    The rdev will appear in the list once creation completes (~30-120s).
    Returns a job_id that can be polled via GET /rdevs/jobs/{job_id}.
    """
    # Build rdev name
    if body.rdev_name:
        rdev_full_name = f"{body.mp_name}/{body.rdev_name}"
    else:
        rdev_full_name = body.mp_name

    cmd = ["rdev", "create", rdev_full_name, "-s"]  # -s for silent (only output name)

    if body.branch:
        cmd.extend(["--branch", body.branch])
    if body.flavor:
        cmd.extend(["--flavor", body.flavor])

    job_id = uuid.uuid4().hex[:12]
    _create_jobs[job_id] = {"status": "running", "name": rdev_full_name, "updated": time.time()}
    _purge_old_jobs()

    logger.info("Creating rdev (background): %s [job=%s]", " ".join(cmd), job_id)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_create_rdev, cmd, rdev_full_name, job_id)

    return {"ok": True, "status": "creating", "name": rdev_full_name, "job_id": job_id}


@router.get("/rdevs/jobs/{job_id}")
def get_create_job(job_id: str):
    """Poll the status of a background rdev-create job.

    Returns {status: "running"}, {status: "done", name: ...},
    or {status: "failed", error: ...}.
    """
    job = _create_jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found or expired")
    return job


@router.delete("/rdevs/{rdev_name:path}")
def delete_rdev(rdev_name: str, db=Depends(get_db)):
    """Delete an rdev instance.

    Uses -f flag for non-interactive deletion.
    Fails if the rdev has active workers assigned.
    """
    # Check if rdev has workers assigned
    sessions = repo.list_sessions(db)
    for s in sessions:
        if s.host == rdev_name:
            raise HTTPException(
                409,
                f"Cannot delete rdev '{rdev_name}': worker '{s.name}' is still assigned. "
                "Remove the worker first.",
            )

    try:
        logger.info("Deleting rdev: %s", rdev_name)
        result = subprocess.run(
            ["rdev", "delete", rdev_name, "-f"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            logger.error("rdev delete failed: %s", error_msg)
            raise HTTPException(400, f"Failed to delete rdev: {error_msg}")

        # Invalidate cache
        global _rdev_cache
        _rdev_cache["timestamp"] = 0

        logger.info("Deleted rdev: %s", rdev_name)
        return {"ok": True}

    except subprocess.TimeoutExpired:
        logger.error("rdev delete timed out for %s", rdev_name)
        raise HTTPException(504, "rdev delete timed out")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("rdev delete failed")
        raise HTTPException(500, f"Failed to delete rdev: {str(e)}")


@router.post("/rdevs/{rdev_name:path}/restart")
def restart_rdev(rdev_name: str):
    """Restart an rdev instance."""
    try:
        logger.info("Restarting rdev: %s", rdev_name)
        result = subprocess.run(
            ["rdev", "restart", rdev_name],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            logger.error("rdev restart failed: %s", error_msg)
            raise HTTPException(400, f"Failed to restart rdev: {error_msg}")

        # Invalidate cache
        global _rdev_cache
        _rdev_cache["timestamp"] = 0

        logger.info("Restarted rdev: %s", rdev_name)
        return {"ok": True}

    except subprocess.TimeoutExpired:
        logger.error("rdev restart timed out for %s", rdev_name)
        raise HTTPException(504, "rdev restart timed out")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("rdev restart failed")
        raise HTTPException(500, f"Failed to restart rdev: {str(e)}")


@router.post("/rdevs/{rdev_name:path}/stop")
def stop_rdev(rdev_name: str):
    """Stop an rdev instance."""
    try:
        logger.info("Stopping rdev: %s", rdev_name)
        result = subprocess.run(
            ["rdev", "stop", rdev_name],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            logger.error("rdev stop failed: %s", error_msg)
            raise HTTPException(400, f"Failed to stop rdev: {error_msg}")

        # Invalidate cache
        global _rdev_cache
        _rdev_cache["timestamp"] = 0

        logger.info("Stopped rdev: %s", rdev_name)
        return {"ok": True}

    except subprocess.TimeoutExpired:
        logger.error("rdev stop timed out for %s", rdev_name)
        raise HTTPException(504, "rdev stop timed out")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("rdev stop failed")
        raise HTTPException(500, f"Failed to stop rdev: {str(e)}")
