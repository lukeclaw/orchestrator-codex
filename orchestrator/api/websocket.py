"""Native Starlette WebSocket for real-time updates."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

from orchestrator.core.events import Event, subscribe

logger = logging.getLogger(__name__)

# Connected WebSocket clients
_clients: set[WebSocket] = set()

# Reference to the main asyncio event loop (set during app startup).
# Needed so sync handlers running in threadpool threads can schedule
# broadcasts via call_soon_threadsafe.
_event_loop: asyncio.AbstractEventLoop | None = None

# Latest focus URL received from frontend (updated via WebSocket)
_current_focus_url: str | None = None
_focus_event: asyncio.Event | None = None


def init_event_loop() -> None:
    """Store a reference to the running event loop.

    Must be called from an async context during app startup (e.g., lifespan).
    """
    global _event_loop
    _event_loop = asyncio.get_running_loop()


def get_current_focus() -> str | None:
    """Get the current focus URL (set by frontend via WebSocket)."""
    return _current_focus_url


def set_current_focus(url: str | None) -> None:
    """Set the current focus URL."""
    global _current_focus_url
    _current_focus_url = url


async def request_focus_from_frontend(timeout: float = 1.0) -> str | None:
    """Request current URL from frontend via WebSocket and wait for response."""
    global _focus_event

    if not _clients:
        return _current_focus_url  # No clients, return cached

    _focus_event = asyncio.Event()

    # Request focus from all connected clients
    await broadcast({"type": "request_focus"})

    try:
        await asyncio.wait_for(_focus_event.wait(), timeout=timeout)
    except TimeoutError:
        pass
    finally:
        _focus_event = None

    return _current_focus_url


async def websocket_endpoint(websocket: WebSocket):
    """Handle WebSocket connections for real-time dashboard updates."""
    global _current_focus_url, _focus_event

    await websocket.accept()
    _clients.add(websocket)
    logger.info("WebSocket client connected (total: %d)", len(_clients))

    try:
        while True:
            # Keep connection alive; handle incoming messages
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                msg_type = msg.get("type")

                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                elif msg_type == "focus_response":
                    # Frontend responded with current URL
                    _current_focus_url = msg.get("url")
                    if _focus_event:
                        _focus_event.set()
                elif msg_type == "focus_update":
                    # Frontend proactively sending focus update
                    _current_focus_url = msg.get("url")
                elif msg_type == "user_activity":
                    tracker = getattr(websocket.app.state, "human_tracker", None)
                    if tracker:
                        tracker.record_heartbeat()
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(websocket)
        logger.info("WebSocket client disconnected (total: %d)", len(_clients))


async def broadcast(message: dict[str, Any]):
    """Broadcast a message to all connected WebSocket clients."""
    if not _clients:
        return

    data = json.dumps(message)
    disconnected = set()

    for client in list(_clients):
        try:
            await client.send_text(data)
        except Exception:
            disconnected.add(client)

    _clients.difference_update(disconnected)


def _on_event(event: Event):
    """Bridge between sync event bus and async WebSocket broadcast.

    Works from both async contexts (same thread as the event loop) and
    sync contexts (threadpool threads used by FastAPI for sync handlers).
    """
    message = {
        "type": event.type,
        "data": event.data,
        "timestamp": event.timestamp,
    }

    # Fast path: called from within the event loop thread
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast(message))
        return
    except RuntimeError:
        pass

    # Slow path: called from a threadpool thread (sync route handler).
    # Use call_soon_threadsafe to schedule the coroutine on the main loop.
    loop = _event_loop
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(loop.create_task, broadcast(message))


# Subscribe to all events for WebSocket broadcasting
subscribe("*", _on_event)
