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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import websockets
import websockets.asyncio.client
import websockets.exceptions

from orchestrator.session.tunnel import close_tunnel, create_tunnel

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
    page_url: str = ""  # Current page URL
    page_title: str = ""  # Current page title
    viewport_width: int = 1280
    viewport_height: int = 960
    quality: int = 60
    status: str = "active"  # "active" | "closed"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


# In-memory registry: session_id -> BrowserViewSession
_active_views: dict[str, BrowserViewSession] = {}


def get_active_view(session_id: str) -> BrowserViewSession | None:
    """Get the active browser view for a session, or None."""
    return _active_views.get(session_id)


def list_active_views() -> list[str]:
    """Return session IDs that have active browser views."""
    return list(_active_views.keys())


async def discover_browser_targets(cdp_port: int) -> list[dict]:
    """Query CDP /json endpoint to find debuggable pages.

    Returns list of targets with fields: id, title, url, webSocketDebuggerUrl.
    Filters to type='page' targets only.
    """
    url = f"http://localhost:{cdp_port}/json"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        targets = resp.json()
        return [t for t in targets if t.get("type") == "page"]


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

    # Step 1: Create SSH tunnel for CDP port
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

    # Step 2: Discover page targets
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
            f"No debuggable pages found on CDP port {cdp_port}. The browser may have no open tabs."
        )

    target = targets[0]
    ws_url = target.get("webSocketDebuggerUrl", "")
    if not ws_url:
        close_tunnel(local_port, host)
        raise RuntimeError("Target has no webSocketDebuggerUrl")

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
        )
    except Exception as e:
        close_tunnel(local_port, host)
        raise RuntimeError(f"Failed to connect to CDP WebSocket: {e}") from e

    # Step 4: Start screencast
    try:
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
        close_tunnel(local_port, host)
        raise RuntimeError(f"Failed to start screencast: {e}") from e

    view = BrowserViewSession(
        session_id=session_id,
        host=host,
        cdp_ws=cdp_ws,
        tunnel_local_port=local_port,
        page_url=target.get("url", ""),
        page_title=target.get("title", ""),
        viewport_width=max_width,
        viewport_height=max_height,
        quality=quality,
    )
    _active_views[session_id] = view

    logger.info(
        "Started browser view for session %s: %s (%s)",
        session_id,
        view.page_title,
        view.page_url,
    )

    return view


async def stop_browser_view(session_id: str) -> bool:
    """Stop the browser view and clean up resources.

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

    # Close SSH tunnel
    try:
        close_tunnel(view.tunnel_local_port, view.host)
    except Exception:
        pass

    logger.info("Stopped browser view for session %s", session_id)
    return True


def stop_browser_view_sync(session_id: str) -> bool:
    """Synchronous version of stop_browser_view for cleanup hooks.

    Removes the session from the registry and cleans up the tunnel.
    The CDP WebSocket close is best-effort (may already be dead).
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

    # Close SSH tunnel
    try:
        close_tunnel(view.tunnel_local_port, view.host)
    except Exception:
        pass

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


async def dispatch_key_event(
    view: BrowserViewSession,
    event_type: str,
    key: str = "",
    code: str = "",
    text: str = "",
    modifiers: int = 0,
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
    if text:
        params["text"] = text
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

            elif method == "Page.frameNavigated":
                # Notify client of URL change
                frame = msg.get("params", {}).get("frame", {})
                url = frame.get("url", "")
                title = frame.get("name", "")
                if url:
                    view.page_url = url
                if title:
                    view.page_title = title
                await send_json({"type": "navigate", "url": url, "title": title})

            elif method == "Page.screencastVisibilityChanged":
                visible = msg.get("params", {}).get("visible", True)
                await send_json({"type": "visibility", "visible": visible})

    except websockets.exceptions.ConnectionClosed:
        logger.info("CDP connection closed for session %s", view.session_id)
        await send_json({"type": "closed", "reason": "browser_closed"})
    except Exception as e:
        logger.error("CDP relay error for session %s: %s", view.session_id, e)
        await send_json({"type": "error", "message": f"CDP error: {e}"})


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

    elif msg_type == "quality":
        quality = msg.get("quality", 60)
        await set_screencast_quality(view, quality)
