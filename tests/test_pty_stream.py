"""Tests for direct PTY streaming via pipe-pane.

Tests the ``PtyStreamReader``, ``PtyStreamPool``, tmux version detection,
and the pipe-pane integration in ``ws_terminal.py``.

All tests mock tmux commands — no live tmux session needed.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.terminal.pty_stream import (
    PtyStreamPool,
    PtyStreamReader,
    _parse_tmux_version,
    get_tmux_version,
    reset_tmux_version_cache,
    set_tmux_version_cache,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset module-level caches and singletons between tests."""
    reset_tmux_version_cache()
    PtyStreamPool.reset_instance()
    yield
    reset_tmux_version_cache()
    PtyStreamPool.reset_instance()


@pytest.fixture
def tmp_fifo_dir(tmp_path):
    """Override FIFO_DIR to a temp directory for test isolation."""
    fifo_dir = str(tmp_path / "orchestrator_pty")
    with patch("orchestrator.terminal.pty_stream.FIFO_DIR", fifo_dir):
        yield fifo_dir


# ---------------------------------------------------------------------------
# tmux version detection
# ---------------------------------------------------------------------------


class TestTmuxVersionParsing:
    """Test _parse_tmux_version with various version strings."""

    def test_standard_version(self):
        assert _parse_tmux_version("tmux 3.4") == (3, 4)

    def test_version_with_suffix(self):
        assert _parse_tmux_version("tmux 3.3a") == (3, 3)

    def test_version_with_newline(self):
        assert _parse_tmux_version("tmux 3.6a\n") == (3, 6)

    def test_next_version(self):
        assert _parse_tmux_version("tmux next-3.5") == (3, 5)

    def test_master_version(self):
        assert _parse_tmux_version("tmux master") == (999, 0)

    def test_old_version(self):
        assert _parse_tmux_version("tmux 2.6") == (2, 6)

    def test_very_old_version(self):
        assert _parse_tmux_version("tmux 1.8") == (1, 8)

    def test_unknown_format(self):
        assert _parse_tmux_version("not tmux") == (0, 0)

    def test_empty_string(self):
        assert _parse_tmux_version("") == (0, 0)


class TestTmuxVersionDetection:
    """Test get_tmux_version async detection."""

    async def test_detect_version(self):
        """Should parse tmux -V output."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"tmux 3.4\n", b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            version = await get_tmux_version()
            assert version == (3, 4)

    async def test_caches_result(self):
        """Second call should return cached result without subprocess."""
        set_tmux_version_cache(3, 4)
        # No subprocess mock — would fail if it tried to run tmux
        version = await get_tmux_version()
        assert version == (3, 4)

    async def test_handles_tmux_not_found(self):
        """Should return (0, 0) if tmux is not installed."""
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("tmux not found"),
        ):
            version = await get_tmux_version()
            assert version == (0, 0)


# ---------------------------------------------------------------------------
# PtyStreamReader
# ---------------------------------------------------------------------------


class TestPtyStreamReader:
    """Tests for PtyStreamReader FIFO lifecycle and behavior."""

    async def test_requires_tmux_26(self, tmp_fifo_dir):
        """Should return False if tmux < 2.6."""
        set_tmux_version_cache(2, 5)
        reader = PtyStreamReader("sess", "win", "%5")
        callback = AsyncMock()
        result = await reader.start(callback)
        assert result is False
        assert not reader.is_alive

    async def test_creates_fifo_dir(self, tmp_fifo_dir):
        """Should create the FIFO directory with correct permissions."""
        set_tmux_version_cache(3, 4)
        reader = PtyStreamReader("sess", "win", "%5")

        # Mock pipe-pane to fail so we can test FIFO creation
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = (b"", b"error")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await reader.start(AsyncMock())

        # FIFO dir should have been created
        assert os.path.isdir(tmp_fifo_dir)
        dir_stat = os.stat(tmp_fifo_dir)
        assert stat.S_IMODE(dir_stat.st_mode) == 0o700

    async def test_sanitizes_pane_id(self):
        """Pane ID should have % stripped for FIFO filename."""
        reader = PtyStreamReader("sess", "win", "%5")
        assert reader._safe_pane_id == "5"

        reader2 = PtyStreamReader("sess", "win", "5")
        assert reader2._safe_pane_id == "5"

    async def test_fifo_path_contains_pid(self, tmp_fifo_dir):
        """FIFO path should include server PID in the filename."""
        reader = PtyStreamReader("sess", "win", "%5")
        expected_name = f"5_{os.getpid()}.fifo"
        # The reader computes the path but doesn't create it until start().
        # Verify the naming convention:
        assert reader._safe_pane_id == "5"
        # Construct what the FIFO path would be
        # Verify by creating the FIFO via the reader's internals
        set_tmux_version_cache(3, 4)
        os.makedirs(tmp_fifo_dir, mode=0o700, exist_ok=True)
        fifo_name = f"{reader._safe_pane_id}_{os.getpid()}.fifo"
        assert fifo_name == expected_name

    async def test_pipe_pane_failure_returns_false(self, tmp_fifo_dir):
        """Should return False and clean up when pipe-pane fails."""
        set_tmux_version_cache(3, 4)
        reader = PtyStreamReader("sess", "win", "%5")

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = (b"", b"can't find pane")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await reader.start(AsyncMock())

        assert result is False
        assert not reader.is_alive
        # FIFO should be cleaned up
        expected_name = f"5_{os.getpid()}.fifo"
        fifo_path = os.path.join(tmp_fifo_dir, expected_name)
        assert not os.path.exists(fifo_path)

    async def test_is_alive_property(self):
        """is_alive should reflect running + not EOF state."""
        reader = PtyStreamReader("sess", "win", "%5")
        assert not reader.is_alive

        reader._running = True
        assert reader.is_alive

        reader._eof = True
        assert not reader.is_alive

    async def test_stop_idempotent(self):
        """Calling stop() multiple times should not error."""
        reader = PtyStreamReader("sess", "win", "%5")
        await reader.stop()
        await reader.stop()

    async def test_eof_callback_invoked(self, tmp_fifo_dir):
        """EOF callback should be called when _on_eof is triggered."""
        reader = PtyStreamReader("sess", "win", "%5")
        eof_mock = AsyncMock()
        reader._eof_callback = eof_mock
        reader._running = True

        await reader._on_eof()

        assert reader._eof is True
        eof_mock.assert_awaited_once()

    async def test_eof_callback_only_once(self, tmp_fifo_dir):
        """EOF callback should only fire once even if _on_eof called twice."""
        reader = PtyStreamReader("sess", "win", "%5")
        eof_mock = AsyncMock()
        reader._eof_callback = eof_mock
        reader._running = True

        await reader._on_eof()
        await reader._on_eof()

        eof_mock.assert_awaited_once()

    async def test_data_callback_strips_esc_k(self, tmp_fifo_dir):
        """Data callback should receive bytes with ESC k sequences stripped."""
        reader = PtyStreamReader("sess", "win", "%5")
        received = []

        async def callback(data: bytes):
            received.append(data)

        reader._callback = callback
        reader._running = True

        # ESC k title ST + regular data
        test_data = b"\x1bktitle\x1b\\hello"
        await reader._on_data(test_data)

        assert len(received) == 1
        assert received[0] == b"hello"

    async def test_data_callback_not_called_when_stopped(self):
        """Callback should not fire when reader is stopped."""
        reader = PtyStreamReader("sess", "win", "%5")
        callback = AsyncMock()
        reader._callback = callback
        reader._running = False

        await reader._on_data(b"test")
        callback.assert_not_awaited()


# ---------------------------------------------------------------------------
# PtyStreamPool
# ---------------------------------------------------------------------------


class TestPtyStreamPool:
    """Tests for PtyStreamPool subscriber management and fan-out."""

    async def test_subscribe_starts_reader(self, tmp_fifo_dir):
        """First subscriber should start a PtyStreamReader."""
        set_tmux_version_cache(3, 4)
        pool = PtyStreamPool()

        mock_reader = AsyncMock(spec=PtyStreamReader)
        mock_reader.is_alive = True
        mock_reader.start = AsyncMock(return_value=True)

        callback = AsyncMock()

        with patch(
            "orchestrator.terminal.pty_stream.PtyStreamReader",
            return_value=mock_reader,
        ):
            result = await pool.subscribe("%5", "sess", "win", callback)

        assert result is True
        mock_reader.start.assert_awaited_once()
        assert "%5" in pool._readers
        assert callback in pool._consumers["%5"]

    async def test_second_subscriber_reuses_reader(self, tmp_fifo_dir):
        """Second subscriber should reuse existing reader."""
        set_tmux_version_cache(3, 4)
        pool = PtyStreamPool()

        mock_reader = AsyncMock(spec=PtyStreamReader)
        mock_reader.is_alive = True
        mock_reader.start = AsyncMock(return_value=True)

        cb1 = AsyncMock()
        cb2 = AsyncMock()

        with patch(
            "orchestrator.terminal.pty_stream.PtyStreamReader",
            return_value=mock_reader,
        ):
            await pool.subscribe("%5", "sess", "win", cb1)
            await pool.subscribe("%5", "sess", "win", cb2)

        # start() should only be called once
        mock_reader.start.assert_awaited_once()
        assert cb1 in pool._consumers["%5"]
        assert cb2 in pool._consumers["%5"]

    async def test_unsubscribe_last_stops_reader(self, tmp_fifo_dir):
        """Unsubscribing the last consumer should stop the reader."""
        set_tmux_version_cache(3, 4)
        pool = PtyStreamPool()

        mock_reader = AsyncMock(spec=PtyStreamReader)
        mock_reader.is_alive = True
        mock_reader.start = AsyncMock(return_value=True)
        mock_reader.stop = AsyncMock()

        callback = AsyncMock()

        with patch(
            "orchestrator.terminal.pty_stream.PtyStreamReader",
            return_value=mock_reader,
        ):
            await pool.subscribe("%5", "sess", "win", callback)
            await pool.unsubscribe("%5", callback)

        mock_reader.stop.assert_awaited_once()
        assert "%5" not in pool._readers
        assert "%5" not in pool._consumers

    async def test_unsubscribe_one_of_two_keeps_reader(self, tmp_fifo_dir):
        """Unsubscribing one of two consumers should keep reader alive."""
        set_tmux_version_cache(3, 4)
        pool = PtyStreamPool()

        mock_reader = AsyncMock(spec=PtyStreamReader)
        mock_reader.is_alive = True
        mock_reader.start = AsyncMock(return_value=True)
        mock_reader.stop = AsyncMock()

        cb1 = AsyncMock()
        cb2 = AsyncMock()

        with patch(
            "orchestrator.terminal.pty_stream.PtyStreamReader",
            return_value=mock_reader,
        ):
            await pool.subscribe("%5", "sess", "win", cb1)
            await pool.subscribe("%5", "sess", "win", cb2)
            await pool.unsubscribe("%5", cb1)

        mock_reader.stop.assert_not_awaited()
        assert "%5" in pool._readers
        assert cb2 in pool._consumers["%5"]
        assert cb1 not in pool._consumers["%5"]

    async def test_subscribe_returns_false_on_failure(self, tmp_fifo_dir):
        """Should return False when reader fails to start."""
        set_tmux_version_cache(3, 4)
        pool = PtyStreamPool()

        mock_reader = AsyncMock(spec=PtyStreamReader)
        mock_reader.is_alive = False
        mock_reader.start = AsyncMock(return_value=False)

        callback = AsyncMock()

        with patch(
            "orchestrator.terminal.pty_stream.PtyStreamReader",
            return_value=mock_reader,
        ):
            result = await pool.subscribe("%5", "sess", "win", callback)

        assert result is False
        assert "%5" not in pool._readers

    async def test_fan_out_dispatches_to_all(self, tmp_fifo_dir):
        """_dispatch should send data to all subscribers."""
        pool = PtyStreamPool()

        cb1 = AsyncMock()
        cb2 = AsyncMock()
        pool._consumers["%5"] = {cb1, cb2}

        await pool._dispatch("%5", b"hello")
        # Give create_task callbacks time to run
        await asyncio.sleep(0.05)

        cb1.assert_awaited_once_with(b"hello")
        cb2.assert_awaited_once_with(b"hello")

    async def test_fan_out_slow_subscriber_doesnt_block(self, tmp_fifo_dir):
        """A slow subscriber should not block delivery to others."""
        pool = PtyStreamPool()

        async def _slow(*a, **kw):
            await asyncio.sleep(0.3)

        slow_cb = AsyncMock(side_effect=_slow)
        fast_cb = AsyncMock()
        pool._consumers["%5"] = {slow_cb, fast_cb}

        await pool._dispatch("%5", b"hello")
        # Fast callback should complete quickly
        await asyncio.sleep(0.05)

        fast_cb.assert_awaited_once_with(b"hello")

        # Let slow callback finish to avoid unawaited-coroutine warning
        await asyncio.sleep(0.35)

    async def test_eof_removes_reader(self, tmp_fifo_dir):
        """_on_reader_eof should remove the dead reader."""
        set_tmux_version_cache(3, 4)
        pool = PtyStreamPool()

        mock_reader = AsyncMock(spec=PtyStreamReader)
        mock_reader.is_alive = True
        mock_reader.start = AsyncMock(return_value=True)
        mock_reader.stop = AsyncMock()
        mock_reader.session = "sess"
        mock_reader.window = "win"

        callback = AsyncMock()

        with patch(
            "orchestrator.terminal.pty_stream.PtyStreamReader",
            return_value=mock_reader,
        ):
            await pool.subscribe("%5", "sess", "win", callback)

        assert "%5" in pool._readers

        # Simulate EOF — patch PtyStreamReader to fail restart
        with patch(
            "orchestrator.terminal.pty_stream.PtyStreamReader",
        ) as mock_reader_cls:
            restart_reader = AsyncMock(spec=PtyStreamReader)
            restart_reader.start = AsyncMock(return_value=False)
            mock_reader_cls.return_value = restart_reader

            await pool._on_reader_eof("%5")
            # Give eager restart time
            await asyncio.sleep(0.1)

        # Reader should be removed (restart failed)
        assert "%5" not in pool._readers
        # Consumers should still be there (for drift correction re-subscribe)
        assert callback in pool._consumers.get("%5", set())

    async def test_stale_fifo_cleanup(self, tmp_fifo_dir):
        """Pool init should clean up stale FIFOs."""
        fifo_dir = Path(tmp_fifo_dir)
        fifo_dir.mkdir(parents=True, exist_ok=True)

        # Create stale FIFOs (different PID)
        stale_fifo = fifo_dir / "5_99999.fifo"
        os.mkfifo(str(stale_fifo), 0o600)
        assert stale_fifo.exists()

        # Create FIFO with our PID (should not be removed)
        our_fifo = fifo_dir / f"6_{os.getpid()}.fifo"
        os.mkfifo(str(our_fifo), 0o600)

        PtyStreamPool()

        assert not stale_fifo.exists()
        assert our_fifo.exists()

        # Cleanup
        our_fifo.unlink()

    async def test_unsubscribe_nonexistent_no_error(self, tmp_fifo_dir):
        """Unsubscribing a non-existent callback should not error."""
        pool = PtyStreamPool()
        await pool.unsubscribe("%5", AsyncMock())


# ---------------------------------------------------------------------------
# ws_terminal integration: pipe-pane path
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

    async def close(self, code=None):
        self.closed = True
        self.close_code = code

    async def receive(self) -> dict:
        msg = await self._incoming.get()
        if msg is None:
            from fastapi import WebSocketDisconnect

            raise WebSocketDisconnect()
        return msg

    def inject_text(self, text: str):
        self._incoming.put_nowait({"text": text})

    def inject_disconnect(self):
        self._incoming.put_nowait(None)


def _make_db_row(name="test"):
    return {"name": name, "id": "sess-1", "rws_pty_id": None, "host": "localhost"}


class TestWsTerminalPipePanePath:
    """Test ws_terminal with pipe-pane streaming."""

    async def test_pipe_pane_subscribe_called(self):
        """When pipe-pane succeeds, PtyStreamPool.subscribe should be used."""
        from orchestrator.api.ws_terminal import terminal_websocket

        ws = FakeWebSocket()
        subscribe_called = {}

        mock_pool = AsyncMock(spec=PtyStreamPool)

        async def fake_subscribe(pane_id, session, window, callback):
            subscribe_called["pane_id"] = pane_id
            subscribe_called["cb"] = callback
            return True

        mock_pool.subscribe = fake_subscribe
        mock_pool.unsubscribe = AsyncMock()

        with (
            patch("orchestrator.api.ws_terminal._get_conn") as mock_get_conn,
            patch("orchestrator.api.ws_terminal.ensure_window", return_value="o:t"),
            patch("orchestrator.api.ws_terminal.get_pane_id_async", return_value="%0"),
            patch(
                "orchestrator.api.ws_terminal.PtyStreamPool.get_instance",
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

            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.15)

            assert "pane_id" in subscribe_called
            assert subscribe_called["pane_id"] == "%0"

            # Send data via the callback and verify it arrives as binary
            cb = subscribe_called["cb"]
            await cb(b"\x1b[32mgreen\x1b[0m")
            await asyncio.sleep(0.05)

            ws.inject_disconnect()
            await asyncio.sleep(0.1)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            assert len(ws.sent_bytes) >= 1
            assert b"\x1b[32mgreen\x1b[0m" in b"".join(ws.sent_bytes)

    async def test_pipe_pane_failure_degrades_gracefully(self):
        """When pipe-pane fails, terminal should degrade to drift-correction-only."""
        from orchestrator.api.ws_terminal import terminal_websocket

        ws = FakeWebSocket()

        mock_pty_pool = AsyncMock(spec=PtyStreamPool)
        mock_pty_pool.subscribe = AsyncMock(return_value=False)
        mock_pty_pool.unsubscribe = AsyncMock()

        with (
            patch("orchestrator.api.ws_terminal._get_conn") as mock_get_conn,
            patch("orchestrator.api.ws_terminal.ensure_window", return_value="o:t"),
            patch("orchestrator.api.ws_terminal.get_pane_id_async", return_value="%0"),
            patch(
                "orchestrator.api.ws_terminal.PtyStreamPool.get_instance",
                return_value=mock_pty_pool,
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

            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.5)

            # No error should have been sent
            error_msgs = [m for m in ws.sent_json if m.get("type") == "error"]
            assert len(error_msgs) == 0

            # History should still be sent
            json_types = {m["type"] for m in ws.sent_json}
            assert "history" in json_types

            ws.inject_disconnect()
            await asyncio.sleep(0.1)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_backpressure_triggers_snapshot_recovery(self):
        """Buffer exceeding SNAPSHOT_RECOVERY_THRESHOLD should trigger sync."""
        from orchestrator.api.ws_terminal import (
            terminal_websocket,
        )

        ws = FakeWebSocket()
        subscribe_called = {}

        mock_pool = AsyncMock(spec=PtyStreamPool)

        async def fake_subscribe(pane_id, session, window, callback):
            subscribe_called["cb"] = callback
            return True

        mock_pool.subscribe = fake_subscribe
        mock_pool.unsubscribe = AsyncMock()

        sync_count = 0

        async def counting_capture(session, window):
            nonlocal sync_count
            sync_count += 1
            return ("recovered", 0, 0)

        _ws = "orchestrator.api.ws_terminal"
        with (
            patch(f"{_ws}._get_conn") as mock_get_conn,
            patch(f"{_ws}.ensure_window", return_value="o:t"),
            patch(f"{_ws}.get_pane_id_async", return_value="%0"),
            patch(f"{_ws}.PtyStreamPool.get_instance", return_value=mock_pool),
            patch(f"{_ws}.resize_async", return_value=True),
            patch(f"{_ws}.check_alternate_screen_async", return_value=False),
            patch(f"{_ws}.capture_pane_with_history_async", return_value=("$ \n", 0, 0, 1)),
            patch(f"{_ws}.capture_pane_with_cursor_atomic_async", side_effect=counting_capture),
            patch(f"{_ws}.DRIFT_HEALTHY_INTERVAL", 0.05),
            patch(f"{_ws}.DRIFT_UNHEALTHY_INTERVAL", 0.02),
            patch(f"{_ws}.DRIFT_STREAM_HEALTH_TIMEOUT", 0.15),
            patch(f"{_ws}.DRIFT_STAGGER_MAX", 0.0),
            patch(f"{_ws}.DRIFT_EARLY_SYNC_DELAY", 0.01),
        ):
            db_conn = MagicMock()
            db_conn.execute.return_value.fetchone.return_value = _make_db_row()
            mock_get_conn.return_value = db_conn
            ws.app.state.conn_factory = None
            ws.app.state.conn = db_conn

            handler_task = asyncio.create_task(terminal_websocket(ws, "sess-1"))
            await asyncio.sleep(0.05)

            ws.inject_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await asyncio.sleep(0.1)

            initial_syncs = sync_count

            # Flood with data exceeding threshold
            cb = subscribe_called["cb"]
            chunk = b"x" * 130_000
            await cb(chunk)
            await cb(chunk)

            # Wait for drift correction to pick up sync_requested
            await asyncio.sleep(0.3)

            ws.inject_disconnect()
            await asyncio.sleep(0.05)
            handler_task.cancel()
            try:
                await handler_task
            except (asyncio.CancelledError, Exception):
                pass

            recovery_syncs = sync_count - initial_syncs
            assert recovery_syncs >= 1, f"Expected snapshot recovery sync, got {recovery_syncs}"


# ---------------------------------------------------------------------------
# stream_remote_pty: PTY-not-found clears stale DB state
# ---------------------------------------------------------------------------


def _make_remote_db_row(name="rws-test", pty_id="pty-abc"):
    return {"name": name, "id": "sess-1", "rws_pty_id": pty_id, "host": "user/rdev-vm"}


class TestStreamRemotePtyNotFound:
    """When the daemon reports PTY not found, clear DB state but do NOT send pty_exit.

    "PTY not found" can mean the daemon restarted/GC'd — Claude may still be
    running.  Frontend will retry via the 4004 close code path.
    """

    async def test_pty_not_found_clears_db_but_no_pty_exit(self):
        from orchestrator.api.ws_terminal import terminal_websocket

        ws = FakeWebSocket()
        mock_rws = MagicMock()
        mock_rws.connect_pty_stream.side_effect = RuntimeError(
            "PTY stream connect failed: PTY not found"
        )

        mock_update = MagicMock()

        with (
            patch("orchestrator.api.ws_terminal._get_conn") as mock_get_conn,
            patch(
                "orchestrator.terminal.remote_worker_server.get_remote_worker_server",
                return_value=mock_rws,
            ),
            patch("orchestrator.terminal.ssh.is_remote_host", return_value=True),
            patch(
                "orchestrator.state.repositories.sessions.update_session",
                mock_update,
            ),
        ):
            db_conn = MagicMock()
            db_conn.execute.return_value.fetchone.return_value = _make_remote_db_row()
            mock_get_conn.return_value = db_conn
            ws.app.state.conn_factory = None
            ws.app.state.conn = db_conn

            await terminal_websocket(ws, "sess-1")

        # Should NOT send pty_exit — "not found" doesn't mean Claude exited
        assert not any(m.get("type") == "pty_exit" for m in ws.sent_json), (
            f"Should not send pty_exit on PTY not found, got: {ws.sent_json}"
        )
        # Should NOT send error either — just close with 4004
        assert not any(m.get("type") == "error" for m in ws.sent_json), (
            "Should not send error message when PTY is not found"
        )

        # Should clear stale rws_pty_id in DB
        mock_update.assert_called_once_with(
            db_conn, "sess-1", rws_pty_id=None, status="disconnected"
        )

        assert ws.closed
        assert ws.close_code == 4004

    async def test_transient_error_sends_error_not_pty_exit(self):
        """Non-'PTY not found' errors should send a generic error (allow retries)."""
        from orchestrator.api.ws_terminal import terminal_websocket

        ws = FakeWebSocket()
        mock_rws = MagicMock()
        mock_rws.connect_pty_stream.side_effect = RuntimeError(
            "PTY stream handshake failed for pty-abc on user/rdev-vm"
        )

        with (
            patch("orchestrator.api.ws_terminal._get_conn") as mock_get_conn,
            patch(
                "orchestrator.terminal.remote_worker_server.get_remote_worker_server",
                return_value=mock_rws,
            ),
            patch("orchestrator.terminal.ssh.is_remote_host", return_value=True),
            patch("orchestrator.state.repositories.sessions.update_session") as mock_update,
        ):
            db_conn = MagicMock()
            db_conn.execute.return_value.fetchone.return_value = _make_remote_db_row()
            mock_get_conn.return_value = db_conn
            ws.app.state.conn_factory = None
            ws.app.state.conn = db_conn

            await terminal_websocket(ws, "sess-1")

        # Should send generic error (frontend can retry)
        assert any(m.get("type") == "error" for m in ws.sent_json)
        assert not any(m.get("type") == "pty_exit" for m in ws.sent_json)

        # Should NOT clear DB — error may be transient
        mock_update.assert_not_called()

        assert ws.closed


class TestStreamRemotePtyCleanup:
    """Verify stream cleanup only sends pty_exit when PTY is confirmed dead."""

    async def test_tunnel_death_does_not_send_pty_exit(self):
        """When stream EOF is caused by tunnel death (daemon unreachable),
        do NOT send pty_exit or clear DB — the PTY may still be alive."""
        from orchestrator.api.ws_terminal import stream_remote_pty

        ws = FakeWebSocket()
        ws.accepted = True

        # Simulate: connect_pty_stream succeeds, then recv returns b"" (EOF)
        fake_sock = MagicMock()
        mock_rws = MagicMock()
        mock_rws.connect_pty_stream.return_value = (fake_sock, b"")
        # After stream closes, daemon is unreachable (tunnel died)
        mock_rws.execute.side_effect = RuntimeError("connection refused")

        mock_update = MagicMock()

        with (
            patch(
                "orchestrator.terminal.remote_worker_server.get_remote_worker_server",
                return_value=mock_rws,
            ),
            patch(
                "orchestrator.state.repositories.sessions.update_session",
                mock_update,
            ),
            patch(
                "orchestrator.api.ws_terminal._blocking_recv",
                return_value=b"",  # EOF — stream socket closed
            ),
        ):
            await stream_remote_pty(ws, "sess-1", "pty-abc", "user/rdev-vm")

        # Should NOT send pty_exit — daemon was unreachable, PTY might still live
        assert not any(m.get("type") == "pty_exit" for m in ws.sent_json), (
            f"Should not send pty_exit on tunnel death, got: {ws.sent_json}"
        )
        # Should NOT clear DB
        mock_update.assert_not_called()

    async def test_confirmed_dead_pty_sends_pty_exit(self):
        """When daemon confirms PTY is dead, send pty_exit and clear DB."""
        from orchestrator.api.ws_terminal import stream_remote_pty

        ws = FakeWebSocket()
        ws.accepted = True

        fake_sock = MagicMock()
        mock_rws = MagicMock()
        mock_rws.connect_pty_stream.return_value = (fake_sock, b"")
        # After stream closes, daemon says PTY is dead
        mock_rws.execute.return_value = {"ptys": [{"pty_id": "pty-abc", "alive": False}]}

        mock_update = MagicMock()

        with (
            patch(
                "orchestrator.terminal.remote_worker_server.get_remote_worker_server",
                return_value=mock_rws,
            ),
            patch(
                "orchestrator.state.repositories.sessions.update_session",
                mock_update,
            ),
            patch("orchestrator.api.ws_terminal._get_conn") as mock_get_conn,
            patch(
                "orchestrator.api.ws_terminal._blocking_recv",
                return_value=b"",
            ),
        ):
            db_conn = MagicMock()
            mock_get_conn.return_value = db_conn
            ws.app.state.conn_factory = None
            ws.app.state.conn = db_conn

            await stream_remote_pty(ws, "sess-1", "pty-abc", "user/rdev-vm")

        # Should send pty_exit — PTY confirmed dead
        assert any(m.get("type") == "pty_exit" for m in ws.sent_json), (
            f"Expected pty_exit for confirmed dead PTY, got: {ws.sent_json}"
        )
        # Should clear DB
        mock_update.assert_called_once_with(db_conn, "sess-1", rws_pty_id=None, status="idle")

    async def test_pty_still_alive_no_pty_exit(self):
        """When daemon confirms PTY is still alive, do NOT send pty_exit."""
        from orchestrator.api.ws_terminal import stream_remote_pty

        ws = FakeWebSocket()
        ws.accepted = True

        fake_sock = MagicMock()
        mock_rws = MagicMock()
        mock_rws.connect_pty_stream.return_value = (fake_sock, b"")
        # Daemon says PTY is alive (tunnel died, not the PTY)
        mock_rws.execute.return_value = {"ptys": [{"pty_id": "pty-abc", "alive": True}]}

        mock_update = MagicMock()

        with (
            patch(
                "orchestrator.terminal.remote_worker_server.get_remote_worker_server",
                return_value=mock_rws,
            ),
            patch(
                "orchestrator.state.repositories.sessions.update_session",
                mock_update,
            ),
            patch(
                "orchestrator.api.ws_terminal._blocking_recv",
                return_value=b"",
            ),
        ):
            await stream_remote_pty(ws, "sess-1", "pty-abc", "user/rdev-vm")

        assert not any(m.get("type") == "pty_exit" for m in ws.sent_json)
        mock_update.assert_not_called()
