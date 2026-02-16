"""WebSocket endpoint for live terminal streaming via tmux.

Protocol
--------
Two frame types coexist on the same WebSocket:

* **Binary frames** carry raw PTY bytes (stream data) — zero overhead.
* **Text frames** carry JSON messages for everything else (sync, history,
  error, input, resize, ack).

Server → Client binary:  raw PTY bytes (write directly to xterm.js)
Server → Client JSON:    {"type": "sync"|"history"|"error", ...}
Client → Server JSON:    {"type": "input"|"resize"|"request_history"|"request_sync", ...}
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import zlib

from fastapi import WebSocket, WebSocketDisconnect

from orchestrator.terminal.control import (
    TmuxControlPool,
    capture_pane_with_cursor_atomic_async,
    capture_pane_with_history_async,
    check_alternate_screen_async,
    get_pane_id_async,
    resize_async,
    send_keys_async,
)
from orchestrator.terminal.manager import ensure_window, tmux_target

logger = logging.getLogger(__name__)

# Track last user input time per session (for user activity detection)
# Key: session_id, Value: timestamp (time.time())
_session_last_input: dict[str, float] = {}

# How long to wait for user activity before background connection ops (seconds)
USER_ACTIVITY_TIMEOUT = 30

# Flow control: snapshot recovery threshold.  When the stream buffer
# accumulates more than this many bytes without being flushed, we discard
# the stale buffer and schedule an immediate sync (capture-pane) instead.
# This replaces the old drop-based approach that silently lost bytes.
SNAPSHOT_RECOVERY_THRESHOLD = 256_000  # ~256 KB


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

    # Look up the session name from DB to derive the tmux target
    db_conn = _get_conn(websocket)
    owns_conn = getattr(websocket.app.state, "conn_factory", None) is not None
    try:
        row = db_conn.execute(
            "SELECT name FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()

        if not row:
            await websocket.send_json({
                "type": "error",
                "message": f"Session {session_id} not found",
            })
            await websocket.close()
            return

        tmux_sess, tmux_win = tmux_target(row["name"])

        # Auto-create tmux session and window if they don't exist
        try:
            target = ensure_window(tmux_sess, tmux_win)
            logger.info("Terminal ready: %s", target)
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
            db_conn.close()

    # Wait for the client to send a resize before capturing initial content.
    # This ensures the tmux pane matches xterm's dimensions.
    initial_sent = False

    # --- Resolve pane ID for %output streaming --------------------------------
    pane_id = await get_pane_id_async(tmux_sess, tmux_win)
    stream_active = False
    conn = None  # TmuxControlConnection (set when streaming)
    drift_task: asyncio.Task | None = None

    # --- Stream batching & flow control state ---------------------------------
    stream_buffer = bytearray()          # accumulates %output bytes
    flush_event = asyncio.Event()        # signals that stream_buffer has data
    last_flush_time: float = 0.0         # monotonic time of last successful flush
    sync_requested = False               # set True to trigger an immediate sync
    sync_in_progress = False             # prevents flusher from sending during sync

    # Callback invoked by TmuxControlConnection._read_output for our pane.
    # Subscription is deferred until AFTER initial history is sent so there
    # is no startup gap to buffer across.
    #
    # NEVER drops bytes.  If the buffer grows too large (client can't keep
    # up), we discard the stale buffer and request a full sync instead.
    async def on_pane_output(raw_bytes: bytes) -> None:
        nonlocal sync_requested
        stream_buffer.extend(raw_bytes)
        if len(stream_buffer) > SNAPSHOT_RECOVERY_THRESHOLD:
            # Buffer has grown too large — the client can't keep up.
            # Discard the stale incremental stream and request a fresh
            # full-screen snapshot.  This is the terminal-safe equivalent
            # of "I've fallen behind, just show me the current state."
            stream_buffer.clear()
            flush_event.clear()
            sync_requested = True
            logger.debug(
                "Snapshot recovery: buffer exceeded %d bytes",
                SNAPSHOT_RECOVERY_THRESHOLD,
            )
            return
        flush_event.set()

    async def stream_flusher():
        """Batch stream bytes and send as binary WebSocket frames (~60 fps)."""
        nonlocal last_flush_time
        while True:
            await flush_event.wait()   # zero-cost when idle
            await asyncio.sleep(0.016) # ~16ms batch window (one frame)
            flush_event.clear()
            # Skip flush while a sync is in progress — the sync will
            # discard these bytes (they're already in the capture) and
            # we must not send them after the sync JSON.
            if stream_buffer and not sync_in_progress:
                data = bytes(stream_buffer)
                stream_buffer.clear()
                try:
                    await websocket.send_bytes(data)
                    last_flush_time = asyncio.get_event_loop().time()
                except Exception:
                    break  # WebSocket closed

    # --- Helpers for sync with divergence hash ---------------------------------
    async def _send_sync():
        """Capture pane and send a sync message with CRC32 hash."""
        nonlocal sync_in_progress

        # Block the stream flusher while we capture + send.  Any bytes
        # that arrive during the capture are already reflected in the
        # snapshot and must not be flushed after the sync JSON.
        sync_in_progress = True
        try:
            stream_buffer.clear()
            flush_event.clear()

            content, cx, cy = await capture_pane_with_cursor_atomic_async(
                tmux_sess, tmux_win
            )

            # Discard bytes that accumulated during the capture — they
            # are already included in the snapshot.
            stream_buffer.clear()
            flush_event.clear()

            if content.endswith('\n'):
                content = content[:-1]
            # CRC32 of the plain-text content (strip ANSI for stable hash)
            content_hash = zlib.crc32(content.encode("utf-8")) & 0xFFFFFFFF
            await websocket.send_json({
                "type": "sync",
                "data": content,
                "cursorX": cx,
                "cursorY": cy,
                "hash": content_hash,
            })
        finally:
            sync_in_progress = False

    # --- Drift correction (background sync) ------------------------------------
    async def drift_correction():
        nonlocal sync_requested, pane_id, stream_active

        # Early sync: correct any desync from the brief gap between
        # history capture and streaming start.
        await asyncio.sleep(0.15)
        if initial_sent:
            try:
                await _send_sync()
            except Exception:
                pass

        # Regular interval
        while True:
            await asyncio.sleep(2)
            if not initial_sent:
                continue

            # --- Detect pane ID change (window destroyed & recreated) ---
            try:
                new_pane_id = await get_pane_id_async(tmux_sess, tmux_win)
                if new_pane_id and new_pane_id != pane_id:
                    logger.info(
                        "Pane ID changed for %s:%s: %s -> %s, re-subscribing",
                        tmux_sess, tmux_win, pane_id, new_pane_id,
                    )
                    # Unsubscribe from old pane
                    if stream_active and pane_id:
                        try:
                            pool = TmuxControlPool.get_instance()
                            ctrl = await pool.get_connection(tmux_sess)
                            await ctrl.unsubscribe(pane_id, on_pane_output)
                        except Exception:
                            pass
                    # Subscribe to new pane
                    pane_id = new_pane_id
                    try:
                        pool = TmuxControlPool.get_instance()
                        ctrl = await pool.get_connection(tmux_sess)
                        await ctrl.subscribe(pane_id, on_pane_output)
                        stream_active = True
                        logger.info(
                            "Re-subscribed %%output for new pane %s", pane_id,
                        )
                    except Exception:
                        logger.warning("Failed to re-subscribe to new pane %s", pane_id)
                    # Force a sync to show current content
                    sync_requested = True
            except Exception:
                pass

            # Immediate sync requested by snapshot recovery
            if sync_requested:
                sync_requested = False
                try:
                    await _send_sync()
                except Exception:
                    pass
                continue

            # Skip sync if stream was successfully flushed recently —
            # the client is getting real-time updates and a sync would
            # only cause a disruptive full-screen redraw.
            # NOTE: we check last_flush_time (when bytes were *sent* to
            # the client), NOT when bytes *arrived* from tmux.  This way,
            # if the buffer is growing without flushing (snapshot recovery
            # pending), sync still fires.
            now = asyncio.get_event_loop().time()
            if last_flush_time > 0 and (now - last_flush_time) < 2.0:
                continue

            try:
                await _send_sync()
            except Exception:
                pass

    # --- Start drift correction (streaming is deferred until after history) ----
    flush_task: asyncio.Task | None = None

    if not pane_id:
        logger.warning(
            "Could not resolve pane ID for %s:%s — drift correction only",
            tmux_sess, tmux_win,
        )

    # Always start drift correction — it provides ground-truth sync even if
    # streaming is active, and is the only update path if pane_id failed.
    drift_task = asyncio.create_task(drift_correction())

    try:
        while True:
            ws_msg = await websocket.receive()

            # Binary frames from client are not expected but handle gracefully
            if ws_msg.get("bytes"):
                continue

            raw = ws_msg.get("text", "")
            if not raw:
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "input":
                record_user_input(session_id)
                asyncio.create_task(send_keys_async(
                    tmux_sess, tmux_win, msg.get("data", "")
                ))
            elif msg.get("type") == "request_sync":
                # Client detected divergence — force immediate sync
                sync_requested = True
            elif msg.get("type") == "request_history":
                scrollback = msg.get("lines", 1000)
                try:
                    result = await capture_pane_with_history_async(
                        tmux_sess, tmux_win, scrollback
                    )
                    content, cursor_x, cursor_y, total_lines = result
                    if content.endswith('\n'):
                        content = content[:-1]
                    await websocket.send_json({
                        "type": "history",
                        "data": content,
                        "cursorX": cursor_x,
                        "cursorY": cursor_y,
                        "totalLines": total_lines,
                    })
                except Exception as e:
                    logger.error("Failed to capture history: %s", e)
            elif msg.get("type") == "resize":
                cols = msg.get("cols", 80)
                rows = msg.get("rows", 24)
                await resize_async(tmux_sess, tmux_win, cols, rows)

                if not initial_sent:
                    await asyncio.sleep(0.05)

                    alternate_on = await check_alternate_screen_async(tmux_sess, tmux_win)

                    result = await capture_pane_with_history_async(
                        tmux_sess, tmux_win, scrollback_lines=1000
                    )
                    content, cursor_x, cursor_y, total_lines = result
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
                    initial_sent = True

                    # Subscribe to %output NOW — after history is sent.
                    # This eliminates the startup gap entirely: the history
                    # capture is the ground truth, and any %output events from
                    # this point forward are incremental updates on top of it.
                    if pane_id and not stream_active:
                        pool = TmuxControlPool.get_instance()
                        conn = await pool.get_connection(tmux_sess)
                        await conn.subscribe(pane_id, on_pane_output)
                        stream_active = True
                        flush_task = asyncio.create_task(stream_flusher())
                        logger.info(
                            "Streaming %%output for pane %s (session %s)",
                            pane_id, tmux_sess,
                        )
    except WebSocketDisconnect:
        pass
    finally:
        # --- Cleanup ----------------------------------------------------------
        if stream_active and pane_id:
            try:
                pool = TmuxControlPool.get_instance()
                ctrl = await pool.get_connection(tmux_sess)
                await ctrl.unsubscribe(pane_id, on_pane_output)
            except Exception:
                pass
        for task in (drift_task, flush_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        clear_user_activity(session_id)
