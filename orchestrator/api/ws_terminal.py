"""WebSocket endpoint for live terminal streaming via tmux."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from orchestrator.terminal.manager import (
    capture_pane_with_escapes,
    ensure_window,
    send_keys_literal,
    resize_pane,
)
from orchestrator.terminal.control import send_keys_async, resize_async, capture_pane_with_cursor_async

logger = logging.getLogger(__name__)

# Tmux session name used by the orchestrator
TMUX_SESSION = "orchestrator"


async def terminal_websocket(websocket: WebSocket, session_id: str):
    """Stream terminal output and relay input for a session."""
    await websocket.accept()

    # Determine the tmux window name — use the session name from DB if possible
    conn = websocket.app.state.conn
    row = conn.execute(
        "SELECT name, tmux_window FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()

    if not row:
        await websocket.send_json({"type": "error", "message": f"Session {session_id} not found"})
        await websocket.close()
        return

    session_name = row["name"]
    tmux_window = row["tmux_window"] or session_name

    # Parse tmux target — could be "session:window" or just "window"
    if ":" in tmux_window:
        tmux_sess, tmux_win = tmux_window.split(":", 1)
    else:
        tmux_sess = TMUX_SESSION
        tmux_win = tmux_window

    # Auto-create tmux session and window if they don't exist
    try:
        target = ensure_window(tmux_sess, tmux_win)
        logger.info("Terminal ready: %s", target)

        # Store the tmux_window back to DB if it was auto-created
        if not row["tmux_window"]:
            conn.execute(
                "UPDATE sessions SET tmux_window = ? WHERE id = ?",
                (f"{tmux_sess}:{tmux_win}", session_id),
            )
            conn.commit()
    except Exception as e:
        logger.exception("Failed to create tmux session/window")
        await websocket.send_json({
            "type": "error",
            "message": f"Failed to create terminal: {e}",
        })
        await websocket.close()
        return

    # Wait for the client to send a resize before capturing initial content.
    # This ensures the tmux pane matches xterm's dimensions.
    initial_sent = False
    last_content = ""
    poll_active = True

    async def poll_output():
        nonlocal last_content
        poll_interval = 0.05  # Start fast (50ms)
        idle_count = 0
        
        while poll_active:
            await asyncio.sleep(poll_interval)
            if not initial_sent:
                continue
            try:
                content, cursor_x, cursor_y = await capture_pane_with_cursor_async(tmux_sess, tmux_win)
                
                if content != last_content:
                    # Content changed - send update and reset to fast polling
                    idle_count = 0
                    poll_interval = 0.05  # 50ms when active
                    
                    await websocket.send_json({
                        "type": "output",
                        "data": content,
                        "cursorX": cursor_x,
                        "cursorY": cursor_y,
                    })
                    last_content = content
                else:
                    # No change - gradually slow down polling
                    idle_count += 1
                    if idle_count > 10:
                        poll_interval = min(0.2, poll_interval + 0.02)  # Slow to 200ms max
            except Exception:
                pass

    poll_task = asyncio.create_task(poll_output())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "input":
                # Use async control mode for lower latency input
                await send_keys_async(tmux_sess, tmux_win, msg.get("data", ""))
                # Immediately capture and send update for responsiveness
                try:
                    content, cursor_x, cursor_y = await capture_pane_with_cursor_async(tmux_sess, tmux_win)
                    if content != last_content:
                        await websocket.send_json({
                            "type": "output",
                            "data": content,
                            "cursorX": cursor_x,
                            "cursorY": cursor_y,
                        })
                        last_content = content
                except Exception:
                    pass
            elif msg.get("type") == "resize":
                cols = msg.get("cols", 80)
                rows = msg.get("rows", 24)
                # Use async control mode for resize too
                await resize_async(tmux_sess, tmux_win, cols, rows)

                if not initial_sent:
                    # Give tmux a moment to apply the resize
                    await asyncio.sleep(0.05)

                    # Capture visible pane content with cursor position
                    content, cursor_x, cursor_y = await capture_pane_with_cursor_async(tmux_sess, tmux_win)
                    await websocket.send_json({
                        "type": "output",
                        "data": content,
                        "cursorX": cursor_x,
                        "cursorY": cursor_y,
                    })
                    last_content = content
                    initial_sent = True
    except WebSocketDisconnect:
        pass
    finally:
        poll_active = False
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
