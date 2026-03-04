"""WebSocket endpoint for browser view streaming via CDP screencast.

Protocol
--------
Two frame types coexist on the same WebSocket:

* **Binary frames** carry raw JPEG screencast bytes (server → client).
* **Text frames** carry JSON messages for control and input events.

Server → Client binary:  raw JPEG frame bytes (draw on canvas)
Server → Client JSON:    {"type": "navigate"|"visibility"|"closed"|"error"|"ping", ...}
Client → Server JSON:    {"type": "mouse"|"key"|"scroll"|"quality", ...}
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from orchestrator.browser.cdp_proxy import (
    cleanup_stale_view,
    get_active_view,
    handle_client_input,
    is_view_alive,
    poll_url,
    reconnect_cdp,
    relay_cdp_to_client,
    restart_screencast,
)

logger = logging.getLogger(__name__)

# Interval for server-side keepalive pings (seconds)
_KEEPALIVE_INTERVAL = 30


async def ws_browser_view(websocket: WebSocket, session_id: str):
    """Stream browser view frames and relay input for a session.

    Binary frames (server → client): JPEG screencast frames from CDP.
    JSON frames (client → server): Mouse, keyboard, scroll input events.
    JSON frames (server → client): Navigation, visibility, error, close events.

    The server-side CDP connection survives client WebSocket disconnects.
    New clients reattach to the existing view and restart the screencast.
    """
    await websocket.accept()

    view = get_active_view(session_id)
    if not view:
        await websocket.send_json({"type": "error", "message": "No active browser view"})
        await websocket.close(code=4004)
        return

    # If the view exists but the CDP WebSocket is dead, clean it up
    if not is_view_alive(session_id):
        await cleanup_stale_view(session_id)
        await websocket.send_json({"type": "error", "message": "Browser view CDP connection lost"})
        await websocket.close(code=4004)
        return

    # If a previous client was connected, cancel its CDP reader tasks and
    # open a fresh CDP WebSocket.  This avoids all shared-state issues:
    # - Stale recv() locks from websockets (concurrent recv crashes)
    # - Flow control backpressure (pause_reading blocks pong processing)
    # - Buffered frames from the old session
    is_reattach = bool(view._cdp_reader_tasks)
    if is_reattach:
        for task in view._cdp_reader_tasks:
            if not task.done():
                task.cancel()
        for task in view._cdp_reader_tasks:
            if not task.done():
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        view._cdp_reader_tasks = []
        view._pending.clear()  # Drop unresolved CDP response futures

        try:
            await reconnect_cdp(view)
        except Exception as e:
            logger.warning("CDP reconnect failed for session %s: %s", session_id, e)
            await websocket.send_json(
                {"type": "error", "message": "Failed to reconnect to browser"}
            )
            await websocket.close(code=4004)
            return
    else:
        # First client connection — screencast was started by start_browser_view(),
        # but restart it in case Chrome paused delivery (e.g. rapid connect/disconnect).
        try:
            await restart_screencast(view)
        except Exception:
            logger.debug("Failed to restart screencast for session %s", session_id)

    # NOTE: We intentionally do NOT call activate_browser_tab() here.
    # Tab activation is done once during start_browser_view() (the POST).
    # Calling it on every WS reconnect would bring Chrome to the foreground
    # on macOS, stealing focus from the user's app.

    # Send initial metadata
    await websocket.send_json(
        {
            "type": "metadata",
            "url": view.page_url,
            "title": view.page_title,
            "viewport": {
                "width": view.viewport_width,
                "height": view.viewport_height,
            },
        }
    )

    # Callbacks for the CDP relay
    async def send_binary(data: bytes) -> None:
        try:
            await websocket.send_bytes(data)
        except Exception:
            raise  # Let the relay loop handle it

    async def send_json(msg: dict) -> None:
        try:
            await websocket.send_json(msg)
        except Exception:
            raise

    # Task 1: Relay CDP screencast frames to the dashboard client
    cdp_relay_task = asyncio.create_task(relay_cdp_to_client(view, send_binary, send_json))

    # Task 2: Read client input and dispatch to CDP
    client_input_task = asyncio.create_task(_relay_client_input(websocket, view))

    # Task 3: Poll browser URL to keep the address bar in sync
    url_poll_task = asyncio.create_task(poll_url(view, send_json))

    # Task 4: Send keepalive pings to prevent idle WS drops
    keepalive_task = asyncio.create_task(_keepalive(websocket))

    # Track tasks that read from the CDP WebSocket so the next client
    # connection can cancel them before starting its own recv() loop.
    view._cdp_reader_tasks = [cdp_relay_task, url_poll_task]

    tasks = [cdp_relay_task, client_input_task, url_poll_task, keepalive_task]

    try:
        # Wait for any task to complete (first one wins)
        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel remaining tasks
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        # Check if the CDP relay ended (browser closed)
        for task in done:
            exc = task.exception() if not task.cancelled() else None
            if exc:
                logger.error(
                    "Browser view task failed for session %s: %s",
                    session_id,
                    exc,
                )

    except Exception as e:
        logger.error("Browser view WebSocket error for session %s: %s", session_id, e)
    finally:
        # Clean up: cancel any remaining local tasks
        for task in tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        # NOTE: We intentionally do NOT call stop_browser_view() here.
        # The CDP connection to Chrome survives client WS disconnects so
        # new clients can reattach without re-creating the view.
        # We also do NOT clear view._cdp_reader_tasks — if a new client
        # is already connecting, it needs to see and cancel these tasks.


async def _keepalive(websocket: WebSocket) -> None:
    """Send periodic pings to prevent idle WebSocket drops."""
    try:
        while True:
            await asyncio.sleep(_KEEPALIVE_INTERVAL)
            await websocket.send_json({"type": "ping"})
    except (WebSocketDisconnect, Exception):
        pass


async def _relay_client_input(websocket: WebSocket, view) -> None:
    """Read input events from the dashboard client and dispatch to CDP."""
    try:
        while True:
            ws_msg = await websocket.receive()

            # Binary frames from client are not expected
            if ws_msg.get("bytes"):
                continue

            raw = ws_msg.get("text", "")
            if not raw:
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            try:
                await handle_client_input(view, msg)
            except Exception as e:
                logger.debug(
                    "Input dispatch error for session %s: %s",
                    view.session_id,
                    e,
                )

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("Client input relay ended for session %s: %s", view.session_id, e)
