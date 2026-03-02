"""API routes for remote browser view (CDP screencast)."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orchestrator.api.deps import get_db
from orchestrator.browser.cdp_proxy import (
    discover_browser_targets,
    get_active_view,
    start_browser_view,
    stop_browser_view,
)
from orchestrator.core.events import Event, publish
from orchestrator.state.repositories import sessions as repo
from orchestrator.terminal.ssh import is_remote_host

logger = logging.getLogger(__name__)

router = APIRouter()


class BrowserViewRequest(BaseModel):
    cdp_port: int = Field(default=9222, ge=1, le=65535)
    quality: int = Field(default=60, ge=1, le=100)
    max_width: int = Field(default=1280, ge=320, le=3840)
    max_height: int = Field(default=960, ge=240, le=2160)


@router.post("/sessions/{session_id}/browser-view")
async def start_browser_view_endpoint(
    session_id: str,
    body: BrowserViewRequest,
    db=Depends(get_db),
):
    """Start a browser view session for a worker.

    Creates an SSH tunnel for the CDP port, connects to the remote browser,
    and starts screencast streaming.
    """
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    if not is_remote_host(s.host):
        raise HTTPException(400, "Browser view is only supported for remote (rdev/SSH) workers")

    existing = get_active_view(session_id)
    if existing:
        raise HTTPException(409, "Browser view already active for this session")

    try:
        view = await start_browser_view(
            session_id=session_id,
            host=s.host,
            cdp_port=body.cdp_port,
            quality=body.quality,
            max_width=body.max_width,
            max_height=body.max_height,
        )
    except ValueError as e:
        raise HTTPException(409, str(e))
    except RuntimeError as e:
        msg = str(e)
        if "No browser found" in msg or "No debuggable pages" in msg:
            raise HTTPException(502, msg)
        if "tunnel" in msg.lower():
            raise HTTPException(502, msg)
        raise HTTPException(500, msg)

    # Broadcast event
    publish(
        Event(
            type="browser_view_started",
            data={
                "session_id": session_id,
                "session_name": s.name,
                "page_url": view.page_url,
                "page_title": view.page_title,
            },
        )
    )

    return {
        "ok": True,
        "page_url": view.page_url,
        "page_title": view.page_title,
        "viewport": {
            "width": view.viewport_width,
            "height": view.viewport_height,
        },
        "tunnel_port": view.tunnel_local_port,
    }


@router.delete("/sessions/{session_id}/browser-view")
async def stop_browser_view_endpoint(session_id: str, db=Depends(get_db)):
    """Stop the browser view and close CDP connection + tunnel."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    stopped = await stop_browser_view(session_id)
    if not stopped:
        raise HTTPException(404, "No active browser view for this session")

    publish(
        Event(
            type="browser_view_closed",
            data={"session_id": session_id},
        )
    )

    return {"ok": True}


@router.get("/sessions/{session_id}/browser-view")
async def get_browser_view_status(session_id: str, db=Depends(get_db)):
    """Get the status of the browser view."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    view = get_active_view(session_id)
    if not view:
        return {"active": False}

    return {
        "active": True,
        "page_url": view.page_url,
        "page_title": view.page_title,
        "created_at": view.created_at,
        "tunnel_port": view.tunnel_local_port,
        "quality": view.quality,
        "viewport": {
            "width": view.viewport_width,
            "height": view.viewport_height,
        },
    }


@router.get("/sessions/{session_id}/browser-view/targets")
async def list_browser_targets(
    session_id: str,
    cdp_port: int = 9222,
    db=Depends(get_db),
):
    """List available browser page targets for debugging.

    Useful for discovering which pages are open in the remote browser
    before starting a browser view.

    Requires an active SSH tunnel for the CDP port (either from an
    active browser view or a manually created tunnel).
    """
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    # Check if there's already a tunnel for this port
    view = get_active_view(session_id)
    if view:
        port = view.tunnel_local_port
    else:
        # Try the raw port — caller may have tunneled it manually
        port = cdp_port

    try:
        targets = await discover_browser_targets(port)
    except Exception as e:
        raise HTTPException(
            502,
            f"Cannot reach CDP on port {port}. "
            f"Ensure the browser is running with --remote-debugging-port: {e}",
        )

    return {
        "targets": [
            {
                "id": t.get("id", ""),
                "title": t.get("title", ""),
                "url": t.get("url", ""),
            }
            for t in targets
        ]
    }


@router.post("/sessions/{session_id}/browser-view/minimize")
def minimize_browser_view(session_id: str, db=Depends(get_db)):
    """Minimize the browser view overlay (UI-only, no CDP changes)."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    view = get_active_view(session_id)
    if not view:
        raise HTTPException(404, "No active browser view for this session")

    publish(
        Event(
            type="browser_view_minimized",
            data={"session_id": session_id},
        )
    )

    return {"ok": True}


@router.post("/sessions/{session_id}/browser-view/restore")
def restore_browser_view(session_id: str, db=Depends(get_db)):
    """Restore the browser view overlay from minimized state."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    view = get_active_view(session_id)
    if not view:
        raise HTTPException(404, "No active browser view for this session")

    publish(
        Event(
            type="browser_view_restored",
            data={"session_id": session_id},
        )
    )

    return {"ok": True}
