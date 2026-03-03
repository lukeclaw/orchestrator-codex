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

Streaming modes
---------------
* **pipe-pane** (default): Raw PTY bytes via ``tmux pipe-pane -O`` — no octal
  encoding, no line-level fragmentation.  Eliminates TUI frame tearing.
* **control-mode** (fallback): ``%output`` notifications via tmux control mode.
  Used when pipe-pane is unavailable (tmux < 2.6, pipe-pane startup failure).

Set ``TERMINAL_STREAM_MODE=control-mode`` env var to force the legacy path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
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
from orchestrator.terminal.manager import ensure_window, tmux_target, window_exists
from orchestrator.terminal.pty_stream import (
    TERMINAL_STREAM_MODE,
    PtyStreamPool,
    suppress_control_mode_output,
)

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


def is_any_session_active(timeout: float = 5.0) -> bool:
    """Check if ANY session has had recent user input.

    Used by drift correction to avoid tmux subprocess contention
    (capture-pane, list-panes) while the user is typing in any terminal.
    """
    now = time.time()
    return any((now - t) < timeout for t in _session_last_input.values())


def clear_user_activity(session_id: str) -> None:
    """Clear activity tracking for a session (on disconnect/delete)."""
    _session_last_input.pop(session_id, None)


def _get_conn(websocket: WebSocket):
    """Get a database connection, preferring factory for thread safety."""
    factory = getattr(websocket.app.state, "conn_factory", None)
    if factory:
        return factory.create()
    return websocket.app.state.conn


async def stream_pane(
    websocket: WebSocket,
    tmux_sess: str,
    tmux_win: str,
    session_id: str | None = None,
) -> None:
    """Core terminal streaming loop — reusable for any tmux pane.

    Handles: PTY subscription, stream batching, input relay, resize,
    sync/history, drift correction.

    Args:
        websocket: Accepted WebSocket connection.
        tmux_sess: tmux session name.
        tmux_win: tmux window name.
        session_id: Optional session ID for user activity tracking.
    """
    # Wait for the client to send a resize before capturing initial content.
    # This ensures the tmux pane matches xterm's dimensions.
    initial_sent = False

    # --- Resolve pane ID for streaming ----------------------------------------
    pane_id = await get_pane_id_async(tmux_sess, tmux_win)
    stream_active = False
    # Which streaming mode is active for this connection
    using_pipe_pane = False
    drift_task: asyncio.Task | None = None

    # --- Stream batching & flow control state ---------------------------------
    stream_buffer = bytearray()  # accumulates stream bytes
    flush_event = asyncio.Event()  # signals that stream_buffer has data
    last_flush_time: float = 0.0  # monotonic time of last successful flush
    sync_requested = False  # set True to trigger an immediate sync
    sync_in_progress = False  # prevents flusher from sending during sync
    last_sync_hash: int | None = None  # CRC32 of last sync content — skip if unchanged

    # --- Callback for stream data (used by both pipe-pane and %output) --------
    # NEVER drops bytes.  If the buffer grows too large (client can't keep
    # up), we discard the stale buffer and request a full sync instead.
    async def on_stream_data(raw_bytes: bytes) -> None:
        nonlocal sync_requested
        stream_buffer.extend(raw_bytes)
        if len(stream_buffer) > SNAPSHOT_RECOVERY_THRESHOLD:
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
        """Batch stream bytes and send as binary WebSocket frames.

        Adaptive batching: short delay for small buffers (typing echo),
        longer for burst output (command output scrolling).
        """
        nonlocal last_flush_time
        while True:
            await flush_event.wait()  # zero-cost when idle
            # Adaptive batch window: 1ms for typing echo, 8ms for bursts.
            await asyncio.sleep(0.001)
            if len(stream_buffer) > 512:
                # Large burst — coalesce a bit more to avoid WS frame flood
                await asyncio.sleep(0.007)  # total ~8ms
            flush_event.clear()
            if stream_buffer and not sync_in_progress:
                data = bytes(stream_buffer)
                stream_buffer.clear()
                try:
                    await websocket.send_bytes(data)
                    last_flush_time = asyncio.get_running_loop().time()
                except Exception:
                    break  # WebSocket closed

    # --- Helpers for sync with divergence hash ---------------------------------
    async def _send_sync():
        """Capture pane and send a sync message with CRC32 hash."""
        nonlocal sync_in_progress, last_sync_hash

        sync_in_progress = True
        try:
            stream_buffer.clear()
            flush_event.clear()

            content, cx, cy = await capture_pane_with_cursor_atomic_async(tmux_sess, tmux_win)

            stream_buffer.clear()
            flush_event.clear()

            if content.endswith("\n"):
                content = content[:-1]
            content_hash = zlib.crc32(content.encode("utf-8")) & 0xFFFFFFFF

            # Skip sending if content hasn't changed — avoids expensive
            # client-side full-screen rewrite that blocks the browser
            # main thread and delays keyboard event processing.
            if content_hash == last_sync_hash and not sync_requested:
                return
            last_sync_hash = content_hash

            await websocket.send_json(
                {
                    "type": "sync",
                    "data": content,
                    "cursorX": cx,
                    "cursorY": cy,
                    "hash": content_hash,
                }
            )
        finally:
            sync_in_progress = False

    # --- Streaming: subscribe (pipe-pane with fallback to %output) ------------
    async def _start_streaming() -> None:
        """Start output streaming for the resolved pane.

        Tries pipe-pane first (if enabled), falls back to %output on failure.
        """
        nonlocal stream_active, using_pipe_pane

        if not pane_id or stream_active:
            return

        # Try pipe-pane mode first
        if TERMINAL_STREAM_MODE == "pipe-pane":
            pty_pool = PtyStreamPool.get_instance()
            success = await pty_pool.subscribe(pane_id, tmux_sess, tmux_win, on_stream_data)
            if success:
                stream_active = True
                using_pipe_pane = True
                # Suppress unused %output processing (tmux >= 3.2)
                await suppress_control_mode_output(tmux_sess)
                logger.info(
                    "Streaming via pipe-pane for pane %s (session %s)",
                    pane_id,
                    tmux_sess,
                )
                return
            else:
                logger.warning(
                    "pipe-pane failed for pane %s, falling back to %%output",
                    pane_id,
                )

        # Fallback: %output control mode
        pool = TmuxControlPool.get_instance()
        conn = await pool.get_connection(tmux_sess)
        await conn.subscribe(pane_id, on_stream_data)
        stream_active = True
        using_pipe_pane = False
        logger.info(
            "Streaming via %%output for pane %s (session %s)",
            pane_id,
            tmux_sess,
        )

    async def _stop_streaming() -> None:
        """Stop output streaming and clean up."""
        nonlocal stream_active

        if not stream_active or not pane_id:
            return

        if using_pipe_pane:
            try:
                pty_pool = PtyStreamPool.get_instance()
                await pty_pool.unsubscribe(pane_id, on_stream_data)
            except Exception:
                pass
        else:
            try:
                pool = TmuxControlPool.get_instance()
                ctrl = await pool.get_connection(tmux_sess)
                await ctrl.unsubscribe(pane_id, on_stream_data)
            except Exception:
                pass

        stream_active = False

    # --- Drift correction (background sync) ------------------------------------
    async def drift_correction():
        nonlocal sync_requested, pane_id

        # Early sync: correct any desync from the brief gap between
        # history capture and streaming start.
        await asyncio.sleep(0.15)
        if initial_sent:
            try:
                await _send_sync()
            except Exception:
                pass

        # Stagger drift correction across connections to avoid all terminals
        # spawning tmux subprocesses simultaneously.
        import random

        stagger = random.uniform(0, 2.0)
        await asyncio.sleep(stagger)

        while True:
            # Longer interval when streaming is healthy (5s); standard 2s
            # when stream is unhealthy and we need ground-truth syncs.
            now = asyncio.get_running_loop().time()
            stream_healthy = (
                using_pipe_pane
                and stream_active
                and last_flush_time > 0
                and (now - last_flush_time) < 5.0
            )
            interval = 5.0 if stream_healthy else 2.0
            await asyncio.sleep(interval)

            if not initial_sent:
                continue

            # Skip subprocess-heavy work while user is actively typing
            # in ANY terminal — avoids cross-session tmux contention.
            if is_any_session_active():
                continue

            # Re-check stream health after sleep (may have changed)
            now = asyncio.get_running_loop().time()
            stream_healthy = (
                using_pipe_pane
                and stream_active
                and last_flush_time > 0
                and (now - last_flush_time) < 5.0
            )

            if stream_healthy and not sync_requested:
                continue

            loop_t0 = time.monotonic()

            # Immediate sync requested by snapshot recovery
            if sync_requested:
                sync_requested = False
                try:
                    await _send_sync()
                except Exception:
                    pass
                continue

            # --- Detect pane ID change (window destroyed & recreated) ---
            # Only check when stream is NOT healthy (EOF, no recent data, etc.)
            try:
                new_pane_id = await get_pane_id_async(tmux_sess, tmux_win)
                if new_pane_id and new_pane_id != pane_id:
                    logger.info(
                        "Pane ID changed for %s:%s: %s -> %s, re-subscribing",
                        tmux_sess,
                        tmux_win,
                        pane_id,
                        new_pane_id,
                    )
                    await _stop_streaming()
                    pane_id = new_pane_id
                    await _start_streaming()
                    sync_requested = True
                    continue
            except Exception:
                pass

            # No recent stream data — do a full sync via capture-pane
            try:
                await _send_sync()
            except Exception:
                pass

            loop_ms = (time.monotonic() - loop_t0) * 1000
            if loop_ms > 100:
                logger.warning(
                    "drift[%s:%s] full loop took %.0fms",
                    tmux_sess,
                    tmux_win,
                    loop_ms,
                )

    # --- Start drift correction (streaming is deferred until after history) ----
    flush_task: asyncio.Task | None = None

    if not pane_id:
        logger.warning(
            "Could not resolve pane ID for %s:%s — drift correction only",
            tmux_sess,
            tmux_win,
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
                if session_id:
                    record_user_input(session_id)

                input_data = msg.get("data", "")
                cr_idx = input_data.find("\r")
                needs_enter_split = cr_idx > 0 and "\x1b[200~" not in input_data

                async def _send_input_split(data: str):
                    """Handle Enter-split case in a task (needs sleep)."""
                    before = data[: data.find("\r")]
                    after = data[data.find("\r") :]
                    ok = await send_keys_async(tmux_sess, tmux_win, before)
                    if ok:
                        await asyncio.sleep(0.015)
                        ok = await send_keys_async(tmux_sess, tmux_win, after)
                    if not ok:
                        try:
                            await websocket.send_json(
                                {"type": "error", "message": "Failed to send input to terminal"}
                            )
                        except Exception:
                            pass

                if needs_enter_split:
                    # Enter after text: must split with delay → use background task
                    asyncio.create_task(_send_input_split(input_data))
                else:
                    # Simple keystroke: send inline to avoid task scheduling delay
                    ok = await send_keys_async(tmux_sess, tmux_win, input_data)
                    if not ok:
                        logger.error(
                            "send_keys failed for %s:%s (data_len=%d)",
                            tmux_sess,
                            tmux_win,
                            len(input_data),
                        )
                        try:
                            await websocket.send_json(
                                {"type": "error", "message": "Failed to send input to terminal"}
                            )
                        except Exception:
                            pass
            elif msg.get("type") == "request_sync":
                sync_requested = True
            elif msg.get("type") == "request_history":
                scrollback = msg.get("lines", 1000)
                try:
                    result = await capture_pane_with_history_async(tmux_sess, tmux_win, scrollback)
                    content, cursor_x, cursor_y, total_lines = result
                    if content.endswith("\n"):
                        content = content[:-1]
                    await websocket.send_json(
                        {
                            "type": "history",
                            "data": content,
                            "cursorX": cursor_x,
                            "cursorY": cursor_y,
                            "totalLines": total_lines,
                        }
                    )
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
                    if content.endswith("\n"):
                        content = content[:-1]
                    await websocket.send_json(
                        {
                            "type": "history",
                            "data": content,
                            "cursorX": cursor_x,
                            "cursorY": cursor_y,
                            "totalLines": total_lines,
                            "alternateScreen": alternate_on,
                        }
                    )
                    initial_sent = True

                    # Start streaming NOW — after history is sent.
                    await _start_streaming()
                    if stream_active:
                        flush_task = asyncio.create_task(stream_flusher())

    except WebSocketDisconnect:
        pass
    finally:
        # --- Cleanup ----------------------------------------------------------
        await _stop_streaming()
        for task in (drift_task, flush_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if session_id:
            clear_user_activity(session_id)


async def terminal_websocket(websocket: WebSocket, session_id: str):
    """Stream terminal output and relay input for a session."""
    await websocket.accept()

    # Look up the session name from DB to derive the tmux target
    db_conn = _get_conn(websocket)
    owns_conn = getattr(websocket.app.state, "conn_factory", None) is not None
    try:
        row = db_conn.execute("SELECT name FROM sessions WHERE id = ?", (session_id,)).fetchone()

        if not row:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": f"Session {session_id} not found",
                }
            )
            await websocket.close()
            return

        tmux_sess, tmux_win = tmux_target(row["name"])

        # Auto-create tmux session and window if they don't exist
        try:
            worker_tmp_dir = os.path.join("/tmp/orchestrator/workers", row["name"])
            target = ensure_window(tmux_sess, tmux_win, cwd=worker_tmp_dir)
            logger.info("Terminal ready: %s", target)
        except Exception as e:
            logger.exception("Failed to create tmux session/window")
            await websocket.send_json(
                {
                    "type": "error",
                    "message": f"Failed to create terminal: {e}",
                }
            )
            await websocket.close()
            return
    finally:
        if owns_conn:
            db_conn.close()

    await stream_pane(websocket, tmux_sess, tmux_win, session_id=session_id)


async def stream_remote_pty(
    websocket: WebSocket,
    session_id: str,
    pty_id: str,
    rws_host: str,
) -> None:
    """Proxy between a WebSocket client and a remote PTY via RWS daemon.

    Opens a dedicated PTY stream connection to the RWS daemon, then:
      - Reads raw PTY output bytes and sends as binary WebSocket frames
      - Relays input/resize from WebSocket to the PTY stream as JSON-lines

    The PTY stays alive on the daemon even after the WebSocket disconnects,
    enabling reattach with history replay.
    """

    from orchestrator.terminal.remote_worker_server import get_remote_worker_server

    try:
        rws = get_remote_worker_server(rws_host)
    except RuntimeError as e:
        await websocket.send_json({"type": "error", "message": f"RWS not available: {e}"})
        await websocket.close(code=4004)
        return

    try:
        stream_sock, initial_data = rws.connect_pty_stream(pty_id)
    except RuntimeError as e:
        await websocket.send_json({"type": "error", "message": f"PTY stream failed: {e}"})
        await websocket.close(code=4004)
        return

    # Send any initial data (ringbuffer history replay)
    if initial_data:
        try:
            await websocket.send_bytes(initial_data)
        except Exception:
            stream_sock.close()
            return

    # --- Background task: read from PTY stream → send to WebSocket --------
    stream_buffer = bytearray()
    flush_event = asyncio.Event()
    stream_closed = asyncio.Event()
    pty_exited = False  # True when the PTY process exits (EOF on stream)

    async def read_pty_output():
        """Read raw bytes from PTY stream socket and batch-send to WebSocket."""
        nonlocal pty_exited
        loop = asyncio.get_running_loop()
        while not stream_closed.is_set():
            try:
                data = await loop.run_in_executor(None, _blocking_recv, stream_sock)
            except Exception:
                stream_closed.set()
                break
            if data is None:
                continue  # Timeout — no data yet, keep reading
            if not data:
                pty_exited = True
                stream_closed.set()
                break  # b"" means EOF — remote closed connection
            stream_buffer.extend(data)
            flush_event.set()

    async def stream_flusher():
        """Batch stream bytes and send as binary WebSocket frames."""
        while not stream_closed.is_set():
            await flush_event.wait()
            # Adaptive batch: 1ms for small buffers, 8ms for bursts
            await asyncio.sleep(0.001)
            if len(stream_buffer) > 512:
                await asyncio.sleep(0.007)
            flush_event.clear()
            if stream_buffer:
                data = bytes(stream_buffer)
                stream_buffer.clear()
                try:
                    await websocket.send_bytes(data)
                except Exception:
                    stream_closed.set()
                    break

    read_task = asyncio.create_task(read_pty_output())
    flush_task = asyncio.create_task(stream_flusher())

    try:
        while not stream_closed.is_set():
            try:
                ws_msg = await asyncio.wait_for(websocket.receive(), timeout=1.0)
            except TimeoutError:
                continue
            except WebSocketDisconnect:
                break

            raw = ws_msg.get("text", "")
            if not raw:
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "input":
                if session_id:
                    record_user_input(session_id)
                cmd = json.dumps({"type": "input", "data": msg.get("data", "")}).encode() + b"\n"
                try:
                    stream_sock.setblocking(True)
                    stream_sock.settimeout(5.0)
                    stream_sock.sendall(cmd)
                    stream_sock.setblocking(False)
                except OSError:
                    break

            elif msg.get("type") == "resize":
                cols = msg.get("cols", 80)
                rows = msg.get("rows", 24)
                cmd = json.dumps({"type": "resize", "cols": cols, "rows": rows}).encode() + b"\n"
                try:
                    stream_sock.setblocking(True)
                    stream_sock.settimeout(5.0)
                    stream_sock.sendall(cmd)
                    stream_sock.setblocking(False)
                except OSError:
                    break

    except WebSocketDisconnect:
        pass
    finally:
        stream_closed.set()
        for task in (read_task, flush_task):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            stream_sock.close()
        except OSError:
            pass
        # Notify the client that the PTY process exited (not just a WS drop)
        if pty_exited:
            try:
                await websocket.send_json({"type": "pty_exit"})
            except Exception:
                pass
        if session_id:
            clear_user_activity(session_id)


def _blocking_recv(sock, bufsize: int = 65536, timeout: float = 1.0) -> bytes | None:
    """Blocking recv with timeout for use in run_in_executor.

    Returns:
        bytes: Data received from the socket.
        None: Timeout — no data available but connection is still alive.
        b"": Connection closed by remote end (EOF).
    """

    sock.setblocking(True)
    sock.settimeout(timeout)
    try:
        return sock.recv(bufsize)
    except TimeoutError:
        return None  # Timeout — connection still alive, just no data
    except OSError:
        return b""  # Connection closed


async def ws_interactive_cli(websocket: WebSocket, session_id: str):
    """Stream the interactive CLI terminal for a session."""
    from orchestrator.terminal.interactive import _active_clis, get_active_cli, recover_cli

    await websocket.accept()

    cli = get_active_cli(session_id)
    if not cli:
        # Try inline recovery from surviving tmux window or remote PTY
        db_conn = _get_conn(websocket)
        try:
            cli = await asyncio.get_running_loop().run_in_executor(
                None, recover_cli, session_id, db_conn
            )
        finally:
            if getattr(websocket.app.state, "conn_factory", None) is not None:
                db_conn.close()
    if not cli:
        await websocket.send_json(
            {
                "type": "error",
                "message": "No active interactive CLI",
            }
        )
        await websocket.close(code=4004)
        return

    # Route to RWS PTY streaming if this is a remote PTY-backed CLI
    if cli.remote_pty_id and cli.rws_host:
        await stream_remote_pty(websocket, session_id, cli.remote_pty_id, cli.rws_host)
        _active_clis.pop(session_id, None)
    else:
        tmux_sess = "orchestrator"

        # Watch for tmux window death (user typed 'exit').
        # stream_pane doesn't detect window destruction — its main loop
        # blocks on websocket.receive() and drift_correction fails silently.
        # This watcher runs alongside and sends pty_exit + closes the
        # websocket when the window is gone, causing stream_pane to exit.
        window_gone = asyncio.Event()

        async def _watch_window():
            loop = asyncio.get_running_loop()
            while not window_gone.is_set():
                await asyncio.sleep(2)
                exists = await loop.run_in_executor(None, window_exists, tmux_sess, cli.window_name)
                if not exists:
                    window_gone.set()
                    try:
                        await websocket.send_json({"type": "pty_exit"})
                    except Exception:
                        pass
                    try:
                        await websocket.close()
                    except Exception:
                        pass

        watcher = asyncio.create_task(_watch_window())
        try:
            await stream_pane(websocket, tmux_sess, cli.window_name)
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
            # Only remove from registry if the tmux window is actually dead.
            # If the user just navigated away (WS disconnect), the window is
            # still alive and should remain registered so it isn't orphaned.
            if window_gone.is_set() or not window_exists(tmux_sess, cli.window_name):
                _active_clis.pop(session_id, None)
            window_gone.set()
