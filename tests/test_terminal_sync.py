"""Tests for terminal sync improvements.

Tests the WebSocket terminal protocol, flow control, batching, and
conditional sync logic. Uses mocked tmux operations so no live tmux
session is needed.

All tests use pipe-pane streaming via ``PtyStreamPool`` mocks.
Drift correction timing constants are patched to tiny values so tests
run fast (<1s each instead of 5-14s).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.api.ws_terminal import (
    SNAPSHOT_RECOVERY_THRESHOLD,
    terminal_websocket,
)
from orchestrator.terminal.pty_stream import PtyStreamPool

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
    row = {"name": name, "id": "sess-1", "rws_pty_id": None, "host": "localhost"}
    return row


def _setup_ws_test(
    ws,
    captured_callback=None,
    capture_side_effect=None,
    capture_return=None,
    subscribe_return=True,
    pane_id_side_effect=None,
    pool_override=None,
):
    """Set up common patches and return an ExitStack context manager.

    Returns (stack, mock_pool) where stack is an ExitStack that must be
    used as a context manager.

    Drift correction timing is patched to tiny values so tests don't
    have to sleep for seconds waiting for real intervals to elapse.
    """
    if pool_override:
        mock_pool = pool_override
    else:
        mock_pool = AsyncMock(spec=PtyStreamPool)
        # get_reader returns None by default so drift correction uses the
        # subscribe() path (not the refresh_pipe_pane stale-reader path).
        mock_pool.get_reader = MagicMock(return_value=None)
        if captured_callback is not None:

            async def fake_subscribe(pane_id, session, window, callback):
                captured_callback["cb"] = callback
                return subscribe_return

            mock_pool.subscribe = fake_subscribe
        else:
            mock_pool.subscribe = AsyncMock(return_value=subscribe_return)
        mock_pool.unsubscribe = AsyncMock()

    capture_kwargs = {}
    if capture_side_effect:
        capture_kwargs["side_effect"] = capture_side_effect
    elif capture_return:
        capture_kwargs["return_value"] = capture_return
    else:
        capture_kwargs["return_value"] = ("$ ", 2, 0)

    pane_id_kwargs = {}
    if pane_id_side_effect:
        pane_id_kwargs["side_effect"] = pane_id_side_effect
    else:
        pane_id_kwargs["return_value"] = "%0"

    stack = ExitStack()
    # Patch drift correction timing to tiny values for fast tests.
    _ws = "orchestrator.api.ws_terminal"
    stack.enter_context(patch(f"{_ws}.DRIFT_HEALTHY_INTERVAL", 0.05))
    stack.enter_context(patch(f"{_ws}.DRIFT_UNHEALTHY_INTERVAL", 0.02))
    stack.enter_context(patch(f"{_ws}.DRIFT_STREAM_HEALTH_TIMEOUT", 0.15))
    stack.enter_context(patch(f"{_ws}.DRIFT_STAGGER_MAX", 0.0))
    stack.enter_context(patch(f"{_ws}.DRIFT_EARLY_SYNC_DELAY", 0.01))
    stack.enter_context(patch(f"{_ws}.PIPE_PANE_REFRESH_THRESHOLD", 0.3))
    stack.enter_context(patch(f"{_ws}.DRIFT_IDLE_CONFIRMED_INTERVAL", 0.05))
    mock_get_conn = stack.enter_context(patch(f"{_ws}._get_conn"))
    stack.enter_context(patch(f"{_ws}.ensure_window", return_value="o:t"))
    stack.enter_context(patch(f"{_ws}.get_pane_id_async", **pane_id_kwargs))
    stack.enter_context(patch(f"{_ws}.PtyStreamPool.get_instance", return_value=mock_pool))
    stack.enter_context(patch(f"{_ws}.resize_async", return_value=True))
    stack.enter_context(patch(f"{_ws}.check_alternate_screen_async", return_value=False))
    stack.enter_context(
        patch(
            f"{_ws}.capture_pane_with_history_async",
            return_value=("$ \n", 0, 0, 1),
        )
    )
    stack.enter_context(
        patch(
            f"{_ws}.capture_pane_with_cursor_atomic_async",
            **capture_kwargs,
        )
    )

    db_conn = MagicMock()
    db_conn.execute.return_value.fetchone.return_value = _make_db_row()
    mock_get_conn.return_value = db_conn
    ws.app.state.conn_factory = None
    ws.app.state.conn = db_conn

    return stack, mock_pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBinaryWebSocketProtocol:
    """Verify that stream data is sent as binary WebSocket frames."""

    @pytest.fixture
    def ws(self):
        return FakeWebSocket()

    async def test_stream_bytes_sent_as_binary(self, ws):
        """After initial handshake, pipe-pane data should arrive as binary."""

        captured_callback = {}
        stack, _ = _setup_ws_test(
            ws,
            captured_callback=captured_callback,
            capture_return=("hello", 0, 0),
        )

        with stack:
            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.1)

            assert "cb" in captured_callback, "subscribe callback not captured"
            await captured_callback["cb"](b"\x1b[31mhello\x1b[0m")
            await asyncio.sleep(0.05)

            ws.inject_disconnect()
            await asyncio.sleep(0.05)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            assert len(ws.sent_bytes) >= 1, f"Expected binary frames, got {ws.sent_bytes}"
            assert ws.sent_bytes[0] == b"\x1b[31mhello\x1b[0m"

            json_types = {m["type"] for m in ws.sent_json}
            assert "history" in json_types


class TestStreamBatching:
    """Verify that rapid stream events are batched into fewer frames."""

    async def test_multiple_outputs_batched(self):
        """Several rapid outputs should be combined into one binary frame."""
        ws = FakeWebSocket()
        captured_callback = {}
        stack, _ = _setup_ws_test(ws, captured_callback=captured_callback)

        with stack:
            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.1)

            cb = captured_callback["cb"]
            for i in range(5):
                await cb(f"line{i}\r\n".encode())

            await asyncio.sleep(0.05)

            ws.inject_disconnect()
            await asyncio.sleep(0.05)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            assert len(ws.sent_bytes) <= 2, f"Expected batching, got {len(ws.sent_bytes)} frames"
            combined = b"".join(ws.sent_bytes)
            for i in range(5):
                assert f"line{i}\r\n".encode() in combined


class TestConditionalSync:
    """Verify that drift sync is skipped when stream is active."""

    async def test_sync_skipped_when_stream_active(self):
        """Drift correction should skip sync if stream was active."""
        ws = FakeWebSocket()
        captured_callback = {}
        capture_call_count = 0

        async def counting_capture(session, window):
            nonlocal capture_call_count
            capture_call_count += 1
            return ("content", 0, 0)

        stack, _ = _setup_ws_test(
            ws,
            captured_callback=captured_callback,
            capture_side_effect=counting_capture,
        )

        with stack:
            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.1)

            initial_syncs = len([m for m in ws.sent_json if m.get("type") == "sync"])

            # Keep stream active — send data faster than health timeout (0.15s)
            cb = captured_callback["cb"]
            for _ in range(6):
                await cb(b"keepalive\r\n")
                await asyncio.sleep(0.05)

            ws.inject_disconnect()
            await asyncio.sleep(0.05)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            final_syncs = len([m for m in ws.sent_json if m.get("type") == "sync"])
            periodic_syncs = final_syncs - initial_syncs
            assert periodic_syncs == 0, (
                f"Expected 0 periodic syncs while stream active, "
                f"got {periodic_syncs} (early={initial_syncs}, "
                f"total={final_syncs})"
            )


class TestSnapshotRecovery:
    """Verify snapshot recovery replaces drop-based flow control."""

    async def test_threshold_constant(self):
        """SNAPSHOT_RECOVERY_THRESHOLD should be ~256KB."""
        assert SNAPSHOT_RECOVERY_THRESHOLD == 256_000

    async def test_large_buffer_triggers_sync(self):
        """Buffer exceeding threshold should trigger sync."""
        ws = FakeWebSocket()
        captured_callback = {}
        sync_count = 0

        async def counting_capture(session, window):
            nonlocal sync_count
            sync_count += 1
            return ("recovered", 0, 0)

        stack, _ = _setup_ws_test(
            ws,
            captured_callback=captured_callback,
            capture_side_effect=counting_capture,
        )

        with stack:
            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.1)

            initial_syncs = sync_count

            cb = captured_callback["cb"]
            chunk = b"x" * 130_000
            await cb(chunk)
            await cb(chunk)

            # Wait for drift correction to pick up the sync_requested flag
            await asyncio.sleep(0.3)

            ws.inject_disconnect()
            await asyncio.sleep(0.05)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            recovery_syncs = sync_count - initial_syncs
            assert recovery_syncs >= 1, (
                f"Expected snapshot recovery sync, got {recovery_syncs} syncs"
            )

    async def test_request_sync_message(self):
        """Client request_sync message should not crash."""
        ws = FakeWebSocket()
        stack, _ = _setup_ws_test(ws)

        with stack:
            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.1)

            ws.inject_text(json.dumps({"type": "request_sync"}))
            await asyncio.sleep(0.05)

            ws.inject_disconnect()
            await asyncio.sleep(0.05)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            error_msgs = [m for m in ws.sent_json if m.get("type") == "error"]
            assert len(error_msgs) == 0


class TestDeferredSubscription:
    """Verify that pipe-pane subscription happens after initial history."""

    async def test_subscribe_after_history(self):
        """subscribe should be called only after initial_sent = True."""
        ws = FakeWebSocket()
        subscribe_times = []

        mock_pool = AsyncMock(spec=PtyStreamPool)

        async def tracking_subscribe(pane_id, session, window, callback):
            subscribe_times.append(asyncio.get_event_loop().time())
            return True

        mock_pool.subscribe = tracking_subscribe
        mock_pool.unsubscribe = AsyncMock()

        history_send_times = []
        original_send_json = ws.send_json

        async def tracking_send_json(data):
            if data.get("type") == "history":
                history_send_times.append(asyncio.get_event_loop().time())
            await original_send_json(data)

        ws.send_json = tracking_send_json

        stack, _ = _setup_ws_test(ws, pool_override=mock_pool)

        with stack:
            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.15)

            ws.inject_disconnect()
            await asyncio.sleep(0.05)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            assert len(history_send_times) >= 1, "History not sent"
            assert len(subscribe_times) >= 1, "Subscribe not called"
            assert subscribe_times[0] >= history_send_times[0], (
                "Subscribe should happen after history send"
            )


class TestPipePaneFailureDegradation:
    """Verify graceful degradation when pipe-pane fails."""

    async def test_pipe_pane_failure_degrades_to_drift_only(self):
        """When pipe-pane fails, terminal works via drift correction."""
        ws = FakeWebSocket()
        sync_count = 0

        async def counting_capture(session, window):
            nonlocal sync_count
            sync_count += 1
            return ("$ ", 2, 0)

        stack, _ = _setup_ws_test(
            ws,
            subscribe_return=False,
            capture_side_effect=counting_capture,
        )

        with stack:
            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))

            # Wait for early sync + a couple drift cycles
            await asyncio.sleep(0.3)

            ws.inject_disconnect()
            await asyncio.sleep(0.05)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            assert sync_count >= 1, "Expected at least one sync via drift correction"

            error_msgs = [m for m in ws.sent_json if m.get("type") == "error"]
            assert len(error_msgs) == 0


class TestSyncBugFixes:
    """Regression tests for the three sync stall bugs."""

    async def test_snapshot_recovery_force_bypasses_hash(self):
        """Bug B fix: snapshot recovery with force=True bypasses hash."""
        ws = FakeWebSocket()
        captured_callback = {}
        sync_sent_count = 0

        async def counting_capture(session, window):
            nonlocal sync_sent_count
            sync_sent_count += 1
            return ("$ ", 2, 0)

        stack, _ = _setup_ws_test(
            ws,
            captured_callback=captured_callback,
            capture_side_effect=counting_capture,
        )

        with stack:
            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.1)

            syncs_before = len([m for m in ws.sent_json if m.get("type") == "sync"])

            # Trigger snapshot recovery — same content, but force=True
            # should bypass the hash check
            cb = captured_callback["cb"]
            chunk = b"x" * 130_000
            await cb(chunk)
            await cb(chunk)

            # Wait for drift correction to process sync_requested
            await asyncio.sleep(0.3)

            ws.inject_disconnect()
            await asyncio.sleep(0.05)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            syncs_after = len([m for m in ws.sent_json if m.get("type") == "sync"])
            assert syncs_after > syncs_before, (
                f"Expected forced sync after snapshot recovery, "
                f"but syncs went from {syncs_before} to {syncs_after}"
            )

    async def test_pane_resubscribe_resets_hash(self):
        """Bug C fix: pane re-subscribe resets last_sync_hash."""
        ws = FakeWebSocket()
        pane_ids = ["%0"]

        async def dynamic_pane_id(session, window):
            return pane_ids[0]

        sync_msgs = []
        original_send_json = ws.send_json

        async def tracking_send_json(data):
            if data.get("type") == "sync":
                sync_msgs.append(data)
            await original_send_json(data)

        ws.send_json = tracking_send_json

        stack, _ = _setup_ws_test(ws, pane_id_side_effect=dynamic_pane_id)

        with stack:
            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.1)

            syncs_before_change = len(sync_msgs)

            # Simulate pane ID change
            pane_ids[0] = "%1"

            # Wait for drift correction to detect the pane change
            await asyncio.sleep(0.3)

            ws.inject_disconnect()
            await asyncio.sleep(0.05)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            syncs_after_change = len(sync_msgs) - syncs_before_change
            assert syncs_after_change >= 1, (
                f"Expected sync after pane re-subscribe (hash reset), "
                f"got {syncs_after_change} syncs"
            )

    async def test_sync_forced_on_healthy_to_unhealthy_transition(self):
        """Sync forced on healthy->unhealthy transition even with same hash."""
        ws = FakeWebSocket()
        captured_callback = {}
        sync_msgs = []
        original_send_json = ws.send_json

        async def tracking_send_json(data):
            if data.get("type") == "sync":
                sync_msgs.append(data)
            await original_send_json(data)

        ws.send_json = tracking_send_json

        stack, _ = _setup_ws_test(
            ws,
            captured_callback=captured_callback,
            capture_return=("$ ", 2, 0),
        )

        with stack:
            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.1)

            # Keep stream healthy — send data faster than health timeout (0.15s)
            cb = captured_callback["cb"]
            for _ in range(3):
                await cb(b"active\r\n")
                await asyncio.sleep(0.05)

            syncs_before_stop = len(sync_msgs)

            # Stop sending data — stream becomes unhealthy after 0.15s
            # Wait for health timeout + a couple drift cycles
            await asyncio.sleep(0.5)

            ws.inject_disconnect()
            await asyncio.sleep(0.05)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            syncs_after_stop = len(sync_msgs) - syncs_before_stop
            assert syncs_after_stop >= 1, (
                f"Expected forced sync on healthy->unhealthy transition, "
                f"got {syncs_after_stop} syncs"
            )


class TestDriftNotBlockedByActivityWhenUnhealthy:
    """Verify drift correction runs despite user activity when stream is down."""

    async def test_drift_runs_during_activity_when_stream_unhealthy(self):
        """When stream is unhealthy, drift correction must NOT be blocked
        by is_any_session_active() — it's the only update path."""
        ws = FakeWebSocket()
        sync_count = 0

        async def counting_capture(session, window):
            nonlocal sync_count
            sync_count += 1
            return ("$ ", 2, 0)

        # subscribe returns False → pipe-pane fails → stream_active=False
        stack, _ = _setup_ws_test(
            ws,
            subscribe_return=False,
            capture_side_effect=counting_capture,
        )

        _ws = "orchestrator.api.ws_terminal"
        # Simulate user actively typing in some terminal
        stack.enter_context(patch(f"{_ws}.is_any_session_active", return_value=True))

        with stack:
            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.1)

            syncs_before = sync_count

            # Wait for several drift correction cycles (0.02s interval)
            await asyncio.sleep(0.3)

            ws.inject_disconnect()
            await asyncio.sleep(0.05)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            syncs_during_activity = sync_count - syncs_before
            assert syncs_during_activity >= 1, (
                f"Drift correction should run when stream is unhealthy "
                f"even during user activity, got {syncs_during_activity} syncs"
            )

    async def test_drift_skipped_during_activity_when_stream_healthy(self):
        """When stream is healthy, drift correction should still be skipped
        during user activity (to avoid tmux contention)."""
        ws = FakeWebSocket()
        captured_callback = {}
        sync_count = 0

        async def counting_capture(session, window):
            nonlocal sync_count
            sync_count += 1
            return ("content", 0, 0)

        stack, _ = _setup_ws_test(
            ws,
            captured_callback=captured_callback,
            capture_side_effect=counting_capture,
        )

        _ws = "orchestrator.api.ws_terminal"
        # Simulate user actively typing
        stack.enter_context(patch(f"{_ws}.is_any_session_active", return_value=True))

        with stack:
            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.1)

            initial_syncs = len([m for m in ws.sent_json if m.get("type") == "sync"])

            # Keep stream healthy — send data faster than health timeout
            cb = captured_callback["cb"]
            for _ in range(6):
                await cb(b"keepalive\r\n")
                await asyncio.sleep(0.05)

            ws.inject_disconnect()
            await asyncio.sleep(0.05)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            final_syncs = len([m for m in ws.sent_json if m.get("type") == "sync"])
            periodic_syncs = final_syncs - initial_syncs
            assert periodic_syncs == 0, (
                f"Expected 0 syncs while stream healthy + user active, got {periodic_syncs}"
            )


class TestPipePaneResubscribe:
    """Verify drift correction re-subscribes to pipe-pane when reader dies."""

    async def test_resubscribe_called_when_stream_unhealthy(self):
        """When stream goes unhealthy, drift correction should try to
        re-subscribe to pipe-pane to restart the dead reader."""
        ws = FakeWebSocket()
        captured_callback = {}
        subscribe_calls = []

        mock_pool = AsyncMock(spec=PtyStreamPool)

        async def tracking_subscribe(pane_id, session, window, callback):
            subscribe_calls.append(pane_id)
            captured_callback["cb"] = callback
            return True

        mock_pool.subscribe = tracking_subscribe
        mock_pool.unsubscribe = AsyncMock()
        mock_pool.get_reader = MagicMock(return_value=None)

        stack, _ = _setup_ws_test(
            ws,
            pool_override=mock_pool,
            capture_return=("$ ", 2, 0),
        )

        with stack:
            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.1)

            # Initial subscribe happened during _start_streaming
            initial_subscribes = len(subscribe_calls)
            assert initial_subscribes >= 1, "Initial subscribe not called"

            # Keep stream healthy briefly, then let it go unhealthy
            cb = captured_callback["cb"]
            await cb(b"data\r\n")
            await asyncio.sleep(0.05)

            # Let stream go unhealthy (no data for > health timeout 0.15s)
            # Wait for drift correction to attempt re-subscribe
            await asyncio.sleep(0.5)

            ws.inject_disconnect()
            await asyncio.sleep(0.05)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            resubscribe_calls = len(subscribe_calls) - initial_subscribes
            assert resubscribe_calls >= 1, (
                f"Expected drift correction to re-subscribe when stream "
                f"unhealthy, got {resubscribe_calls} re-subscribe calls"
            )
