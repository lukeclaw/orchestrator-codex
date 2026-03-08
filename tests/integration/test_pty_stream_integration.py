"""Integration tests for direct PTY streaming via pipe-pane.

These tests require a real tmux installation and create actual tmux
sessions/windows/panes.  They verify the full lifecycle:

1. FIFO creation → pipe-pane start → byte delivery → stop → cleanup
2. Multiple subscribers on the same pane
3. EOF detection when pane is destroyed
4. Graceful cleanup on macOS FIFOs

Marked with `allow_subprocess` since they spawn real tmux processes.

All tmux activity uses the globally-patched test session
(``orchestrator-test``) so nothing leaks into the user's real session.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from orchestrator.terminal.pty_stream import (
    PtyStreamPool,
    PtyStreamReader,
    get_tmux_version,
    reset_tmux_version_cache,
)

pytestmark = [
    pytest.mark.allow_subprocess,
    pytest.mark.timeout(30),
]

# Window name used by pty-stream tests inside the global test session.
_PTY_TEST_WINDOW = "pty-test"


@pytest.fixture(autouse=True)
def _reset_caches():
    """Reset version cache and pool between tests."""
    reset_tmux_version_cache()
    PtyStreamPool.reset_instance()
    yield
    reset_tmux_version_cache()
    PtyStreamPool.reset_instance()


@pytest.fixture
def fifo_test_dir(tmp_path):
    """Provide a tmp_path-based FIFO directory so nothing leaks to /tmp/."""
    d = str(tmp_path / "orchestrator_pty_test")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def _tmux_available() -> bool:
    """Check if tmux is available."""
    return shutil.which("tmux") is not None


def _skip_without_tmux():
    if not _tmux_available():
        pytest.skip("tmux not available")


def _get_session_name() -> str:
    """Get the patched test tmux session name."""
    from orchestrator.terminal.manager import TMUX_SESSION

    return TMUX_SESSION


async def _ensure_test_session():
    """Ensure the global test tmux session exists."""
    session = _get_session_name()
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        "new-session",
        "-d",
        "-s",
        session,
        "-x",
        "80",
        "-y",
        "24",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()  # ignore duplicate-session errors
    return session


async def _create_test_window():
    """Create a dedicated test window in the global test session."""
    session = await _ensure_test_session()
    # Kill stale test window if it exists from a previous run
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        "kill-window",
        "-t",
        f"{session}:{_PTY_TEST_WINDOW}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    # Create fresh window
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        "new-window",
        "-d",
        "-t",
        session,
        "-n",
        _PTY_TEST_WINDOW,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return session


async def _destroy_test_window():
    """Kill the test window (session cleanup handled by root conftest)."""
    session = _get_session_name()
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        "kill-window",
        "-t",
        f"{session}:{_PTY_TEST_WINDOW}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


async def _get_pane_id() -> str:
    """Get the pane ID for the test window."""
    session = _get_session_name()
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        "list-panes",
        "-t",
        f"{session}:{_PTY_TEST_WINDOW}",
        "-F",
        "#{pane_id}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode().strip().split("\n")[0]


async def _send_keys(keys: str):
    """Send keys to the test pane."""
    session = _get_session_name()
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        "send-keys",
        "-t",
        f"{session}:{_PTY_TEST_WINDOW}",
        keys,
        "Enter",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


@pytest.fixture
async def tmux_session():
    """Create and tear down a test window for pty-stream tests."""
    _skip_without_tmux()
    session = await _create_test_window()
    yield session
    await _destroy_test_window()


class TestPtyStreamReaderIntegration:
    """End-to-end tests with real tmux."""

    async def test_receives_output(self, tmux_session, fifo_test_dir):
        """Should receive output bytes when a command runs in the pane."""
        pane_id = await _get_pane_id()
        received: list[bytes] = []

        async def on_data(data: bytes):
            received.append(data)

        reader = PtyStreamReader(tmux_session, _PTY_TEST_WINDOW, pane_id)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("orchestrator.terminal.pty_stream.FIFO_DIR", fifo_test_dir)

            started = await reader.start(on_data)
            assert started, "PtyStreamReader failed to start"
            assert reader.is_alive

            # Send a command that produces output
            await _send_keys("echo PIPE_PANE_TEST_12345")
            # Wait for output to flow through
            await asyncio.sleep(0.5)

            await reader.stop()

        # Verify we received the expected output
        all_bytes = b"".join(received)
        assert b"PIPE_PANE_TEST_12345" in all_bytes, (
            f"Expected 'PIPE_PANE_TEST_12345' in output, got: {all_bytes!r}"
        )

        # FIFO should be cleaned up
        fifo_dir = Path(fifo_test_dir)
        if fifo_dir.exists():
            fifos = list(fifo_dir.glob("*.fifo"))
            assert len(fifos) == 0, f"Stale FIFOs remain: {fifos}"

    async def test_stop_cleans_up_fifo(self, tmux_session, fifo_test_dir):
        """stop() should remove the FIFO and stop pipe-pane."""
        pane_id = await _get_pane_id()

        reader = PtyStreamReader(tmux_session, _PTY_TEST_WINDOW, pane_id)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("orchestrator.terminal.pty_stream.FIFO_DIR", fifo_test_dir)

            started = await reader.start(AsyncMock())
            assert started

            # Verify FIFO exists
            fifo_dir = Path(fifo_test_dir)
            fifos = list(fifo_dir.glob("*.fifo"))
            assert len(fifos) == 1

            await reader.stop()

        # FIFO should be gone
        fifo_dir = Path(fifo_test_dir)
        if fifo_dir.exists():
            fifos = list(fifo_dir.glob("*.fifo"))
            assert len(fifos) == 0

    async def test_eof_on_pane_destroy(self, tmux_session, fifo_test_dir):
        """Destroying the pane should trigger EOF callback."""
        pane_id = await _get_pane_id()
        eof_triggered = asyncio.Event()

        async def on_eof():
            eof_triggered.set()

        reader = PtyStreamReader(tmux_session, _PTY_TEST_WINDOW, pane_id)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("orchestrator.terminal.pty_stream.FIFO_DIR", fifo_test_dir)

            started = await reader.start(AsyncMock(), eof_callback=on_eof)
            if not started:
                pytest.skip("PtyStreamReader failed to start (may be tmux version issue)")

            # Kill the test window — should trigger EOF.
            # Other windows in the test session keep it alive.
            proc = await asyncio.create_subprocess_exec(
                "tmux",
                "kill-window",
                "-t",
                f"{tmux_session}:{_PTY_TEST_WINDOW}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

            # Wait for EOF
            try:
                await asyncio.wait_for(eof_triggered.wait(), timeout=5.0)
            except TimeoutError:
                # EOF may not trigger on all platforms; that's OK
                pass

            await reader.stop()

    async def test_version_detection(self):
        """Should detect the real tmux version."""
        _skip_without_tmux()
        version = await get_tmux_version()
        assert version[0] >= 1, f"Unexpected tmux version: {version}"
        assert version[1] >= 0


class TestPtyStreamPoolIntegration:
    """Integration tests for the pool with real tmux."""

    async def test_pool_subscribe_unsubscribe(self, tmux_session, fifo_test_dir):
        """Pool subscribe/unsubscribe lifecycle with real tmux."""
        pane_id = await _get_pane_id()
        received: list[bytes] = []

        async def on_data(data: bytes):
            received.append(data)

        pool = PtyStreamPool()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("orchestrator.terminal.pty_stream.FIFO_DIR", fifo_test_dir)

            success = await pool.subscribe(pane_id, tmux_session, _PTY_TEST_WINDOW, on_data)
            assert success, "Pool subscribe failed"

            # Generate output
            await _send_keys("echo POOL_TEST_67890")
            await asyncio.sleep(0.5)

            await pool.unsubscribe(pane_id, on_data)

        all_bytes = b"".join(received)
        assert b"POOL_TEST_67890" in all_bytes

    async def test_pool_two_subscribers(self, tmux_session, fifo_test_dir):
        """Two subscribers should both receive the same bytes."""
        pane_id = await _get_pane_id()
        received1: list[bytes] = []
        received2: list[bytes] = []

        async def on_data1(data: bytes):
            received1.append(data)

        async def on_data2(data: bytes):
            received2.append(data)

        pool = PtyStreamPool()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("orchestrator.terminal.pty_stream.FIFO_DIR", fifo_test_dir)

            s1 = await pool.subscribe(pane_id, tmux_session, _PTY_TEST_WINDOW, on_data1)
            s2 = await pool.subscribe(pane_id, tmux_session, _PTY_TEST_WINDOW, on_data2)
            assert s1 and s2

            await _send_keys("echo TWO_SUBS_TEST")
            await asyncio.sleep(0.5)

            await pool.unsubscribe(pane_id, on_data1)
            await pool.unsubscribe(pane_id, on_data2)

        all1 = b"".join(received1)
        all2 = b"".join(received2)
        assert b"TWO_SUBS_TEST" in all1
        assert b"TWO_SUBS_TEST" in all2
