"""API routes for browser view (CDP screencast) — works for both local and remote workers."""

import asyncio
import glob
import logging
import os
import subprocess

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orchestrator.api.deps import get_db
from orchestrator.browser.cdp_proxy import (
    cleanup_stale_view,
    discover_browser_targets,
    get_active_view,
    is_view_alive,
    start_browser_view,
    stop_browser_view,
)
from orchestrator.core.events import Event, publish
from orchestrator.state.repositories import sessions as repo
from orchestrator.terminal.remote_worker_server import (
    ensure_rws_starting,
    force_restart_server,
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


def _read_cdp_port_for_session(session_name: str) -> int | None:
    """Read ORCH_CDP_PORT from a local worker's deployed lib.sh."""
    import re

    lib_path = os.path.join("/tmp/orchestrator/workers", session_name, "bin", "lib.sh")
    if os.path.exists(lib_path):
        with open(lib_path) as f:
            for line in f:
                if "ORCH_CDP_PORT" in line:
                    m = re.search(r":-(\d+)", line)
                    if m:
                        return int(m.group(1))
    return None


def _auto_start_browser_local(session_id: str, cdp_port: int) -> int:
    """Launch Chrome/Chromium locally for a local worker session.

    Idempotent — if Chrome is already running on the port, returns immediately.
    Search order: Playwright cache, system apps (macOS), PATH.
    Launches headed with CDP enabled, stores PID for cleanup.

    Returns the actual CDP port used.
    """
    import platform

    # Check if Chrome is already running on this port
    try:
        resp = httpx.get(f"http://localhost:{cdp_port}/json/version", timeout=2)
        if resp.status_code == 200:
            logger.info("Chrome already running on port %d, reusing", cdp_port)
            return cdp_port
    except Exception:
        pass  # Not running, proceed to launch

    chromium_bin = None

    # 1. Playwright cache (Linux: ~/.cache, macOS: ~/Library/Caches)
    pw_dirs = [
        os.path.expanduser("~/.cache/ms-playwright"),
        os.path.expanduser("~/Library/Caches/ms-playwright"),
    ]
    for pw_dir in pw_dirs:
        if not os.path.isdir(pw_dir):
            continue
        # Standard Playwright binary names
        for name in ("chrome", "headless_shell", "chromium"):
            matches = sorted(glob.glob(f"{pw_dir}/chromium*/{name}"), reverse=True)
            for m in matches:
                if os.access(m, os.X_OK):
                    chromium_bin = m
                    break
            if chromium_bin:
                break
        if chromium_bin:
            break
        # macOS: Playwright installs Chrome as a .app bundle
        matches = sorted(
            glob.glob(f"{pw_dir}/chromium*/*/Google Chrome*.app/Contents/MacOS/*"),
            reverse=True,
        )
        for m in matches:
            if os.access(m, os.X_OK):
                chromium_bin = m
                break
        if chromium_bin:
            break

    # 2. System-installed browsers (macOS .app bundles)
    if not chromium_bin and platform.system() == "Darwin":
        for app in (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ):
            if os.access(app, os.X_OK):
                chromium_bin = app
                break

    # 3. PATH
    if not chromium_bin:
        for name in ("chromium-browser", "chromium", "google-chrome", "chrome"):
            result = subprocess.run(["which", name], capture_output=True, text=True)
            if result.returncode == 0:
                chromium_bin = result.stdout.strip()
                break

    if not chromium_bin:
        raise RuntimeError(
            "Chrome/Chromium not found. Install Google Chrome or run: orch-browser --start"
        )

    pid_dir = "/tmp/orchestrator"
    os.makedirs(pid_dir, exist_ok=True)

    # Local/headed: normal browser window with URL bar (no sandbox/gpu flags)
    # Shared PID file — all local workers share one Chrome instance
    log_file = open(f"{pid_dir}/browser-local.log", "w")
    proc = subprocess.Popen(
        [
            chromium_bin,
            f"--remote-debugging-port={cdp_port}",
            "--remote-debugging-address=127.0.0.1",
            "--disable-infobars",
            "--window-size=1280,960",
            "about:blank",
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    # Store PID for cleanup (shared across all local workers)
    with open(f"{pid_dir}/browser-local.pid", "w") as f:
        f.write(str(proc.pid))

    return cdp_port


async def _wait_for_cdp_ready(port: int, timeout: float = 8.0, interval: float = 0.3):
    """Poll the CDP /json/version endpoint until Chrome is listening."""
    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient(timeout=2.0) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await client.get(f"http://localhost:{port}/json/version")
                if resp.status_code == 200:
                    return
            except Exception:
                pass
            await asyncio.sleep(interval)
    raise RuntimeError(f"Chrome did not become ready on port {port} within {timeout}s")


async def _auto_start_browser_and_retry(
    session_id: str,
    host: str,
    cdp_port: int,
    quality: int,
    max_width: int,
    max_height: int,
    original_error: str,
):
    """Auto-start browser and retry browser view creation.

    Called when start_browser_view() fails with "No browser found".
    For remote: waits for the RWS daemon to be ready, starts browser, then retries.
    For local: launches Chromium directly, then retries.
    """
    try:
        if is_remote_host(host):
            rws = await _wait_for_rws(host)
            try:
                await asyncio.to_thread(rws.start_browser, session_id, port=cdp_port)
            except RuntimeError as e:
                if "Unknown action" in str(e):
                    logger.warning("Stale RWS daemon on %s, redeploying", host)
                    rws = await asyncio.to_thread(force_restart_server, host)
                    await asyncio.to_thread(rws.start_browser, session_id, port=cdp_port)
                else:
                    raise
            # Remote: tunnel doesn't exist yet, so we can't poll localhost.
            # start_browser_view() will create the tunnel and discover_browser_targets
            # has its own retries (5 attempts, 1s delay).
            await asyncio.sleep(1)
        else:
            _auto_start_browser_local(session_id, cdp_port)
            # Local: poll until Chrome's CDP port is accepting connections.
            # Chrome can take several seconds to start on first launch.
            await _wait_for_cdp_ready(cdp_port)

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
        # Include both the original and retry errors so the user can debug
        retry_detail = str(e)
        if retry_detail and retry_detail != original_error:
            detail = f"{original_error} | Auto-start retry failed: {retry_detail}"
        else:
            detail = original_error
        raise HTTPException(502, detail)


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

    # For local workers, read the actual CDP port from deployed lib.sh
    cdp_port = body.cdp_port
    if not is_remote_host(s.host):
        cdp_port = _read_cdp_port_for_session(s.name) or body.cdp_port

    existing = get_active_view(session_id)
    if existing:
        if is_view_alive(session_id):
            raise HTTPException(409, "Browser view already active for this session")
        # Stale view (CDP WebSocket dead) — clean up and proceed
        logger.info("Cleaning up stale browser view for session %s", session_id)
        await cleanup_stale_view(session_id)

    try:
        view = await start_browser_view(
            session_id=session_id,
            host=s.host,
            cdp_port=cdp_port,
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
                cdp_port=cdp_port,
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

    stopped = await stop_browser_view(session_id, close_tab=True)
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

    # If the CDP WebSocket is dead, clean up and report inactive
    if not is_view_alive(session_id):
        await cleanup_stale_view(session_id)
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
    """Start a browser process.

    For remote workers: uses the RWS daemon.
    For local workers: launches Chromium directly.
    """
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    # For local workers, read the actual CDP port from deployed lib.sh
    port = body.port
    if not is_remote_host(s.host):
        port = _read_cdp_port_for_session(s.name) or body.port

    if is_remote_host(s.host):
        # Remote: use RWS daemon
        try:
            rws = get_remote_worker_server(s.host)
        except RuntimeError:
            raise HTTPException(503, "Remote worker server not available")

        try:
            result = rws.start_browser(session_id, port=port)
        except RuntimeError as e:
            if "Unknown action" in str(e):
                # Stale daemon — force-redeploy and retry once
                logger.warning("Stale RWS daemon on %s, redeploying", s.host)
                try:
                    rws = force_restart_server(s.host)
                    result = rws.start_browser(session_id, port=port)
                except RuntimeError as e2:
                    raise HTTPException(500, str(e2))
            else:
                raise HTTPException(500, str(e))

        return {
            "ok": True,
            "pid": result.get("pid"),
            "port": result.get("port"),
            "already_running": result.get("already_running", False),
        }
    else:
        # Local: launch Chromium directly
        try:
            actual_port = _auto_start_browser_local(session_id, port)
            return {"ok": True, "pid": None, "port": actual_port, "already_running": False}
        except RuntimeError as e:
            raise HTTPException(500, str(e))


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
        if "Unknown action" in str(e):
            logger.warning("Stale RWS daemon on %s, redeploying", s.host)
            try:
                rws = force_restart_server(s.host)
                rws.stop_browser(session_id)
            except RuntimeError as e2:
                raise HTTPException(500, str(e2))
        else:
            raise HTTPException(500, str(e))

    return {"ok": True}
