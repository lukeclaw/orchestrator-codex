"""CDP (Chrome DevTools Protocol) proxy for remote browser view.

Manages the lifecycle of browser view sessions:
- SSH tunnel for CDP port forwarding
- CDP WebSocket connection to the remote browser
- Page.startScreencast for JPEG frame streaming
- Input.dispatch* for mouse/keyboard event relay

Each worker session can have at most one active browser view.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import websockets
import websockets.asyncio.client
import websockets.exceptions
from websockets.protocol import State as WsState

from orchestrator.session.tunnel import close_tunnel, create_tunnel
from orchestrator.terminal.ssh import is_remote_host

logger = logging.getLogger(__name__)

# Monotonically increasing CDP message ID generator
_id_counter = itertools.count(1)


def _next_id() -> int:
    return next(_id_counter)


@dataclass
class BrowserViewSession:
    """Tracks an active browser view session."""

    session_id: str  # Parent worker session
    host: str  # rdev host for tunnel cleanup
    cdp_ws: Any  # websockets client connection
    tunnel_local_port: int  # SSH tunnel local port
    target_id: str = ""  # CDP target ID for this worker's tab
    page_url: str = ""  # Current page URL
    page_title: str = ""  # Current page title
    viewport_width: int = 1280
    viewport_height: int = 960
    quality: int = 60
    status: str = "active"  # "active" | "closed"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    # Pending CDP responses: msg_id -> Future[dict]
    _pending: dict[int, asyncio.Future] = field(default_factory=dict, repr=False)
    # Tasks that read from the CDP WebSocket (relay + url poll).
    # Tracked so a new client connection can cancel them before starting
    # its own recv() loop — websockets forbids concurrent recv() calls.
    _cdp_reader_tasks: list = field(default_factory=list, repr=False)
    # Current zoom level — used to compute expected viewport dimensions
    # for detecting when another CDP client (Playwright) overrides them.
    _zoom_percent: int = field(default=100, repr=False)
    _last_viewport_fix: float = field(default=0, repr=False)
    # Set by the client input relay when the user requests a tab switch.
    # The WS handler loop reads this to perform the actual switch.
    _switch_target: str = field(default="", repr=False)
    _close_after_switch: str = field(default="", repr=False)
    _known_tab_ids: set = field(default_factory=set, repr=False)


# In-memory registry: session_id -> BrowserViewSession
_active_views: dict[str, BrowserViewSession] = {}

# Persistent mapping: session_id -> target_id for local workers.
# Survives browser view stop/start cycles so we reconnect to the same tab.
_session_tab_targets: dict[str, str] = {}

# Track which target is currently the active (foreground) tab per CDP port.
# Used to skip redundant Target.activateTarget calls.
_active_tab_target: dict[int, str] = {}


def get_active_view(session_id: str) -> BrowserViewSession | None:
    """Get the active browser view for a session, or None."""
    return _active_views.get(session_id)


def list_active_views() -> list[str]:
    """Return session IDs that have active browser views."""
    return list(_active_views.keys())


def is_view_alive(session_id: str) -> bool:
    """Check if the browser view's CDP WebSocket is still connected.

    Returns False if no view exists or the WebSocket is not OPEN.
    O(1) non-blocking — checks in-memory state only.
    """
    view = _active_views.get(session_id)
    if view is None:
        return False
    return view.cdp_ws.state is WsState.OPEN


async def cleanup_stale_view(session_id: str) -> bool:
    """Remove a dead browser view from the registry and clean up resources.

    Returns True if a stale view was found and cleaned up.
    Returns False if no view exists or the view is still alive.
    """
    view = _active_views.get(session_id)
    if view is None:
        return False
    if view.cdp_ws.state is WsState.OPEN:
        return False

    # View is dead — remove from registry and clean up
    _active_views.pop(session_id, None)
    view.status = "closed"

    # Best-effort WebSocket close
    try:
        await view.cdp_ws.close()
    except Exception:
        pass

    # Close SSH tunnel (remote only); keep local tabs alive for reconnection
    if is_remote_host(view.host):
        try:
            close_tunnel(view.tunnel_local_port, view.host)
        except Exception:
            pass

    logger.info("Cleaned up stale browser view for session %s", session_id)
    return True


async def discover_browser_targets(
    cdp_port: int, retries: int = 5, delay: float = 1.0
) -> list[dict]:
    """Query CDP /json endpoint to find debuggable pages.

    Returns list of targets with fields: id, title, url, webSocketDebuggerUrl.
    Filters to type='page' targets only.

    Retries a few times to allow SSH tunnels to become ready.
    """
    url = f"http://localhost:{cdp_port}/json"
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                targets = resp.json()
                return [t for t in targets if t.get("type") == "page"]
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                logger.debug("CDP discovery attempt %d/%d failed: %s", attempt + 1, retries, e)
                await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


async def _find_target_by_id(cdp_port: int, target_id: str) -> dict | None:
    """Find an existing browser target by its ID. Returns None if not found."""
    try:
        targets = await discover_browser_targets(cdp_port, retries=1)
        for t in targets:
            if t.get("id") == target_id:
                return t
    except Exception:
        pass
    return None


async def create_browser_tab(cdp_port: int, url: str = "about:blank") -> dict:
    """Create a new browser tab via CDP HTTP API.

    Returns the target info dict including webSocketDebuggerUrl and id.
    New tabs are automatically the active tab in Chrome.
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.put(f"http://localhost:{cdp_port}/json/new?{url}")
        resp.raise_for_status()
        result = resp.json()
        # New tabs are the active tab — track so we can skip redundant activation
        tab_id = result.get("id", "")
        if tab_id:
            _active_tab_target[cdp_port] = tab_id
        return result


async def activate_browser_tab(cdp_port: int, target_id: str) -> None:
    """Switch the active tab within Chrome without stealing OS-level focus.

    Inactive tabs don't render, which causes black screencast frames.
    Uses Target.activateTarget via the browser-level CDP WebSocket, which
    switches tabs inside Chrome without bringing the Chrome window to the
    front on macOS.  The HTTP /json/activate endpoint and Page.bringToFront
    both trigger OS-level focus stealing, so we avoid them.

    Skips the call entirely if the target is already the active tab.
    """
    if _active_tab_target.get(cdp_port) == target_id:
        logger.debug("Tab %s already active on port %d, skipping activation", target_id, cdp_port)
        return

    # Get the browser-level WebSocket URL from /json/version
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"http://localhost:{cdp_port}/json/version")
        resp.raise_for_status()
        browser_ws_url = resp.json().get("webSocketDebuggerUrl", "")
    if not browser_ws_url:
        raise RuntimeError("No browser WebSocket URL found in /json/version")

    # Rewrite the host/port in case it differs (e.g. 0.0.0.0 vs localhost)
    browser_ws_url = re.sub(r"://[^/]+", f"://127.0.0.1:{cdp_port}", browser_ws_url)

    async with websockets.asyncio.client.connect(browser_ws_url, open_timeout=5) as ws:
        msg_id = _next_id()
        await ws.send(
            json.dumps(
                {
                    "id": msg_id,
                    "method": "Target.activateTarget",
                    "params": {"targetId": target_id},
                }
            )
        )
        # Wait for the response to confirm it was processed
        deadline = asyncio.get_event_loop().time() + 3.0
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            resp_msg = json.loads(raw)
            if resp_msg.get("id") == msg_id:
                break

    _active_tab_target[cdp_port] = target_id


async def bring_browser_to_front(cdp_port: int, target_id: str) -> None:
    """Bring the Chrome window to the OS foreground.

    Uses the HTTP /json/activate endpoint which triggers OS-level window
    activation (unlike Target.activateTarget which only switches tabs).
    Use this for local workers where the user needs to see the actual
    browser window (e.g. for auth popups).
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        await client.get(f"http://localhost:{cdp_port}/json/activate/{target_id}")
    _active_tab_target[cdp_port] = target_id


async def close_browser_tab(cdp_port: int, target_id: str) -> None:
    """Close a browser tab via CDP HTTP API."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        await client.get(f"http://localhost:{cdp_port}/json/close/{target_id}")
    # If this was the active tab, clear tracking so the next activation isn't skipped
    if _active_tab_target.get(cdp_port) == target_id:
        _active_tab_target.pop(cdp_port, None)


async def _cdp_send(
    cdp_ws: Any,
    method: str,
    params: dict | None = None,
) -> int:
    """Send a CDP command and return the message ID."""
    msg_id = _next_id()
    msg = {"id": msg_id, "method": method}
    if params:
        msg["params"] = params
    await cdp_ws.send(json.dumps(msg))
    return msg_id


async def _cdp_send_and_wait(
    cdp_ws: Any,
    method: str,
    params: dict | None = None,
    timeout: float = 5.0,
) -> dict:
    """Send a CDP command and wait for its response.

    Used during startup before the relay loop exists.  Reads messages
    directly from the WebSocket, discarding events and responses to
    earlier fire-and-forget sends until the matching response arrives.
    """
    msg_id = await _cdp_send(cdp_ws, method, params)
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"CDP response timeout for {method}")
        raw = await asyncio.wait_for(cdp_ws.recv(), timeout=remaining)
        msg = json.loads(raw)
        if msg.get("id") == msg_id:
            return msg


async def _cdp_call(
    view: BrowserViewSession,
    method: str,
    params: dict | None = None,
    timeout: float = 5.0,
) -> dict:
    """Send a CDP command and wait for its response."""
    msg_id = await _cdp_send(view.cdp_ws, method, params)
    fut: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
    view._pending[msg_id] = fut
    try:
        return await asyncio.wait_for(fut, timeout)
    finally:
        view._pending.pop(msg_id, None)


async def start_browser_view(
    session_id: str,
    host: str,
    cdp_port: int = 9222,
    quality: int = 60,
    max_width: int = 1280,
    max_height: int = 960,
) -> BrowserViewSession:
    """Start a browser view session.

    1. Creates SSH tunnel for CDP port
    2. Discovers page targets
    3. Connects to first page's CDP WebSocket
    4. Starts screencast

    Raises:
        ValueError: Browser view already active for this session.
        RuntimeError: No browser found, tunnel failure, CDP connection failure.
    """
    if session_id in _active_views:
        raise ValueError(f"Browser view already active for session {session_id}")

    # Step 1: Connect to CDP — tunnel for remote, direct for local
    if is_remote_host(host):
        success, info = create_tunnel(host, cdp_port)
        if not success:
            raise RuntimeError(f"Failed to create CDP tunnel: {info.get('error', 'unknown')}")
        local_port = info["local_port"]
        logger.info(
            "Created CDP tunnel local:%d -> %s:%d for session %s",
            local_port,
            host,
            cdp_port,
            session_id,
        )
    else:
        # Local: connect directly, no tunnel needed
        local_port = cdp_port

    # Step 2: Get a target — create a new tab for local, discover for remote
    if is_remote_host(host):
        try:
            targets = await discover_browser_targets(local_port)
        except Exception as e:
            close_tunnel(local_port, host)
            raise RuntimeError(
                f"No browser found on CDP port {cdp_port}. "
                f"Ensure Chromium is running with --remote-debugging-port={cdp_port}: {e}"
            ) from e

        if not targets:
            close_tunnel(local_port, host)
            raise RuntimeError(
                f"No debuggable pages found on CDP port {cdp_port}. "
                f"The browser may have no open tabs."
            )
        target = targets[0]
    else:
        # Local: reuse the tab Playwright MCP is already using (via CDP proxy),
        # fall back to a remembered tab, then create a new one as last resort.
        target = None

        # Priority 1: Use the CDP worker proxy's tab (what Playwright sees)
        from orchestrator.browser.cdp_worker_proxy import _worker_proxies

        proxy_info = _worker_proxies.get(session_id)
        if proxy_info and proxy_info.target_id:
            target = await _find_target_by_id(local_port, proxy_info.target_id)
            if target:
                logger.info(
                    "Using CDP proxy tab %s for session %s",
                    proxy_info.target_id,
                    session_id,
                )

        # Priority 2: Remembered tab from a previous browser view
        if target is None:
            prev_target_id = _session_tab_targets.get(session_id)
            if prev_target_id:
                target = await _find_target_by_id(local_port, prev_target_id)
                if target:
                    logger.info(
                        "Reconnecting to existing tab %s for session %s",
                        prev_target_id,
                        session_id,
                    )

        # Priority 3: Create a new tab
        if target is None:
            try:
                target = await create_browser_tab(local_port)
            except Exception as e:
                raise RuntimeError(
                    f"No browser found on CDP port {cdp_port}. "
                    f"Ensure Chromium is running with --remote-debugging-port={cdp_port}: {e}"
                ) from e
    ws_url = target.get("webSocketDebuggerUrl", "")
    if not ws_url:
        if is_remote_host(host):
            close_tunnel(local_port, host)
        raise RuntimeError("Target has no webSocketDebuggerUrl")

    # Activate the tab so Chrome renders it — inactive tabs produce black
    # screencast frames.
    target_id_val = target.get("id", "")
    if target_id_val:
        try:
            if is_remote_host(host):
                # Remote: switch tab without stealing OS focus (Chrome is on remote host)
                await activate_browser_tab(local_port, target_id_val)
            else:
                # Local: bring Chrome window to the foreground so the user can see it
                # (needed for auth popups and direct interaction)
                await bring_browser_to_front(local_port, target_id_val)
        except Exception:
            pass  # Best-effort; screencast may still work

    # The CDP WebSocket URL from the browser uses the original port.
    # Replace it with our tunneled local port.
    # e.g., ws://127.0.0.1:9222/devtools/page/ABC -> ws://127.0.0.1:{local_port}/devtools/page/ABC
    ws_url = re.sub(r"://[^/]+", f"://127.0.0.1:{local_port}", ws_url)

    # Step 3: Connect to CDP WebSocket
    try:
        cdp_ws = await websockets.asyncio.client.connect(
            ws_url,
            max_size=16 * 1024 * 1024,  # 16 MB max message (for large frames)
            open_timeout=10,
            # Disable websockets' built-in ping/pong.  When nobody is reading
            # from the CDP WS (between client disconnects), screencast frames
            # pile up, triggering pause_reading() which blocks pong processing.
            # This causes a false ping timeout after ~40s, killing the CDP
            # connection.  We detect liveness through the relay read loop and
            # reconnect_cdp() instead.
            ping_interval=None,
        )
    except Exception as e:
        if is_remote_host(host):
            close_tunnel(local_port, host)
        raise RuntimeError(f"Failed to connect to CDP WebSocket: {e}") from e

    # Step 4: Set viewport, enable Page events, and start screencast.
    # Viewport must be set BEFORE screencast starts, otherwise the first
    # frames render at Chrome's default viewport size (wrong zoom).
    try:
        await _cdp_send(cdp_ws, "Page.enable")
        await _cdp_send(
            cdp_ws,
            "Emulation.setEmulatedMedia",
            {"features": [{"name": "prefers-color-scheme", "value": "dark"}]},
        )
        await _cdp_send(
            cdp_ws,
            "Emulation.setDefaultBackgroundColorOverride",
            {"color": {"r": 30, "g": 30, "b": 30, "a": 1}},
        )
        await _cdp_send_and_wait(
            cdp_ws,
            "Emulation.setDeviceMetricsOverride",
            {
                "width": max_width,
                "height": max_height,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )
        await _cdp_send(
            cdp_ws,
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": quality,
                "maxWidth": max_width,
                "maxHeight": max_height,
                "everyNthFrame": 1,
            },
        )
    except Exception as e:
        await cdp_ws.close()
        if is_remote_host(host):
            close_tunnel(local_port, host)
        raise RuntimeError(f"Failed to start screencast: {e}") from e

    target_id = target.get("id", "")
    view = BrowserViewSession(
        session_id=session_id,
        host=host,
        cdp_ws=cdp_ws,
        tunnel_local_port=local_port,
        target_id=target_id,
        page_url=target.get("url", ""),
        page_title=target.get("title", ""),
        viewport_width=max_width,
        viewport_height=max_height,
        quality=quality,
    )
    _active_views[session_id] = view

    # Remember which tab belongs to this session for reconnection,
    # and sync to the CDP worker proxy so Playwright MCP uses the same tab.
    if not is_remote_host(host) and target_id:
        _session_tab_targets[session_id] = target_id
        if proxy_info and proxy_info.target_id != target_id:
            proxy_info.target_id = target_id
            logger.info(
                "Synced CDP proxy target to %s for session %s",
                target_id,
                session_id,
            )

    logger.info(
        "Started browser view for session %s: %s (%s)",
        session_id,
        view.page_title,
        view.page_url,
    )

    return view


async def stop_browser_view(session_id: str, close_tab: bool = False) -> bool:
    """Stop the browser view and clean up resources.

    Args:
        session_id: The worker session ID.
        close_tab: If True, close the browser tab (local workers only).
            False by default so that navigating away from a worker keeps
            its tab alive for later reconnection.

    Returns True if a view was stopped, False if none was active.
    """
    view = _active_views.pop(session_id, None)
    if view is None:
        return False

    view.status = "closed"

    # Stop screencast
    try:
        await _cdp_send(view.cdp_ws, "Page.stopScreencast")
    except Exception:
        pass

    # Close CDP WebSocket
    try:
        await view.cdp_ws.close()
    except Exception:
        pass

    # Close the tab (local, only on explicit close) or the tunnel (remote)
    if is_remote_host(view.host):
        try:
            close_tunnel(view.tunnel_local_port, view.host)
        except Exception:
            pass
    elif close_tab and view.target_id:
        _session_tab_targets.pop(session_id, None)
        try:
            await close_browser_tab(view.tunnel_local_port, view.target_id)
        except Exception:
            pass

    logger.info("Stopped browser view for session %s", session_id)
    return True


def stop_browser_view_sync(session_id: str, close_tab: bool = False) -> bool:
    """Synchronous version of stop_browser_view for cleanup hooks.

    Removes the session from the registry and cleans up the tunnel.
    The CDP WebSocket close is best-effort (may already be dead).

    Args:
        close_tab: If True, close the browser tab (local workers only).
    """
    view = _active_views.pop(session_id, None)
    if view is None:
        return False

    view.status = "closed"

    # Close CDP WebSocket (best-effort, may not work from sync context)
    try:
        # Schedule the close on the event loop if available
        loop = asyncio.get_running_loop()
        loop.create_task(view.cdp_ws.close())
    except RuntimeError:
        # No event loop — just close the tunnel, WS will die eventually
        pass

    # Close the tab (local, only on explicit close) or the tunnel (remote)
    if is_remote_host(view.host):
        try:
            close_tunnel(view.tunnel_local_port, view.host)
        except Exception:
            pass
    elif close_tab and view.target_id:
        _session_tab_targets.pop(session_id, None)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(close_browser_tab(view.tunnel_local_port, view.target_id))
        except RuntimeError:
            pass  # No event loop — tab will remain until Chrome is closed

    logger.info("Stopped browser view (sync) for session %s", session_id)
    return True


async def dispatch_mouse_event(
    view: BrowserViewSession,
    event_type: str,
    x: float,
    y: float,
    button: str = "left",
    click_count: int = 1,
    modifiers: int = 0,
) -> None:
    """Dispatch a mouse event to the browser via CDP."""
    params: dict[str, Any] = {
        "type": event_type,
        "x": x,
        "y": y,
        "button": button,
        "clickCount": click_count,
        "modifiers": modifiers,
    }
    await _cdp_send(view.cdp_ws, "Input.dispatchMouseEvent", params)


# Special key definitions for CDP Input.dispatchKeyEvent.
# CDP requires windowsVirtualKeyCode for non-printable keys, and text
# for keys that generate character input (e.g. Enter -> "\r").
_SPECIAL_KEYS: dict[str, dict[str, Any]] = {
    "Backspace": {"keyCode": 8},
    "Tab": {"keyCode": 9, "text": "\t"},
    "Enter": {"keyCode": 13, "text": "\r"},
    "Escape": {"keyCode": 27},
    "Delete": {"keyCode": 46},
    "ArrowDown": {"keyCode": 40},
    "ArrowLeft": {"keyCode": 37},
    "ArrowRight": {"keyCode": 39},
    "ArrowUp": {"keyCode": 38},
    "End": {"keyCode": 35},
    "Home": {"keyCode": 36},
    "PageDown": {"keyCode": 34},
    "PageUp": {"keyCode": 33},
    "Insert": {"keyCode": 45},
    "F1": {"keyCode": 112},
    "F2": {"keyCode": 113},
    "F3": {"keyCode": 114},
    "F4": {"keyCode": 115},
    "F5": {"keyCode": 116},
    "F6": {"keyCode": 117},
    "F7": {"keyCode": 118},
    "F8": {"keyCode": 119},
    "F9": {"keyCode": 120},
    "F10": {"keyCode": 121},
    "F11": {"keyCode": 122},
    "F12": {"keyCode": 123},
}


async def dispatch_key_event(
    view: BrowserViewSession,
    event_type: str,
    key: str = "",
    code: str = "",
    text: str = "",
    modifiers: int = 0,
    key_code: int = 0,
) -> None:
    """Dispatch a keyboard event to the browser via CDP."""
    params: dict[str, Any] = {
        "type": event_type,
        "modifiers": modifiers,
    }
    if key:
        params["key"] = key
    if code:
        params["code"] = code

    # Determine the virtual key code.  Prefer the browser-supplied keyCode
    # (which correctly maps punctuation like "." -> 190, not ord(".")==46
    # which collides with VK_DELETE).  Fall back to the special-key table,
    # then to uppercase-ASCII for letters/digits/space.
    if key_code:
        params["windowsVirtualKeyCode"] = key_code
        params["nativeVirtualKeyCode"] = key_code
    else:
        key_def = _SPECIAL_KEYS.get(key, {})
        if "keyCode" in key_def:
            params["windowsVirtualKeyCode"] = key_def["keyCode"]
            params["nativeVirtualKeyCode"] = key_def["keyCode"]
        elif len(key) == 1:
            params["windowsVirtualKeyCode"] = ord(key.upper())
            params["nativeVirtualKeyCode"] = ord(key.upper())

    # Set text: use explicit text from caller, fall back to special key text
    key_def = _SPECIAL_KEYS.get(key, {})
    if text:
        params["text"] = text
    elif event_type == "keyDown" and "text" in key_def:
        params["text"] = key_def["text"]

    await _cdp_send(view.cdp_ws, "Input.dispatchKeyEvent", params)


async def dispatch_scroll_event(
    view: BrowserViewSession,
    x: float,
    y: float,
    delta_x: float,
    delta_y: float,
    modifiers: int = 0,
) -> None:
    """Dispatch a mouse wheel event to the browser via CDP."""
    params: dict[str, Any] = {
        "type": "mouseWheel",
        "x": x,
        "y": y,
        "deltaX": delta_x,
        "deltaY": delta_y,
        "modifiers": modifiers,
    }
    await _cdp_send(view.cdp_ws, "Input.dispatchMouseEvent", params)


async def restart_screencast(view: BrowserViewSession) -> None:
    """Stop and restart the CDP screencast.

    Called when a new client WebSocket connects to an existing view whose
    screencast may be stalled (Chrome pauses frame delivery when acks stop).
    """
    try:
        await _cdp_send(view.cdp_ws, "Page.stopScreencast")
    except Exception:
        pass  # May already be stopped
    await _cdp_send(
        view.cdp_ws,
        "Page.startScreencast",
        {
            "format": "jpeg",
            "quality": view.quality,
            "maxWidth": view.viewport_width,
            "maxHeight": view.viewport_height,
            "everyNthFrame": 1,
        },
    )


async def reconnect_cdp(view: BrowserViewSession) -> None:
    """Close and reopen the CDP WebSocket, re-enabling events and screencast.

    Provides a clean WebSocket with no shared state from the previous
    client session (stale recv locks, flow control backpressure, etc.).
    Called when a new dashboard client reattaches to an existing view.
    """
    # Close old CDP WebSocket (best-effort, may already be dead)
    old_ws = view.cdp_ws
    try:
        await old_ws.close()
    except Exception:
        pass

    # Open fresh CDP WebSocket to the same target
    ws_url = f"ws://127.0.0.1:{view.tunnel_local_port}/devtools/page/{view.target_id}"
    cdp_ws = await websockets.asyncio.client.connect(
        ws_url,
        max_size=16 * 1024 * 1024,
        open_timeout=10,
        ping_interval=None,
    )
    view.cdp_ws = cdp_ws

    # Re-enable page events and dark theme
    await _cdp_send(cdp_ws, "Page.enable")
    await _cdp_send(
        cdp_ws,
        "Emulation.setEmulatedMedia",
        {"features": [{"name": "prefers-color-scheme", "value": "dark"}]},
    )
    await _cdp_send(
        cdp_ws,
        "Emulation.setDefaultBackgroundColorOverride",
        {"color": {"r": 30, "g": 30, "b": 30, "a": 1}},
    )

    # Restore viewport (must be set before screencast for correct zoom)
    view._zoom_percent = 100
    await _cdp_send_and_wait(
        cdp_ws,
        "Emulation.setDeviceMetricsOverride",
        {
            "width": view.viewport_width,
            "height": view.viewport_height,
            "deviceScaleFactor": 1,
            "mobile": False,
        },
    )

    # Start screencast
    await _cdp_send(
        cdp_ws,
        "Page.startScreencast",
        {
            "format": "jpeg",
            "quality": view.quality,
            "maxWidth": view.viewport_width,
            "maxHeight": view.viewport_height,
            "everyNthFrame": 1,
        },
    )


async def set_screencast_quality(view: BrowserViewSession, quality: int) -> None:
    """Update the screencast JPEG quality (requires stop+restart)."""
    quality = max(1, min(100, quality))
    if quality == view.quality:
        return

    await _cdp_send(view.cdp_ws, "Page.stopScreencast")
    view.quality = quality
    await _cdp_send(
        view.cdp_ws,
        "Page.startScreencast",
        {
            "format": "jpeg",
            "quality": quality,
            "maxWidth": view.viewport_width,
            "maxHeight": view.viewport_height,
            "everyNthFrame": 1,
        },
    )


async def set_viewport_zoom(view: BrowserViewSession, zoom_percent: int) -> None:
    """Adjust page zoom by overriding the device viewport size.

    zoom_percent=50 means content renders at 50% size (2x virtual viewport),
    showing more content in the same screencast area.
    """
    zoom_percent = max(25, min(200, zoom_percent))
    view._zoom_percent = zoom_percent
    scale = 100 / zoom_percent
    width = round(view.viewport_width * scale)
    height = round(view.viewport_height * scale)
    await _cdp_send(
        view.cdp_ws,
        "Emulation.setDeviceMetricsOverride",
        {
            "width": width,
            "height": height,
            "deviceScaleFactor": 1,
            "mobile": False,
        },
    )


async def relay_cdp_to_client(
    view: BrowserViewSession,
    send_binary: Any,  # async callable to send binary frames
    send_json: Any,  # async callable to send JSON messages
) -> None:
    """Read CDP messages and relay screencast frames to the client.

    Args:
        view: Active browser view session.
        send_binary: Coroutine to send binary (JPEG) frames to the client.
        send_json: Coroutine to send JSON messages to the client.
    """
    try:
        async for raw_msg in view.cdp_ws:
            msg = json.loads(raw_msg)

            # Resolve pending _cdp_call futures for response messages
            resp_id = msg.get("id")
            if resp_id is not None and resp_id in view._pending:
                view._pending[resp_id].set_result(msg)
                continue

            method = msg.get("method", "")

            if method == "Page.screencastFrame":
                params = msg["params"]
                # Decode base64 JPEG frame
                frame_data = base64.b64decode(params["data"])
                cdp_session_id = params["sessionId"]

                # Acknowledge the frame so CDP sends the next one
                await _cdp_send(
                    view.cdp_ws,
                    "Page.screencastFrameAck",
                    {"sessionId": cdp_session_id},
                )

                # Send raw JPEG bytes as binary WebSocket frame
                await send_binary(frame_data)

                # After delivering the frame, check if another CDP client
                # (e.g. Playwright) changed the viewport.  Re-assert ours
                # at most once per second to avoid a tug-of-war.
                metadata = params.get("metadata")
                if metadata:
                    scale = 100 / view._zoom_percent
                    exp_w = round(view.viewport_width * scale)
                    exp_h = round(view.viewport_height * scale)
                    dw = metadata.get("deviceWidth", exp_w)
                    dh = metadata.get("deviceHeight", exp_h)
                    if dw != exp_w or dh != exp_h:
                        now = time.monotonic()
                        if now - view._last_viewport_fix > 1.0:
                            view._last_viewport_fix = now
                            try:
                                await _cdp_send(
                                    view.cdp_ws,
                                    "Emulation.setDeviceMetricsOverride",
                                    {
                                        "width": exp_w,
                                        "height": exp_h,
                                        "deviceScaleFactor": 1,
                                        "mobile": False,
                                    },
                                )
                            except Exception:
                                pass

            elif method == "Page.frameNavigated":
                # Notify client of URL change (ignore chrome-error:// pages)
                frame = msg.get("params", {}).get("frame", {})
                url = frame.get("url", "")
                title = frame.get("name", "")
                if url and not url.startswith("chrome-error://"):
                    view.page_url = url
                if title:
                    view.page_title = title
                await send_json({"type": "navigate", "url": view.page_url, "title": title})

            elif method == "Page.screencastVisibilityChanged":
                visible = msg.get("params", {}).get("visible", True)
                await send_json({"type": "visibility", "visible": visible})

    except websockets.exceptions.ConnectionClosed:
        logger.info("CDP connection closed for session %s", view.session_id)
        await send_json({"type": "closed", "reason": "browser_closed"})
    except Exception as e:
        logger.error("CDP relay error for session %s: %s", view.session_id, e)
        await send_json({"type": "error", "message": f"CDP error: {e}"})


async def poll_url(
    view: BrowserViewSession,
    send_json: Any,
    interval: float = 0.5,
) -> None:
    """Periodically poll the browser URL via Runtime.evaluate.

    This is more reliable than relying on Page.frameNavigated events,
    which may not fire for SPA navigation, pushState, or hash changes.
    Only sends an update when the URL or title actually changes.
    """
    last_url = view.page_url
    last_title = view.page_title
    js = "JSON.stringify({url: location.href, title: document.title})"

    while True:
        await asyncio.sleep(interval)
        try:
            resp = await _cdp_call(
                view,
                "Runtime.evaluate",
                {
                    "expression": js,
                    "returnByValue": True,
                },
            )
            value = resp.get("result", {}).get("result", {}).get("value", "")
            if value:
                data = json.loads(value)
                url = data.get("url", "")
                title = data.get("title", "")
                if url and not url.startswith("chrome-error://"):
                    if url != last_url or title != last_title:
                        last_url = url
                        last_title = title
                        view.page_url = url
                        view.page_title = title
                        await send_json(
                            {
                                "type": "navigate",
                                "url": url,
                                "title": title,
                            }
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # Browser may be navigating; skip this tick


async def _navigate_history(view: BrowserViewSession, forward: bool = False) -> None:
    """Navigate back or forward using CDP history APIs."""
    try:
        resp = await _cdp_call(view, "Page.getNavigationHistory")
        result = resp.get("result", {})
        entries = result.get("entries", [])
        current = result.get("currentIndex", 0)
        target_idx = current + (1 if forward else -1)
        if 0 <= target_idx < len(entries):
            entry_id = entries[target_idx]["id"]
            await _cdp_send(
                view.cdp_ws,
                "Page.navigateToHistoryEntry",
                {"entryId": entry_id},
            )
    except Exception as e:
        logger.warning("History navigation failed: %s", e)


async def monitor_tabs(
    view: BrowserViewSession,
    interval: float = 2.0,
) -> str | None:
    """Monitor the remote browser for extra tabs created by Playwright MCP.

    Auto-switches the browser view in two cases:

    1. **New tab detected**: Playwright opened a tab we haven't seen before.
       Switch to it immediately since the worker wants to use that tab.
    2. **Stuck on about:blank**: Current view tab is about:blank but another
       tab has real content (e.g. after the initial tab was never navigated).

    This function is non-destructive: it never closes tabs.  Tab management
    is left to the user via the tabs dropdown in the UI.

    Returns the new target_id to switch to, or None if cancelled.
    Only useful for remote workers (one worker per Chrome instance).
    """
    # Seed with current tab so we don't immediately switch away from it
    if not view._known_tab_ids:
        view._known_tab_ids = {view.target_id}

    while True:
        await asyncio.sleep(interval)
        try:
            targets = await discover_browser_targets(view.tunnel_local_port, retries=1)
        except Exception:
            continue

        target_ids = {t.get("id", "") for t in targets} - {""}

        # Case 1: Detect newly created tabs (not seen before)
        new_ids = target_ids - view._known_tab_ids
        view._known_tab_ids = target_ids

        if new_ids:
            # Switch to the new tab — Playwright just opened it
            new_id = next(iter(new_ids))
            logger.info(
                "New tab %s detected, auto-switching for session %s",
                new_id,
                view.session_id,
            )
            return new_id

        if len(targets) <= 1:
            continue

        # Case 2: Current tab is about:blank — switch to one with content
        current_id = view.target_id
        current_url = ""
        for t in targets:
            if t.get("id") == current_id:
                current_url = t.get("url", "")
                break

        if current_url and current_url not in ("about:blank", ""):
            continue

        content_tabs = [
            t
            for t in targets
            if t.get("url", "about:blank") not in ("about:blank", "") and t.get("id") != current_id
        ]
        if content_tabs:
            keep_id = content_tabs[0].get("id", "")
            if keep_id:
                logger.info(
                    "Auto-switching from about:blank to tab %s (%s) for session %s",
                    keep_id,
                    content_tabs[0].get("url", ""),
                    view.session_id,
                )
                return keep_id

    return None  # unreachable; for type-checkers


async def switch_to_tab(view: BrowserViewSession, new_target_id: str) -> None:
    """Switch the browser view to a different browser tab.

    Closes the old CDP WebSocket, opens a new one to the target tab,
    re-enables page events / dark theme, and restarts the screencast.
    """
    old_ws = view.cdp_ws

    # Close old CDP WebSocket
    try:
        await old_ws.close()
    except Exception:
        pass

    # Activate new tab so Chrome renders it
    try:
        await activate_browser_tab(view.tunnel_local_port, new_target_id)
    except Exception:
        pass

    # Connect to the new target's page-level CDP WebSocket
    ws_url = f"ws://127.0.0.1:{view.tunnel_local_port}/devtools/page/{new_target_id}"
    cdp_ws = await websockets.asyncio.client.connect(
        ws_url,
        max_size=16 * 1024 * 1024,
        open_timeout=10,
        ping_interval=None,
    )
    view.cdp_ws = cdp_ws
    view.target_id = new_target_id

    # Re-enable page events and dark theme
    await _cdp_send(cdp_ws, "Page.enable")
    await _cdp_send(
        cdp_ws,
        "Emulation.setEmulatedMedia",
        {"features": [{"name": "prefers-color-scheme", "value": "dark"}]},
    )
    await _cdp_send(
        cdp_ws,
        "Emulation.setDefaultBackgroundColorOverride",
        {"color": {"r": 30, "g": 30, "b": 30, "a": 1}},
    )

    # Restore viewport
    view._zoom_percent = 100
    await _cdp_send_and_wait(
        cdp_ws,
        "Emulation.setDeviceMetricsOverride",
        {
            "width": view.viewport_width,
            "height": view.viewport_height,
            "deviceScaleFactor": 1,
            "mobile": False,
        },
    )

    # Start screencast
    await _cdp_send(
        cdp_ws,
        "Page.startScreencast",
        {
            "format": "jpeg",
            "quality": view.quality,
            "maxWidth": view.viewport_width,
            "maxHeight": view.viewport_height,
            "everyNthFrame": 1,
        },
    )

    # Fetch the new tab's URL and title
    try:
        resp = await _cdp_send_and_wait(
            cdp_ws,
            "Runtime.evaluate",
            {
                "expression": "JSON.stringify({url: location.href, title: document.title})",
                "returnByValue": True,
            },
        )
        value = resp.get("result", {}).get("result", {}).get("value", "")
        if value:
            data = json.loads(value)
            view.page_url = data.get("url", "")
            view.page_title = data.get("title", "")
    except Exception:
        pass

    # Update persistent tab tracking
    if not is_remote_host(view.host):
        _session_tab_targets[view.session_id] = new_target_id
        from orchestrator.browser.cdp_worker_proxy import _worker_proxies

        proxy_info = _worker_proxies.get(view.session_id)
        if proxy_info:
            proxy_info.target_id = new_target_id

    logger.info(
        "Switched browser view for %s to tab %s (%s)",
        view.session_id,
        new_target_id,
        view.page_url,
    )


async def handle_client_input(view: BrowserViewSession, msg: dict) -> None:
    """Handle an input event from the dashboard client.

    Dispatches mouse, keyboard, and scroll events to the browser via CDP.
    """
    msg_type = msg.get("type", "")

    if msg_type == "mouse":
        await dispatch_mouse_event(
            view,
            event_type=msg.get("event", "mouseMoved"),
            x=msg.get("x", 0),
            y=msg.get("y", 0),
            button=msg.get("button", "left"),
            click_count=msg.get("clickCount", 1),
            modifiers=msg.get("modifiers", 0),
        )

    elif msg_type == "key":
        await dispatch_key_event(
            view,
            event_type=msg.get("event", "keyDown"),
            key=msg.get("key", ""),
            code=msg.get("code", ""),
            text=msg.get("text", ""),
            modifiers=msg.get("modifiers", 0),
            key_code=msg.get("keyCode", 0),
        )

    elif msg_type == "scroll":
        await dispatch_scroll_event(
            view,
            x=msg.get("x", 0),
            y=msg.get("y", 0),
            delta_x=msg.get("deltaX", 0),
            delta_y=msg.get("deltaY", 0),
            modifiers=msg.get("modifiers", 0),
        )

    elif msg_type == "zoom":
        zoom = msg.get("zoom", 100)
        await set_viewport_zoom(view, zoom)

    elif msg_type == "quality":
        quality = msg.get("quality", 60)
        await set_screencast_quality(view, quality)

    elif msg_type == "navigate":
        url = msg.get("url", "")
        if url:
            await _cdp_send(view.cdp_ws, "Page.navigate", {"url": url})

    elif msg_type == "switchTab":
        target_id = msg.get("targetId", "")
        if target_id and target_id != view.target_id:
            # Signal the WS handler loop to perform the switch.
            # We can't do it here because it requires cancelling the
            # relay tasks and reopening the CDP WebSocket.
            view._switch_target = target_id

    elif msg_type == "closeTab":
        target_id = msg.get("targetId", "")
        if target_id and target_id != view.target_id:
            # Close a non-active tab directly via CDP HTTP API
            try:
                await close_browser_tab(view.tunnel_local_port, target_id)
            except Exception:
                pass
        elif target_id and target_id == view.target_id:
            # Closing the active tab — find another tab to switch to first
            try:
                targets = await discover_browser_targets(view.tunnel_local_port, retries=1)
                other = [t for t in targets if t.get("id") and t["id"] != target_id]
                if other:
                    # Switch to another tab, then close the old one
                    view._switch_target = other[0]["id"]
                    view._close_after_switch = target_id
            except Exception:
                pass

    elif msg_type in ("goBack", "goForward"):
        await _navigate_history(view, forward=(msg_type == "goForward"))
