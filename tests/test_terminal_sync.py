"""Tests for terminal sync improvements.

Tests the WebSocket terminal protocol, flow control, batching, and
conditional sync logic. Uses mocked tmux operations so no live tmux
session is needed.

Tests use ``TERMINAL_STREAM_MODE="control-mode"`` to exercise the
%output fallback path.  Pipe-pane streaming is tested in
``test_pty_stream.py``.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.api.ws_terminal import (
    SNAPSHOT_RECOVERY_THRESHOLD,
    terminal_websocket,
)

# Force control-mode for all tests in this file
_CONTROL_MODE_PATCH = patch("orchestrator.api.ws_terminal.TERMINAL_STREAM_MODE", "control-mode")


@pytest.fixture(autouse=True)
def _force_control_mode():
    """Force control-mode streaming for all tests in this module."""
    with _CONTROL_MODE_PATCH:
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeWebSocket:
    """Minimal WebSocket mock that records sent frames."""

    def __init__(self):
        self.accepted = False
        self.sent_json: list[dict] = []
        self.sent_bytes: list[bytes] = []
        self.closed = False
        self._incoming: asyncio.Queue = asyncio.Queue()
        self.app = MagicMock()

    async def accept(self):
        self.accepted = True

    async def send_json(self, data: dict):
        self.sent_json.append(data)

    async def send_bytes(self, data: bytes):
        self.sent_bytes.append(data)

    async def close(self):
        self.closed = True

    async def receive(self) -> dict:
        msg = await self._incoming.get()
        if msg is None:
            from fastapi import WebSocketDisconnect

            raise WebSocketDisconnect()
        return msg

    def inject_text(self, text: str):
        """Enqueue a text frame for the handler to receive."""
        self._incoming.put_nowait({"text": text})

    def inject_disconnect(self):
        """Enqueue a disconnect signal."""
        self._incoming.put_nowait(None)


def _make_db_row(name="test"):
    """Simulate a sqlite3.Row with dict-like access."""
    row = {"name": name, "id": "sess-1"}
    return row


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBinaryWebSocketProtocol:
    """Verify that stream data is sent as binary WebSocket frames."""

    @pytest.fixture
    def ws(self):
        return FakeWebSocket()

    async def test_stream_bytes_sent_as_binary(self, ws):
        """After initial handshake, %output should arrive as binary frames."""

        captured_callback = {}

        async def fake_subscribe(pane_id, callback):
            captured_callback["cb"] = callback

        async def fake_unsubscribe(pane_id, callback):
            pass

        mock_conn = AsyncMock()
        mock_conn.subscribe = fake_subscribe
        mock_conn.unsubscribe = fake_unsubscribe
        mock_conn.is_alive = True

        mock_pool = MagicMock()
        mock_pool.get_connection = AsyncMock(return_value=mock_conn)

        with (
            patch("orchestrator.api.ws_terminal._get_conn") as mock_get_conn,
            patch("orchestrator.api.ws_terminal.ensure_window", return_value="o:t"),
            patch("orchestrator.api.ws_terminal.get_pane_id_async", return_value="%0"),
            patch(
                "orchestrator.api.ws_terminal.TmuxControlPool.get_instance",
                return_value=mock_pool,
            ),
            patch("orchestrator.api.ws_terminal.resize_async", return_value=True),
            patch("orchestrator.api.ws_terminal.check_alternate_screen_async", return_value=False),
            patch(
                "orchestrator.api.ws_terminal.capture_pane_with_history_async",
                return_value=("hello\n", 0, 0, 1),
            ),
            patch(
                "orchestrator.api.ws_terminal.capture_pane_with_cursor_atomic_async",
                return_value=("hello", 0, 0),
            ),
        ):
            db_conn = MagicMock()
            db_conn.execute.return_value.fetchone.return_value = _make_db_row()
            mock_get_conn.return_value = db_conn
            ws.app.state.conn_factory = None
            ws.app.state.conn = db_conn

            # Run the handler in a task
            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))

            # Wait for handler to be ready
            await asyncio.sleep(0.05)

            # Send resize to trigger initial_sent
            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.15)

            # Simulate a %output event
            assert "cb" in captured_callback, "subscribe callback not captured"
            await captured_callback["cb"](b"\x1b[31mhello\x1b[0m")

            # Wait for the batching flush (~16ms)
            await asyncio.sleep(0.05)

            # Disconnect
            ws.inject_disconnect()
            await asyncio.sleep(0.1)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            # Verify: stream data should be sent as binary, not JSON
            assert len(ws.sent_bytes) >= 1, f"Expected binary frames, got {ws.sent_bytes}"
            assert ws.sent_bytes[0] == b"\x1b[31mhello\x1b[0m"

            # History/sync should be JSON
            json_types = {m["type"] for m in ws.sent_json}
            assert "history" in json_types


class TestStreamBatching:
    """Verify that rapid %output events are batched into fewer frames."""

    async def test_multiple_outputs_batched(self):
        """Several rapid %output events should be combined into one binary frame."""
        ws = FakeWebSocket()
        captured_callback = {}

        async def fake_subscribe(pane_id, callback):
            captured_callback["cb"] = callback

        mock_conn = AsyncMock()
        mock_conn.subscribe = fake_subscribe
        mock_conn.unsubscribe = AsyncMock()
        mock_conn.is_alive = True

        mock_pool = MagicMock()
        mock_pool.get_connection = AsyncMock(return_value=mock_conn)

        with (
            patch("orchestrator.api.ws_terminal._get_conn") as mock_get_conn,
            patch("orchestrator.api.ws_terminal.ensure_window", return_value="o:t"),
            patch("orchestrator.api.ws_terminal.get_pane_id_async", return_value="%0"),
            patch(
                "orchestrator.api.ws_terminal.TmuxControlPool.get_instance",
                return_value=mock_pool,
            ),
            patch("orchestrator.api.ws_terminal.resize_async", return_value=True),
            patch("orchestrator.api.ws_terminal.check_alternate_screen_async", return_value=False),
            patch(
                "orchestrator.api.ws_terminal.capture_pane_with_history_async",
                return_value=("$ \n", 0, 0, 1),
            ),
            patch(
                "orchestrator.api.ws_terminal.capture_pane_with_cursor_atomic_async",
                return_value=("$ ", 2, 0),
            ),
        ):
            db_conn = MagicMock()
            db_conn.execute.return_value.fetchone.return_value = _make_db_row()
            mock_get_conn.return_value = db_conn
            ws.app.state.conn_factory = None
            ws.app.state.conn = db_conn

            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            # Trigger initial_sent
            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.15)

            # Fire 5 rapid %output events within the 16ms batch window
            cb = captured_callback["cb"]
            for i in range(5):
                await cb(f"line{i}\r\n".encode())

            # Wait for flush
            await asyncio.sleep(0.05)

            ws.inject_disconnect()
            await asyncio.sleep(0.05)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            # All 5 outputs should be batched into <=2 binary frames
            # (ideally 1, but timing might split across 2 batch windows)
            assert len(ws.sent_bytes) <= 2, f"Expected batching, got {len(ws.sent_bytes)} frames"
            combined = b"".join(ws.sent_bytes)
            for i in range(5):
                assert f"line{i}\r\n".encode() in combined


class TestConditionalSync:
    """Verify that drift sync is skipped when stream is active."""

    async def test_sync_skipped_when_stream_active(self):
        """Drift correction should skip sync if stream was active < 2s ago."""
        ws = FakeWebSocket()
        captured_callback = {}

        async def fake_subscribe(pane_id, callback):
            captured_callback["cb"] = callback

        mock_conn = AsyncMock()
        mock_conn.subscribe = fake_subscribe
        mock_conn.unsubscribe = AsyncMock()
        mock_conn.is_alive = True

        mock_pool = MagicMock()
        mock_pool.get_connection = AsyncMock(return_value=mock_conn)

        capture_call_count = 0

        async def counting_capture(session, window):
            nonlocal capture_call_count
            capture_call_count += 1
            return ("content", 0, 0)

        with (
            patch("orchestrator.api.ws_terminal._get_conn") as mock_get_conn,
            patch("orchestrator.api.ws_terminal.ensure_window", return_value="o:t"),
            patch("orchestrator.api.ws_terminal.get_pane_id_async", return_value="%0"),
            patch(
                "orchestrator.api.ws_terminal.TmuxControlPool.get_instance",
                return_value=mock_pool,
            ),
            patch("orchestrator.api.ws_terminal.resize_async", return_value=True),
            patch("orchestrator.api.ws_terminal.check_alternate_screen_async", return_value=False),
            patch(
                "orchestrator.api.ws_terminal.capture_pane_with_history_async",
                return_value=("$ \n", 0, 0, 1),
            ),
            patch(
                "orchestrator.api.ws_terminal.capture_pane_with_cursor_atomic_async",
                side_effect=counting_capture,
            ),
        ):
            db_conn = MagicMock()
            db_conn.execute.return_value.fetchone.return_value = _make_db_row()
            mock_get_conn.return_value = db_conn
            ws.app.state.conn_factory = None
            ws.app.state.conn = db_conn

            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))

            # Wait for initial history + early 150ms sync to complete
            await asyncio.sleep(0.5)

            # Record how many sync messages we have so far (early sync)
            initial_syncs = len([m for m in ws.sent_json if m.get("type") == "sync"])

            # Keep stream active by sending %output every 1s for 6s
            # (drift correction fires at 5s intervals)
            cb = captured_callback["cb"]
            for _ in range(6):
                await cb(b"keepalive\r\n")
                await asyncio.sleep(1.0)

            ws.inject_disconnect()
            await asyncio.sleep(0.1)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            # The early 150ms sync is expected.  The periodic 5s syncs
            # should all have been skipped because the stream was active.
            final_syncs = len([m for m in ws.sent_json if m.get("type") == "sync"])
            periodic_syncs = final_syncs - initial_syncs
            assert periodic_syncs == 0, (
                f"Expected 0 periodic syncs while stream active, "
                f"got {periodic_syncs} (early={initial_syncs}, total={final_syncs})"
            )


class TestSnapshotRecovery:
    """Verify snapshot recovery replaces drop-based flow control."""

    async def test_threshold_constant(self):
        """SNAPSHOT_RECOVERY_THRESHOLD should be ~256KB."""
        assert SNAPSHOT_RECOVERY_THRESHOLD == 256_000

    async def test_large_buffer_triggers_sync(self):
        """When buffer exceeds threshold, it should be cleared and sync requested."""
        ws = FakeWebSocket()
        captured_callback = {}

        async def fake_subscribe(pane_id, callback):
            captured_callback["cb"] = callback

        mock_conn = AsyncMock()
        mock_conn.subscribe = fake_subscribe
        mock_conn.unsubscribe = AsyncMock()
        mock_conn.is_alive = True

        mock_pool = MagicMock()
        mock_pool.get_connection = AsyncMock(return_value=mock_conn)

        sync_count = 0

        async def counting_capture(session, window):
            nonlocal sync_count
            sync_count += 1
            return ("recovered", 0, 0)

        with (
            patch("orchestrator.api.ws_terminal._get_conn") as mock_get_conn,
            patch("orchestrator.api.ws_terminal.ensure_window", return_value="o:t"),
            patch("orchestrator.api.ws_terminal.get_pane_id_async", return_value="%0"),
            patch(
                "orchestrator.api.ws_terminal.TmuxControlPool.get_instance",
                return_value=mock_pool,
            ),
            patch("orchestrator.api.ws_terminal.resize_async", return_value=True),
            patch("orchestrator.api.ws_terminal.check_alternate_screen_async", return_value=False),
            patch(
                "orchestrator.api.ws_terminal.capture_pane_with_history_async",
                return_value=("$ \n", 0, 0, 1),
            ),
            patch(
                "orchestrator.api.ws_terminal.capture_pane_with_cursor_atomic_async",
                side_effect=counting_capture,
            ),
        ):
            db_conn = MagicMock()
            db_conn.execute.return_value.fetchone.return_value = _make_db_row()
            mock_get_conn.return_value = db_conn
            ws.app.state.conn_factory = None
            ws.app.state.conn = db_conn

            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            # Trigger initial_sent
            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.15)

            # Record sync count after initial setup
            initial_syncs = sync_count

            # Flood with data exceeding SNAPSHOT_RECOVERY_THRESHOLD
            cb = captured_callback["cb"]
            chunk = b"x" * 130_000  # Two chunks > 256KB
            await cb(chunk)
            await cb(chunk)

            # Wait for drift correction to pick up the sync_requested flag
            await asyncio.sleep(3)

            ws.inject_disconnect()
            await asyncio.sleep(0.1)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            # A sync should have been triggered by snapshot recovery
            recovery_syncs = sync_count - initial_syncs
            assert recovery_syncs >= 1, (
                f"Expected snapshot recovery sync, got {recovery_syncs} syncs"
            )

    async def test_request_sync_message(self):
        """Client request_sync message should trigger immediate sync."""
        ws = FakeWebSocket()

        async def fake_subscribe(pane_id, callback):
            pass

        mock_conn = AsyncMock()
        mock_conn.subscribe = fake_subscribe
        mock_conn.unsubscribe = AsyncMock()
        mock_conn.is_alive = True

        mock_pool = MagicMock()
        mock_pool.get_connection = AsyncMock(return_value=mock_conn)

        with (
            patch("orchestrator.api.ws_terminal._get_conn") as mock_get_conn,
            patch("orchestrator.api.ws_terminal.ensure_window", return_value="o:t"),
            patch("orchestrator.api.ws_terminal.get_pane_id_async", return_value="%0"),
            patch(
                "orchestrator.api.ws_terminal.TmuxControlPool.get_instance",
                return_value=mock_pool,
            ),
            patch("orchestrator.api.ws_terminal.resize_async", return_value=True),
            patch("orchestrator.api.ws_terminal.check_alternate_screen_async", return_value=False),
            patch(
                "orchestrator.api.ws_terminal.capture_pane_with_history_async",
                return_value=("$ \n", 0, 0, 1),
            ),
            patch(
                "orchestrator.api.ws_terminal.capture_pane_with_cursor_atomic_async",
                return_value=("$ ", 2, 0),
            ),
        ):
            db_conn = MagicMock()
            db_conn.execute.return_value.fetchone.return_value = _make_db_row()
            mock_get_conn.return_value = db_conn
            ws.app.state.conn_factory = None
            ws.app.state.conn = db_conn

            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            # Trigger initial_sent
            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.15)

            # Send request_sync — should not crash, should set sync_requested
            ws.inject_text(json.dumps({"type": "request_sync"}))
            await asyncio.sleep(0.05)

            ws.inject_disconnect()
            await asyncio.sleep(0.05)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            # No error should have been sent
            error_msgs = [m for m in ws.sent_json if m.get("type") == "error"]
            assert len(error_msgs) == 0


class TestDeferredSubscription:
    """Verify that %output subscription happens after initial history."""

    async def test_subscribe_after_history(self):
        """subscribe should be called only after initial_sent = True."""
        ws = FakeWebSocket()
        subscribe_times = []

        async def tracking_subscribe(pane_id, callback):
            subscribe_times.append(asyncio.get_event_loop().time())

        mock_conn = AsyncMock()
        mock_conn.subscribe = tracking_subscribe
        mock_conn.unsubscribe = AsyncMock()
        mock_conn.is_alive = True

        mock_pool = MagicMock()
        mock_pool.get_connection = AsyncMock(return_value=mock_conn)

        history_send_times = []
        original_send_json = ws.send_json

        async def tracking_send_json(data):
            if data.get("type") == "history":
                history_send_times.append(asyncio.get_event_loop().time())
            await original_send_json(data)

        ws.send_json = tracking_send_json

        with (
            patch("orchestrator.api.ws_terminal._get_conn") as mock_get_conn,
            patch("orchestrator.api.ws_terminal.ensure_window", return_value="o:t"),
            patch("orchestrator.api.ws_terminal.get_pane_id_async", return_value="%0"),
            patch(
                "orchestrator.api.ws_terminal.TmuxControlPool.get_instance",
                return_value=mock_pool,
            ),
            patch("orchestrator.api.ws_terminal.resize_async", return_value=True),
            patch("orchestrator.api.ws_terminal.check_alternate_screen_async", return_value=False),
            patch(
                "orchestrator.api.ws_terminal.capture_pane_with_history_async",
                return_value=("$ \n", 0, 0, 1),
            ),
            patch(
                "orchestrator.api.ws_terminal.capture_pane_with_cursor_atomic_async",
                return_value=("$ ", 2, 0),
            ),
        ):
            db_conn = MagicMock()
            db_conn.execute.return_value.fetchone.return_value = _make_db_row()
            mock_get_conn.return_value = db_conn
            ws.app.state.conn_factory = None
            ws.app.state.conn = db_conn

            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            # Trigger initial handshake
            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.2)

            ws.inject_disconnect()
            await asyncio.sleep(0.05)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            # Verify subscribe happened AFTER history was sent
            assert len(history_send_times) >= 1, "History not sent"
            assert len(subscribe_times) >= 1, "Subscribe not called"
            assert subscribe_times[0] >= history_send_times[0], (
                "Subscribe should happen after history send"
            )
