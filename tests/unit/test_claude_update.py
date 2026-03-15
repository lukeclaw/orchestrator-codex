"""Tests for orchestrator.terminal.claude_update module."""

from unittest.mock import MagicMock, patch

from orchestrator.terminal.claude_update import (
    get_claude_update_chain_command,
    run_claude_update,
    should_update_before_start,
)


class TestShouldUpdateBeforeStart:
    """Tests for should_update_before_start()."""

    def test_default_false_when_not_set(self):
        """Returns False when config key is absent (default)."""
        conn = MagicMock()
        with patch(
            "orchestrator.terminal.claude_update.get_config_value", return_value=False
        ) as mock_get:
            result = should_update_before_start(conn)
            assert result is False
            mock_get.assert_called_once_with(conn, "claude.update_before_start", default=False)

    def test_returns_true_when_set_true(self):
        """Returns True when config value is True."""
        conn = MagicMock()
        with patch("orchestrator.terminal.claude_update.get_config_value", return_value=True):
            assert should_update_before_start(conn) is True

    def test_returns_false_when_set_false(self):
        """Returns False when config value is False."""
        conn = MagicMock()
        with patch("orchestrator.terminal.claude_update.get_config_value", return_value=False):
            assert should_update_before_start(conn) is False

    def test_coerces_truthy_values(self):
        """Non-bool truthy values are coerced to True."""
        conn = MagicMock()
        with patch("orchestrator.terminal.claude_update.get_config_value", return_value=1):
            assert should_update_before_start(conn) is True

    def test_coerces_falsy_values(self):
        """Non-bool falsy values are coerced to False."""
        conn = MagicMock()
        with patch("orchestrator.terminal.claude_update.get_config_value", return_value=0):
            assert should_update_before_start(conn) is False


class TestRunClaudeUpdate:
    """Tests for run_claude_update()."""

    def test_sends_inline_marker_command(self):
        """Sends claude update with inline done-marker on same command line."""
        send_keys_fn = MagicMock()
        # Return marker on first capture so polling exits immediately
        capture_fn = MagicMock(return_value="__UPDATE_DONE_12345__\n")

        with patch("orchestrator.terminal.claude_update.random.randint", return_value=12345):
            with patch("orchestrator.terminal.claude_update.time.sleep"):
                result = run_claude_update(send_keys_fn, capture_fn, "sess", "win")

        assert result is True
        send_keys_fn.assert_called_once_with(
            "sess",
            "win",
            "claude update 2>/dev/null || true; echo __UPDATE_DONE_12345__",
            enter=True,
        )

    def test_returns_true_when_marker_found(self):
        """Returns True when done-marker appears in captured output."""
        send_keys_fn = MagicMock()
        # Simulate: first poll no marker, second poll has marker
        capture_fn = MagicMock(
            side_effect=[
                "Current version: 2.1.58\nChecking for updates...\n",
                "Claude Code is up to date (2.1.58)\n__UPDATE_DONE_99999__\n➜  brain",
            ]
        )

        with patch("orchestrator.terminal.claude_update.random.randint", return_value=99999):
            with patch("orchestrator.terminal.claude_update.time.sleep"):
                result = run_claude_update(send_keys_fn, capture_fn, "sess", "win")

        assert result is True
        assert capture_fn.call_count == 2

    def test_returns_false_on_timeout(self):
        """Returns False when marker never appears within timeout."""
        send_keys_fn = MagicMock()
        # Marker never appears
        capture_fn = MagicMock(return_value="still running...\n")

        with patch("orchestrator.terminal.claude_update.random.randint", return_value=11111):
            # Mock time so the loop times out after one iteration
            with patch("orchestrator.terminal.claude_update.time.sleep"):
                with patch(
                    "orchestrator.terminal.claude_update.time.time",
                    side_effect=[0.0, 0.0, 31.0],
                ):
                    result = run_claude_update(send_keys_fn, capture_fn, "sess", "win", timeout=30)

        assert result is False

    def test_does_not_match_marker_in_command_echo(self):
        """Marker in the command echo line (not alone) is not matched."""
        send_keys_fn = MagicMock()
        # The command echo contains the marker but it's not on its own line
        capture_fn = MagicMock(
            side_effect=[
                "claude update 2>/dev/null || true; echo __UPDATE_DONE_55555__\n",
                "Claude Code is up to date\n__UPDATE_DONE_55555__\n",
            ]
        )

        with patch("orchestrator.terminal.claude_update.random.randint", return_value=55555):
            with patch("orchestrator.terminal.claude_update.time.sleep"):
                result = run_claude_update(send_keys_fn, capture_fn, "sess", "win")

        assert result is True
        # First capture: marker is in command echo (not stripped-equal), no match
        # Second capture: marker is on its own line, match
        assert capture_fn.call_count == 2

    def test_polls_with_short_interval(self):
        """Polls at 0.5s intervals for low latency."""
        send_keys_fn = MagicMock()
        capture_fn = MagicMock(return_value="__UPDATE_DONE_77777__\n")
        sleep_calls = []

        with patch("orchestrator.terminal.claude_update.random.randint", return_value=77777):
            with patch(
                "orchestrator.terminal.claude_update.time.sleep",
                side_effect=lambda s: sleep_calls.append(s),
            ):
                run_claude_update(send_keys_fn, capture_fn, "sess", "win")

        assert sleep_calls == [0.5]


class TestGetClaudeUpdateChainCommand:
    """Tests for get_claude_update_chain_command()."""

    def test_returns_subshell_command(self):
        """Returns command wrapped in subshell with || true."""
        result = get_claude_update_chain_command()
        assert result == "(claude update 2>/dev/null || true)"

    def test_works_in_chain(self):
        """Command can be joined with && in a chain."""
        parts = ["cd /tmp", get_claude_update_chain_command(), "claude --help"]
        cmd = " && ".join(parts)
        assert "(claude update 2>/dev/null || true)" in cmd
        assert cmd.startswith("cd /tmp && ")
        assert cmd.endswith(" && claude --help")
