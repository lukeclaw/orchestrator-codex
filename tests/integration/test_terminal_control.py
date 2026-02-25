"""Integration tests for tmux control module — atomic capture functions.

These tests use synchronous wrappers to avoid pytest-asyncio event loop
conflicts when running alongside E2E tests with session-scoped fixtures.

Uses worker-isolated session names from conftest.py for parallel execution.
"""

import asyncio
import time

import pytest

from orchestrator.terminal import manager as tmux
from orchestrator.terminal.control import (
    capture_pane_with_cursor_atomic_async,
    capture_pane_with_history_async,
)

pytestmark = pytest.mark.allow_subprocess

TEST_WINDOW = "test-win"


def _run(coro):
    """Run coroutine in a separate thread to avoid event loop conflicts.

    When running alongside E2E tests with session-scoped playwright fixtures,
    there may already be a running event loop. Using a thread ensures isolation.
    """
    import concurrent.futures

    def run_in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_in_thread)
        return future.result(timeout=30)


@pytest.fixture
def tmux_window(tmux_control_session):
    """Create a test tmux session and window using worker-isolated session name."""
    tmux.create_session(tmux_control_session)
    tmux.create_window(tmux_control_session, TEST_WINDOW)
    time.sleep(0.2)  # Let shell initialize
    return (tmux_control_session, TEST_WINDOW)


class TestAtomicCapture:
    """Tests for capture_pane_with_cursor_atomic_async."""

    def test_captures_content_and_cursor(self, tmux_window):
        """Atomic capture should return content and cursor position."""
        session, window = tmux_window

        content, cursor_x, cursor_y = _run(capture_pane_with_cursor_atomic_async(session, window))

        # Should have some content (at least a shell prompt)
        assert isinstance(content, str)
        # Cursor position should be valid integers
        assert isinstance(cursor_x, int)
        assert isinstance(cursor_y, int)
        assert cursor_x >= 0
        assert cursor_y >= 0

    def test_captures_typed_content(self, tmux_window):
        """Atomic capture should include content we type."""
        session, window = tmux_window

        # Type something
        tmux.send_keys(session, window, "echo ATOMIC_TEST_123", enter=False)
        time.sleep(0.1)

        content, cursor_x, cursor_y = _run(capture_pane_with_cursor_atomic_async(session, window))

        assert "ATOMIC_TEST_123" in content
        # Cursor should be at the end of what we typed
        assert cursor_x > 0

    def test_cursor_position_consistency(self, tmux_window):
        """Multiple captures should give consistent cursor when content unchanged."""
        session, window = tmux_window

        # Type something and wait
        tmux.send_keys(session, window, "echo stable", enter=False)
        time.sleep(0.2)

        # Capture multiple times
        results = []
        for _ in range(3):
            content, cursor_x, cursor_y = _run(
                capture_pane_with_cursor_atomic_async(session, window)
            )
            results.append((content, cursor_x, cursor_y))
            time.sleep(0.05)

        # All captures should have same cursor position
        cursors = [(r[1], r[2]) for r in results]
        assert all(c == cursors[0] for c in cursors), f"Cursor positions varied: {cursors}"


class TestHistoryCapture:
    """Tests for capture_pane_with_history_async."""

    def test_captures_with_history(self, tmux_window):
        """History capture should return content and metadata."""
        session, window = tmux_window

        content, cursor_x, cursor_y, total_lines = _run(
            capture_pane_with_history_async(session, window)
        )

        assert isinstance(content, str)
        assert isinstance(cursor_x, int)
        assert isinstance(cursor_y, int)
        assert isinstance(total_lines, int)
        assert total_lines >= 1

    def test_captures_scrollback(self, tmux_window):
        """History capture should include scrollback content."""
        session, window = tmux_window

        # Generate some output to create scrollback
        for i in range(5):
            tmux.send_keys(session, window, f"echo LINE_{i}", enter=True)
            time.sleep(0.1)

        time.sleep(0.3)

        content, _, _, total_lines = _run(
            capture_pane_with_history_async(session, window, scrollback_lines=100)
        )

        # Should have captured all the lines
        for i in range(5):
            assert f"LINE_{i}" in content, f"Missing LINE_{i} in captured content"

    def test_respects_scrollback_limit(self, tmux_window):
        """History capture should respect the scrollback_lines parameter."""
        session, window = tmux_window

        # Small scrollback should still work
        content, cursor_x, cursor_y, total_lines = _run(
            capture_pane_with_history_async(session, window, scrollback_lines=10)
        )

        assert isinstance(content, str)
        assert cursor_x >= 0
        assert cursor_y >= 0


class TestNonexistentTarget:
    """Tests for error handling with nonexistent targets."""

    def test_atomic_capture_nonexistent(self):
        """Atomic capture should return empty on nonexistent target."""
        content, cursor_x, cursor_y = _run(
            capture_pane_with_cursor_atomic_async("nonexistent", "window")
        )

        # Should return defaults, not crash
        assert content == ""
        assert cursor_x == 0
        assert cursor_y == 0

    def test_history_capture_nonexistent(self):
        """History capture should return empty on nonexistent target."""
        content, cursor_x, cursor_y, total_lines = _run(
            capture_pane_with_history_async("nonexistent", "window")
        )

        # Should return defaults, not crash
        assert cursor_x == 0
        assert cursor_y == 0
        assert total_lines == 0
