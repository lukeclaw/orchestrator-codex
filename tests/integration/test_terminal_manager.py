"""Integration tests for tmux manager — real tmux operations."""

import pytest

from orchestrator.terminal import manager as tmux

TEST_SESSION = "orch-test-session"


@pytest.fixture(autouse=True)
def cleanup_tmux():
    """Ensure test session is cleaned up after each test."""
    yield
    tmux.kill_session(TEST_SESSION)


class TestTmuxManager:
    def test_create_session(self):
        assert tmux.create_session(TEST_SESSION) is True
        assert tmux.session_exists(TEST_SESSION) is True

    def test_create_session_idempotent(self):
        tmux.create_session(TEST_SESSION)
        assert tmux.create_session(TEST_SESSION) is False

    def test_create_window(self):
        tmux.create_session(TEST_SESSION)
        target = tmux.create_window(TEST_SESSION, "test-win")
        assert target == f"{TEST_SESSION}:test-win"
        windows = tmux.list_windows(TEST_SESSION)
        names = [w.name for w in windows]
        assert "test-win" in names

    def test_list_windows_empty_session(self):
        assert tmux.list_windows("nonexistent-session") == []

    def test_send_keys_and_capture(self):
        tmux.create_session(TEST_SESSION)
        tmux.create_window(TEST_SESSION, "echo-test")

        # Send a command
        tmux.send_keys(TEST_SESSION, "echo-test", "echo HELLO_ORCH_TEST")

        # Wait for command to execute
        import time
        time.sleep(0.5)

        output = tmux.capture_output(TEST_SESSION, "echo-test", lines=10)
        assert "HELLO_ORCH_TEST" in output

    def test_kill_window(self):
        tmux.create_session(TEST_SESSION)
        tmux.create_window(TEST_SESSION, "kill-me")
        assert tmux.kill_window(TEST_SESSION, "kill-me") is True
        windows = tmux.list_windows(TEST_SESSION)
        names = [w.name for w in windows]
        assert "kill-me" not in names

    def test_kill_session(self):
        tmux.create_session(TEST_SESSION)
        assert tmux.kill_session(TEST_SESSION) is True
        assert tmux.session_exists(TEST_SESSION) is False

    def test_capture_nonexistent_window(self):
        output = tmux.capture_output(TEST_SESSION, "nonexistent", lines=10)
        assert output == ""
