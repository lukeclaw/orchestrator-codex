"""Unit tests for send_to_session Enter key retry logic."""

from unittest.mock import patch


class TestVerifyMessageSent:
    """Test _verify_message_sent detection logic."""

    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session.tmux")
    def test_empty_output_assumes_sent(self, mock_tmux, _sleep):
        """Empty terminal output should assume message was sent."""
        from orchestrator.terminal.session import _verify_message_sent

        mock_tmux.capture_output.return_value = ""

        result = _verify_message_sent("orchestrator", "test-window", "Hello world")

        assert result is True

    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session.tmux")
    def test_short_message_not_checked_for_tail(self, mock_tmux, _sleep):
        """Short messages (<=50 chars) skip tail matching check."""
        from orchestrator.terminal.session import _verify_message_sent

        # Short message with matching text in output - should still pass
        mock_tmux.capture_output.return_value = "Short msg\n> Short msg"

        result = _verify_message_sent("orchestrator", "test-window", "Short msg")

        # Short messages don't trigger tail matching, but > check might trigger
        # Actually, "> Short msg" is only 11 chars after ">", so it passes
        assert result is True

    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session.tmux")
    def test_long_message_stuck_in_input_detected(self, mock_tmux, _sleep):
        """Long message with tail visible in last line should be detected as stuck."""
        from orchestrator.terminal.session import _verify_message_sent

        long_message = "This is a very long task description that spans multiple words and contains detailed instructions for the worker to follow carefully."
        # Simulate the message being stuck - last line shows end of the message
        mock_tmux.capture_output.return_value = f"Previous output\n> {long_message[-60:]}"

        result = _verify_message_sent("orchestrator", "test-window", long_message)

        assert result is False

    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session.tmux")
    def test_message_sent_successfully_no_tail(self, mock_tmux, _sleep):
        """Successfully sent message - Claude processing, no message tail visible."""
        from orchestrator.terminal.session import _verify_message_sent

        long_message = "This is a very long task description that spans multiple words and contains detailed instructions for the worker to follow carefully."
        # Claude is now processing - shows thinking indicator, not the message
        mock_tmux.capture_output.return_value = "⏳ Thinking..."

        result = _verify_message_sent("orchestrator", "test-window", long_message)

        assert result is True

    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session.tmux")
    def test_prompt_with_substantial_text_detected(self, mock_tmux, _sleep):
        """Prompt line with substantial text after > should be detected as stuck."""
        from orchestrator.terminal.session import _verify_message_sent

        # Short message but showing text stuck after prompt
        mock_tmux.capture_output.return_value = (
            "Previous line\n> This is some text that appears to be stuck in the input line"
        )

        result = _verify_message_sent("orchestrator", "test-window", "short")

        # > with >20 chars after it triggers the stuck detection
        assert result is False

    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session.tmux")
    def test_empty_prompt_line_is_ok(self, mock_tmux, _sleep):
        """Empty prompt line (just >) should not be detected as stuck."""
        from orchestrator.terminal.session import _verify_message_sent

        mock_tmux.capture_output.return_value = "Previous output\n>"

        result = _verify_message_sent("orchestrator", "test-window", "any message")

        assert result is True

    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session.tmux")
    def test_prompt_with_short_text_is_ok(self, mock_tmux, _sleep):
        """Prompt with short text (<=20 chars) should not trigger stuck detection."""
        from orchestrator.terminal.session import _verify_message_sent

        mock_tmux.capture_output.return_value = "Previous output\n> short text"

        result = _verify_message_sent("orchestrator", "test-window", "any message")

        assert result is True


class TestSendToSession:
    """Test send_to_session retry logic."""

    @patch("orchestrator.terminal.session._verify_message_sent")
    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session.tmux")
    def test_successful_send_no_retry(self, mock_tmux, _sleep, mock_verify):
        """Successful send on first try should not retry."""
        from orchestrator.terminal.session import send_to_session

        mock_tmux.paste_to_pane.return_value = True
        mock_tmux.send_keys.return_value = True
        mock_verify.return_value = True  # Message sent successfully

        result = send_to_session("test-window", "Hello world")

        assert result is True
        # send_keys should be called only once (no retries)
        assert mock_tmux.send_keys.call_count == 1
        # paste_to_pane should be used instead of send_keys_literal
        mock_tmux.paste_to_pane.assert_called_once()

    @patch("orchestrator.terminal.session._verify_message_sent")
    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session.tmux")
    def test_retry_on_stuck_message(self, mock_tmux, mock_sleep, mock_verify):
        """Should retry Enter if message appears stuck."""
        from orchestrator.terminal.session import send_to_session

        mock_tmux.paste_to_pane.return_value = True
        mock_tmux.send_keys.return_value = True
        # First attempt fails, second succeeds
        mock_verify.side_effect = [False, True]

        result = send_to_session("test-window", "Long message", retry_delay=0.1)

        assert result is True
        # send_keys should be called twice (1 initial + 1 retry)
        assert mock_tmux.send_keys.call_count == 2

    @patch("orchestrator.terminal.session._verify_message_sent")
    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session.tmux")
    def test_max_retries_exhausted(self, mock_tmux, mock_sleep, mock_verify):
        """Should return False after max retries exhausted."""
        from orchestrator.terminal.session import send_to_session

        mock_tmux.paste_to_pane.return_value = True
        mock_tmux.send_keys.return_value = True
        mock_verify.return_value = False  # Always fails

        result = send_to_session(
            "test-window", "Stuck message", max_enter_retries=3, retry_delay=0.1
        )

        assert result is False
        # send_keys called 3 times (max retries)
        assert mock_tmux.send_keys.call_count == 3

    @patch("orchestrator.terminal.session._verify_message_sent")
    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session.tmux")
    def test_success_on_third_attempt(self, mock_tmux, mock_sleep, mock_verify):
        """Should succeed if third attempt works."""
        from orchestrator.terminal.session import send_to_session

        mock_tmux.paste_to_pane.return_value = True
        mock_tmux.send_keys.return_value = True
        # Fail twice, succeed on third
        mock_verify.side_effect = [False, False, True]

        result = send_to_session(
            "test-window", "Eventually works", max_enter_retries=3, retry_delay=0.1
        )

        assert result is True
        assert mock_tmux.send_keys.call_count == 3

    @patch("orchestrator.terminal.session._verify_message_sent")
    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session.tmux")
    def test_paste_to_pane_failure_falls_back_to_literal(self, mock_tmux, _sleep, mock_verify):
        """Should fall back to send_keys_literal if paste_to_pane fails."""
        from orchestrator.terminal.session import send_to_session

        mock_tmux.paste_to_pane.return_value = False
        mock_tmux.send_keys_literal.return_value = True
        mock_tmux.send_keys.return_value = True
        mock_verify.return_value = True

        result = send_to_session("test-window", "Message")

        assert result is True
        # Both paste_to_pane and send_keys_literal should have been called
        mock_tmux.paste_to_pane.assert_called_once()
        mock_tmux.send_keys_literal.assert_called_once()

    @patch("orchestrator.terminal.session._verify_message_sent")
    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session.tmux")
    def test_both_paste_methods_fail(self, mock_tmux, _sleep, mock_verify):
        """Should return False if both paste_to_pane and send_keys_literal fail."""
        from orchestrator.terminal.session import send_to_session

        mock_tmux.paste_to_pane.return_value = False
        mock_tmux.send_keys_literal.return_value = False

        result = send_to_session("test-window", "Message")

        assert result is False
        # Should not try to send Enter if text delivery failed
        mock_tmux.send_keys.assert_not_called()

    @patch("orchestrator.terminal.session._verify_message_sent")
    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session.tmux")
    def test_send_keys_enter_failure(self, mock_tmux, _sleep, mock_verify):
        """Should return False if send_keys (Enter) fails."""
        from orchestrator.terminal.session import send_to_session

        mock_tmux.paste_to_pane.return_value = True
        mock_tmux.send_keys.return_value = False

        result = send_to_session("test-window", "Message")

        assert result is False
        # verify should not be called if Enter send failed
        mock_verify.assert_not_called()

    @patch("orchestrator.terminal.session._verify_message_sent")
    @patch("orchestrator.terminal.session.time.sleep")
    @patch("orchestrator.terminal.session.tmux")
    def test_custom_retry_parameters(self, mock_tmux, mock_sleep, mock_verify):
        """Should respect custom max_enter_retries and retry_delay."""
        from orchestrator.terminal.session import send_to_session

        mock_tmux.paste_to_pane.return_value = True
        mock_tmux.send_keys.return_value = True
        mock_verify.return_value = False  # Always fails

        result = send_to_session("test-window", "Message", max_enter_retries=5, retry_delay=1.5)

        assert result is False
        # Should have tried 5 times
        assert mock_tmux.send_keys.call_count == 5
