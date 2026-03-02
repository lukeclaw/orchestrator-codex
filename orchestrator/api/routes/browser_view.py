"""API routes for remote browser view (CDP screencast)."""

import asyncio
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
from orchestrator.terminal.remote_worker_server import (
    ensure_rws_starting,
    get_remote_worker_server,
)
from orchestrator.terminal.ssh import is_remote_host

logger = logging.getLogger(__name__)


async def _wait_for_rws(host: str, timeout: float = 15.0):
    """Wait for the Remote Worker Server to become available.

    Polls get_remote_worker_server() in a loop, giving the background
    start thread time to finish. Kicks off a start if not already happening.
    """
    ensure_rws_starting(host)
    deadline = asyncio.get_event_loop().time() + timeout
    last_err = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            return get_remote_worker_server(host)
        except RuntimeError as e:
            last_err = e
            await asyncio.sleep(1.0)
    raise RuntimeError(f"Remote worker server not ready after {timeout}s: {last_err}")


async def _auto_start_browser_and_retry(
    session_id: str,
    host: str,
    cdp_port: int,
    quality: int,
    max_width: int,
    max_height: int,
    original_error: str,
):
    """Auto-start browser via daemon and retry browser view creation.

    Called when start_browser_view() fails with "No browser found".
    Waits for the RWS daemon to be ready, starts browser, then retries.
    """
    try:
        rws = await _wait_for_rws(host)
        rws.start_browser(session_id, port=cdp_port)
        await asyncio.sleep(1)  # Let CDP fully initialize after daemon confirms
        return await start_browser_view(
            session_id=session_id,
            host=host,
            cdp_port=cdp_port,
            quality=quality,
            max_width=max_width,
            max_height=max_height,
        )
    except Exception as e:
        logger.warning(
            "Auto-start browser failed for session %s: %s (original: %s)",
            session_id,
            e,
            original_error,
        )
        raise HTTPException(502, original_error)


router = APIRouter()


class BrowserViewRequest(BaseModel):
    cdp_port: int = Field(default=9222, ge=1, le=65535)
    quality: int = Field(default=60, ge=1, le=100)
    max_width: int = Field(default=1280, ge=320, le=3840)
    max_height: int = Field(default=960, ge=240, le=2160)


class BrowserStartRequest(BaseModel):
    port: int = Field(default=9222, ge=1, le=65535)


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
            # Auto-start: launch browser via daemon, then retry browser view.
            # The RWS daemon may still be connecting (esp. after reconnect),
            # so we poll for it with a timeout.
            view = await _auto_start_browser_and_retry(
                session_id=session_id,
                host=s.host,
                cdp_port=body.cdp_port,
                quality=body.quality,
                max_width=body.max_width,
                max_height=body.max_height,
                original_error=msg,
            )
        elif "tunnel" in msg.lower():
            raise HTTPException(502, msg)
        else:
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


@router.post("/sessions/{session_id}/browser-start")
def start_browser_endpoint(
    session_id: str,
    body: BrowserStartRequest,
    db=Depends(get_db),
):
    """Start a browser process via the RWS daemon.

    Used by the orch-browser CLI to launch Chromium on the remote worker.
    """
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    try:
        rws = get_remote_worker_server(s.host)
    except RuntimeError:
        raise HTTPException(503, "Remote worker server not available")

    try:
        result = rws.start_browser(session_id, port=body.port)
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    return {
        "ok": True,
        "pid": result.get("pid"),
        "port": result.get("port"),
        "already_running": result.get("already_running", False),
    }


@router.post("/sessions/{session_id}/browser-stop")
def stop_browser_endpoint(session_id: str, db=Depends(get_db)):
    """Stop the browser process via the RWS daemon."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    try:
        rws = get_remote_worker_server(s.host)
    except RuntimeError:
        raise HTTPException(503, "Remote worker server not available")

    try:
        rws.stop_browser(session_id)
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    return {"ok": True}
