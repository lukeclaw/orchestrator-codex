"""WebSocket endpoint for browser view streaming via CDP screencast.

Protocol
--------
Two frame types coexist on the same WebSocket:

* **Binary frames** carry raw JPEG screencast bytes (server → client).
* **Text frames** carry JSON messages for control and input events.

Server → Client binary:  raw JPEG frame bytes (draw on canvas)
Server → Client JSON:    {"type": "navigate"|"visibility"|"closed"|"error", ...}
Client → Server JSON:    {"type": "mouse"|"key"|"scroll"|"quality", ...}
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from orchestrator.browser.cdp_proxy import (
    get_active_view,
    handle_client_input,
    poll_url,
    relay_cdp_to_client,
    stop_browser_view,
)
from orchestrator.core.events import Event, publish

logger = logging.getLogger(__name__)


async def ws_browser_view(websocket: WebSocket, session_id: str):
    """Stream browser view frames and relay input for a session.

    Binary frames (server → client): JPEG screencast frames from CDP.
    JSON frames (client → server): Mouse, keyboard, scroll input events.
    JSON frames (server → client): Navigation, visibility, error, close events.
    """
    await websocket.accept()

    view = get_active_view(session_id)
    if not view:
        await websocket.send_json({"type": "error", "message": "No active browser view"})
        await websocket.close(code=4004)
        return

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

    tasks = [cdp_relay_task, client_input_task, url_poll_task]

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
        # Clean up: cancel any remaining tasks
        for task in tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        # Clean up the browser view (closes CDP WebSocket + tunnel, keeps Chromium running)
        try:
            stopped = await stop_browser_view(session_id)
            if stopped:
                publish(
                    Event(
                        type="browser_view_closed",
                        data={"session_id": session_id},
                    )
                )
        except Exception:
            logger.debug("View cleanup in WS handler for session %s failed", session_id)


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
