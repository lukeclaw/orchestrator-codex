"""WebSocket endpoint for live terminal streaming via tmux."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time

from fastapi import WebSocket, WebSocketDisconnect

from orchestrator.terminal.manager import (
    capture_pane_with_escapes,
    ensure_window,
    send_keys_literal,
    resize_pane,
)
from orchestrator.terminal.control import (
    TmuxControlPool,
    send_keys_async,
    resize_async,
    capture_pane_with_cursor_atomic_async,
    capture_pane_with_history_async,
    get_pane_id_async,
    check_alternate_screen_async,
)

logger = logging.getLogger(__name__)

# Tmux session name used by the orchestrator
TMUX_SESSION = "orchestrator"

# Track last user input time per session (for user activity detection)
# Key: session_id, Value: timestamp (time.time())
_session_last_input: dict[str, float] = {}

# How long to wait for user activity before background connection ops (seconds)
USER_ACTIVITY_TIMEOUT = 30


def record_user_input(session_id: str) -> None:
    """Record that user sent input to a session."""
    _session_last_input[session_id] = time.time()


def is_user_active(session_id: str, timeout: float = USER_ACTIVITY_TIMEOUT) -> bool:
    """Check if user has been active in a session within the timeout window.
    
    Used by background operations (reconnect, health-check) to avoid
    interfering with user typing. Screen syncs should NOT use this.
    """
    last_input = _session_last_input.get(session_id)
    if last_input is None:
        return False
    return (time.time() - last_input) < timeout


def clear_user_activity(session_id: str) -> None:
    """Clear activity tracking for a session (on disconnect/delete)."""
    _session_last_input.pop(session_id, None)


def _get_conn(websocket: WebSocket):
    """Get a database connection, preferring factory for thread safety."""
    factory = getattr(websocket.app.state, "conn_factory", None)
    if factory:
        return factory.create()
    return websocket.app.state.conn


async def terminal_websocket(websocket: WebSocket, session_id: str):
    """Stream terminal output and relay input for a session."""
    await websocket.accept()

    # Determine the tmux window name — use the session name from DB if possible
    conn = _get_conn(websocket)
    owns_conn = getattr(websocket.app.state, "conn_factory", None) is not None
    try:
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
    finally:
        if owns_conn:
            conn.close()

    # Wait for the client to send a resize before capturing initial content.
    # This ensures the tmux pane matches xterm's dimensions.
    initial_sent = False
    last_content = ""
    last_cursor_x = -1
    last_cursor_y = -1

    # --- Resolve pane ID for %output streaming --------------------------------
    pane_id = await get_pane_id_async(tmux_sess, tmux_win)
    stream_active = False
    conn = None  # TmuxControlConnection (set when streaming)
    drift_task: asyncio.Task | None = None

    # Callback invoked by TmuxControlConnection._read_output for our pane
    async def on_pane_output(raw_bytes: bytes) -> None:
        if not initial_sent:
            return
        try:
            encoded = base64.b64encode(raw_bytes).decode('ascii')
            await websocket.send_json({"type": "stream", "data": encoded})
        except Exception:
            pass  # WebSocket may have closed

    # --- Drift correction (background sync every 5s) --------------------------
    async def drift_correction():
        while True:
            await asyncio.sleep(5)
            if not initial_sent:
                continue
            try:
                content, cx, cy = await capture_pane_with_cursor_atomic_async(tmux_sess, tmux_win)
                await websocket.send_json({
                    "type": "sync",
                    "data": content,
                    "cursorX": cx,
                    "cursorY": cy,
                })
            except Exception:
                pass

    # --- Fallback poll loop (used when pane_id resolution fails) --------------
    poll_active = False

    async def poll_output():
        nonlocal last_content, last_cursor_x, last_cursor_y
        poll_interval = 0.02
        idle_count = 0

        while poll_active:
            await asyncio.sleep(poll_interval)
            if not initial_sent:
                continue
            try:
                content, cursor_x, cursor_y = await capture_pane_with_cursor_atomic_async(tmux_sess, tmux_win)

                content_changed = content != last_content
                cursor_changed = cursor_x != last_cursor_x or cursor_y != last_cursor_y

                if content_changed:
                    idle_count = 0
                    poll_interval = 0.02
                    await websocket.send_json({
                        "type": "output",
                        "data": content,
                        "cursorX": cursor_x,
                        "cursorY": cursor_y,
                    })
                    last_content = content
                    last_cursor_x = cursor_x
                    last_cursor_y = cursor_y
                elif cursor_changed:
                    idle_count = 0
                    poll_interval = 0.02
                    await websocket.send_json({
                        "type": "cursor",
                        "cursorX": cursor_x,
                        "cursorY": cursor_y,
                    })
                    last_cursor_x = cursor_x
                    last_cursor_y = cursor_y
                else:
                    idle_count += 1
                    if idle_count > 20:
                        poll_interval = min(0.15, poll_interval + 0.01)
            except Exception:
                pass

    # --- Start streaming or fall back to polling ------------------------------
    poll_task: asyncio.Task | None = None

    if pane_id:
        pool = TmuxControlPool.get_instance()
        conn = await pool.get_connection(tmux_sess)
        await conn.subscribe(pane_id, on_pane_output)
        stream_active = True
        drift_task = asyncio.create_task(drift_correction())
        logger.info("Streaming %output for pane %s (session %s)", pane_id, tmux_sess)
    else:
        logger.warning("Could not resolve pane ID for %s:%s — falling back to polling", tmux_sess, tmux_win)
        poll_active = True
        poll_task = asyncio.create_task(poll_output())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "input":
                record_user_input(session_id)
                asyncio.create_task(send_keys_async(tmux_sess, tmux_win, msg.get("data", "")))
            elif msg.get("type") == "request_history":
                scrollback = msg.get("lines", 1000)
                try:
                    content, cursor_x, cursor_y, total_lines = await capture_pane_with_history_async(
                        tmux_sess, tmux_win, scrollback
                    )
                    if content.endswith('\n'):
                        content = content[:-1]
                    await websocket.send_json({
                        "type": "history",
                        "data": content,
                        "cursorX": cursor_x,
                        "cursorY": cursor_y,
                        "totalLines": total_lines,
                    })
                    last_content = content
                    last_cursor_x = cursor_x
                    last_cursor_y = cursor_y
                except Exception as e:
                    logger.error("Failed to capture history: %s", e)
            elif msg.get("type") == "resize":
                cols = msg.get("cols", 80)
                rows = msg.get("rows", 24)
                await resize_async(tmux_sess, tmux_win, cols, rows)

                if not initial_sent:
                    await asyncio.sleep(0.05)

                    # Check if the app is using alternate screen buffer.
                    # TUI apps (Claude Code / Ink, vim, htop, etc.) switch to
                    # alternate screen on startup.  xterm.js must be in the
                    # same mode so that cursor-positioning escape sequences
                    # from the PTY stream land at the correct rows.
                    alternate_on = await check_alternate_screen_async(tmux_sess, tmux_win)

                    content, cursor_x, cursor_y, total_lines = await capture_pane_with_history_async(
                        tmux_sess, tmux_win, scrollback_lines=1000
                    )
                    # Strip trailing newline — capture-pane outputs every row
                    # (including the last) followed by \n.  With convertEol
                    # the final \n scrolls the viewport up by 1 line.
                    if content.endswith('\n'):
                        content = content[:-1]
                    await websocket.send_json({
                        "type": "history",
                        "data": content,
                        "cursorX": cursor_x,
                        "cursorY": cursor_y,
                        "totalLines": total_lines,
                        "alternateScreen": alternate_on,
                    })
                    last_content = content
                    last_cursor_x = cursor_x
                    last_cursor_y = cursor_y

                    if stream_active:
                        # Resize bounce triggers SIGWINCH → full app redraw
                        # via the PTY stream.  Keep initial_sent False during
                        # the bounce so stale incremental updates are dropped.
                        await resize_async(tmux_sess, tmux_win, cols, rows + 1)
                        await asyncio.sleep(0.05)
                        await resize_async(tmux_sess, tmux_win, cols, rows)

                    initial_sent = True
    except WebSocketDisconnect:
        pass
    finally:
        # --- Cleanup ----------------------------------------------------------
        if stream_active and conn and pane_id:
            await conn.unsubscribe(pane_id, on_pane_output)
        if drift_task:
            drift_task.cancel()
            try:
                await drift_task
            except asyncio.CancelledError:
                pass
        if poll_task:
            poll_active = False
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
        clear_user_activity(session_id)
