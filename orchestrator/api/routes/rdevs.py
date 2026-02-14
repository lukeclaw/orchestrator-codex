"""Rdev management: list, create, delete, restart, stop."""

import asyncio
import logging
import subprocess
import time
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
        logger.info("Started rdev background refresh task (interval: %ds)", RDEV_BACKGROUND_REFRESH_INTERVAL)


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
        lines = output.strip().split('\n')
        for line in lines:
            # Skip header, separator lines, and info messages
            if '|' not in line or line.startswith('-') or 'Name' in line and 'State' in line:
                continue
            
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 2:
                name = parts[0].strip()
                state = parts[1].strip() if len(parts) > 1 else ''
                
                # Skip empty or invalid entries
                if not name or '/' not in name:
                    continue
                
                rdevs.append({
                    "name": name,
                    "state": state,
                    "cluster": parts[2].strip() if len(parts) > 2 else '',
                    "created": parts[3].strip() if len(parts) > 3 else '',
                    "last_accessed": parts[4].strip() if len(parts) > 4 else '',
                })
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


@router.post("/rdevs", status_code=201)
def create_rdev(body: RdevCreate):
    """Create a new rdev instance.
    
    Runs `rdev create <mp_name>/<rdev_name>` synchronously.
    This may take ~30 seconds to complete.
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
    
    try:
        logger.info("Creating rdev: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # 2 minute timeout for create
        )
        
        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            logger.error("rdev create failed: %s", error_msg)
            raise HTTPException(400, f"Failed to create rdev: {error_msg}")
        
        # Invalidate cache so next list shows the new rdev
        global _rdev_cache
        _rdev_cache["timestamp"] = 0
        
        created_name = result.stdout.strip() or rdev_full_name
        logger.info("Created rdev: %s", created_name)
        return {"ok": True, "name": created_name}
        
    except subprocess.TimeoutExpired:
        logger.error("rdev create timed out for %s", rdev_full_name)
        raise HTTPException(504, "rdev create timed out (>2 minutes)")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("rdev create failed")
        raise HTTPException(500, f"Failed to create rdev: {str(e)}")


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
                "Remove the worker first."
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
