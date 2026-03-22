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
    close_browser_tab,
    get_active_view,
    handle_client_input,
    is_view_alive,
    monitor_tabs,
    poll_url,
    reconnect_cdp,
    relay_cdp_to_client,
    restart_screencast,
    switch_to_tab,
)
from orchestrator.terminal.ssh import is_remote_host

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
            "targetId": view.target_id,
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

    # Main streaming loop — restarts when a tab switch is detected.
    tasks: list[asyncio.Task] = []
    while True:
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

        # Task 5 (remote only): Monitor for extra tabs created by Playwright MCP.
        # Remote workers have one Chrome per worker (no CDP proxy filtering),
        # so Playwright may open a new tab that the view doesn't track.
        tab_monitor_task: asyncio.Task | None = None
        if is_remote_host(view.host):
            tab_monitor_task = asyncio.create_task(monitor_tabs(view))
            tasks.append(tab_monitor_task)

        # Task 6: Watch for user-initiated tab switch (set via handle_client_input).
        # The switchTab message sets view._switch_target; this task exits when set.
        view._switch_target = ""
        switch_watch_task = asyncio.create_task(_watch_switch_target(view))
        tasks.append(switch_watch_task)

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

            # Check for tab switch — either from monitor or user request
            new_target_id: str | None = None
            close_old_tab = False

            # Automatic switch from tab monitor (closes old about:blank tab)
            if (
                tab_monitor_task is not None
                and tab_monitor_task in done
                and not tab_monitor_task.cancelled()
            ):
                try:
                    new_target_id = tab_monitor_task.result()
                    close_old_tab = True
                except Exception:
                    pass

            # User-initiated switch via dropdown
            if not new_target_id and switch_watch_task in done:
                new_target_id = view._switch_target or None

            if new_target_id:
                old_target_id = view.target_id
                try:
                    await switch_to_tab(view, new_target_id)
                    # Close the old tab for auto-detected switches (about:blank)
                    # or when the user explicitly requested closing via closeTab
                    close_target = None
                    if close_old_tab and old_target_id:
                        close_target = old_target_id
                    elif view._close_after_switch:
                        close_target = view._close_after_switch
                        view._close_after_switch = ""
                    if close_target:
                        try:
                            await close_browser_tab(view.tunnel_local_port, close_target)
                        except Exception:
                            pass
                    # Notify the client of the new page
                    await websocket.send_json(
                        {
                            "type": "metadata",
                            "url": view.page_url,
                            "title": view.page_title,
                            "targetId": view.target_id,
                            "viewport": {
                                "width": view.viewport_width,
                                "height": view.viewport_height,
                            },
                        }
                    )
                    logger.info(
                        "Tab switch complete for session %s, restarting relay",
                        session_id,
                    )
                    continue  # Restart loop with new relay tasks
                except Exception as e:
                    logger.warning("Tab switch failed for session %s: %s", session_id, e)
                    # Fall through to normal exit

            # Normal exit — check for task errors
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

        break  # Exit the loop on normal completion or error

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


async def _watch_switch_target(view) -> None:
    """Poll view._switch_target and exit when the user requests a tab switch.

    This is intentionally a fast poll (100ms) so the UI feels responsive.
    The actual tab switch is performed by the main WS handler loop.
    """
    while not view._switch_target:
        await asyncio.sleep(0.1)


async def _relay_client_input(websocket: WebSocket, view) -> None:
    """Read input events from the dashboard client and dispatch to CDP."""
    try:
        while True:
            ws_msg = await websocket.receive()

            if ws_msg.get("type") == "websocket.disconnect":
                break

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
