"""WebSocket endpoint for live terminal streaming via tmux."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from orchestrator.terminal.manager import (
    capture_pane_with_escapes,
    clear_pane,
    ensure_window,
    send_keys_literal,
    resize_pane,
)

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
        while poll_active:
            await asyncio.sleep(0.15)
            if not initial_sent:
                continue
            try:
                content = capture_pane_with_escapes(tmux_sess, tmux_win)
                if content != last_content:
                    await websocket.send_json({"type": "output", "data": content})
                    last_content = content
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
                send_keys_literal(tmux_sess, tmux_win, msg.get("data", ""))
            elif msg.get("type") == "resize":
                cols = msg.get("cols", 80)
                rows = msg.get("rows", 24)
                resize_pane(tmux_sess, tmux_win, cols, rows)

                if not initial_sent:
                    # Clear old content that was formatted at the wrong width,
                    # then capture the fresh screen at the correct dimensions.
                    clear_pane(tmux_sess, tmux_win)
                    await asyncio.sleep(0.3)
                    content = capture_pane_with_escapes(tmux_sess, tmux_win)
                    await websocket.send_json({"type": "output", "data": content})
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
