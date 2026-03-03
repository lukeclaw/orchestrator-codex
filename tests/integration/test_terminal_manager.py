"""Integration tests for tmux manager — real tmux operations.

Uses worker-isolated session names from conftest.py for parallel execution.
"""

import time

import pytest

from orchestrator.terminal import manager as tmux

pytestmark = pytest.mark.allow_subprocess


class TestTmuxManager:
    def test_create_session(self, tmux_session_name):
        assert tmux.create_session(tmux_session_name) is True
        assert tmux.session_exists(tmux_session_name) is True

    def test_create_session_idempotent(self, tmux_session_name):
        tmux.create_session(tmux_session_name)
        assert tmux.create_session(tmux_session_name) is False

    def test_create_window(self, tmux_session_name):
        tmux.create_session(tmux_session_name)
        target = tmux.create_window(tmux_session_name, "test-win")
        assert target == f"{tmux_session_name}:test-win"
        windows = tmux.list_windows(tmux_session_name)
        names = [w.name for w in windows]
        assert "test-win" in names

    def test_list_windows_empty_session(self):
        assert tmux.list_windows("nonexistent-session") == []

    def test_send_keys_and_capture(self, tmux_session_name):
        tmux.create_session(tmux_session_name)
        tmux.create_window(tmux_session_name, "echo-test")

        # Send a command
        tmux.send_keys(tmux_session_name, "echo-test", "echo HELLO_ORCH_TEST")

        # Wait for command to execute
        time.sleep(0.5)

        output = tmux.capture_output(tmux_session_name, "echo-test", lines=10)
        assert "HELLO_ORCH_TEST" in output

    def test_kill_window(self, tmux_session_name):
        tmux.create_session(tmux_session_name)
        tmux.create_window(tmux_session_name, "kill-me")
        assert tmux.kill_window(tmux_session_name, "kill-me") is True
        windows = tmux.list_windows(tmux_session_name)
        names = [w.name for w in windows]
        assert "kill-me" not in names

    def test_kill_session(self, tmux_session_name):
        tmux.create_session(tmux_session_name)
        assert tmux.kill_session(tmux_session_name) is True
        assert tmux.session_exists(tmux_session_name) is False

    def test_send_keys_dash_prefix(self, tmux_session_name):
        """Text starting with '-' must not be misinterpreted as tmux flags."""
        tmux.create_session(tmux_session_name)
        tmux.create_window(tmux_session_name, "dash-test")

        # This text starts with '-' which previously caused tmux to parse it
        # as option flags (missing '--' end-of-options separator).
        assert tmux.send_keys(tmux_session_name, "dash-test", 'echo "-logging-level debug"') is True

        time.sleep(0.5)
        output = tmux.capture_output(tmux_session_name, "dash-test", lines=10)
        assert "-logging-level debug" in output

    def test_send_keys_literal_dash_prefix(self, tmux_session_name):
        """send_keys_literal must handle text starting with '-'."""
        tmux.create_session(tmux_session_name)
        tmux.create_window(tmux_session_name, "dash-lit")

        assert tmux.send_keys_literal(tmux_session_name, "dash-lit", "-logging-level debug") is True

        time.sleep(0.5)
        output = tmux.capture_output(tmux_session_name, "dash-lit", lines=10)
        assert "-logging-level" in output

    def test_capture_nonexistent_window(self, tmux_session_name):
        output = tmux.capture_output(tmux_session_name, "nonexistent", lines=10)
        assert output == ""
