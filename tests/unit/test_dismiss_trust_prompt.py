"""Tests for dismiss_trust_prompt in orchestrator.terminal.manager."""

import unittest
from unittest.mock import call, patch

_MP = "orchestrator.terminal.manager"


class TestDismissTrustPrompt(unittest.TestCase):
    """Verify dismiss_trust_prompt only sends Enter when the prompt is visible."""

    @patch(f"{_MP}.send_keys")
    @patch(f"{_MP}.capture_output")
    @patch(f"{_MP}.time.sleep")
    def test_sends_enter_when_trust_prompt_visible(self, mock_sleep, mock_capture, mock_keys):
        """Enter is sent exactly once when the trust-folder text is in the pane."""
        from orchestrator.terminal.manager import dismiss_trust_prompt

        mock_capture.return_value = (
            "Welcome to Claude Code!\n\nDo you trust this folder? Yes, trust this folder\n> "
        )

        dismiss_trust_prompt("sess", "win")

        mock_keys.assert_called_once_with("sess", "win", "", enter=True)

    @patch(f"{_MP}.send_keys")
    @patch(f"{_MP}.capture_output")
    @patch(f"{_MP}.time.sleep")
    def test_no_enter_when_prompt_absent(self, mock_sleep, mock_capture, mock_keys):
        """No Enter sent when pane shows normal Claude startup (no trust prompt)."""
        from orchestrator.terminal.manager import dismiss_trust_prompt

        mock_capture.return_value = "Welcome to Claude Code!\n\n> "

        dismiss_trust_prompt("sess", "win")

        mock_keys.assert_not_called()

    @patch(f"{_MP}.send_keys")
    @patch(f"{_MP}.capture_output")
    @patch(f"{_MP}.time.sleep")
    def test_detects_trust_project_variant(self, mock_sleep, mock_capture, mock_keys):
        """Also detects the 'trust this project' phrasing."""
        from orchestrator.terminal.manager import dismiss_trust_prompt

        mock_capture.return_value = "Do you trust this project?\n> "

        dismiss_trust_prompt("sess", "win")

        mock_keys.assert_called_once()

    @patch(f"{_MP}.send_keys")
    @patch(f"{_MP}.capture_output")
    @patch(f"{_MP}.time.sleep")
    def test_retries_until_prompt_appears(self, mock_sleep, mock_capture, mock_keys):
        """Polls multiple times; sends Enter only when prompt finally appears."""
        from orchestrator.terminal.manager import dismiss_trust_prompt

        mock_capture.side_effect = [
            "Loading...",  # check 1: no prompt yet
            "Do you trust this folder?\n> ",  # check 2: prompt appeared
        ]

        dismiss_trust_prompt("sess", "win", max_checks=3)

        mock_keys.assert_called_once_with("sess", "win", "", enter=True)
        assert mock_capture.call_count == 2

    @patch("orchestrator.api.ws_terminal.is_user_active", return_value=True)
    @patch(f"{_MP}.send_keys")
    @patch(f"{_MP}.capture_output")
    @patch(f"{_MP}.time.sleep")
    def test_skips_when_user_active(self, mock_sleep, mock_capture, mock_keys, mock_active):
        """Bails early without sending Enter if user is typing."""
        from orchestrator.terminal.manager import dismiss_trust_prompt

        dismiss_trust_prompt("sess", "win", session_id="s1")

        mock_capture.assert_not_called()
        mock_keys.assert_not_called()

    @patch(f"{_MP}.send_keys")
    @patch(f"{_MP}.capture_output")
    @patch(f"{_MP}.time.sleep")
    def test_case_insensitive_detection(self, mock_sleep, mock_capture, mock_keys):
        """Detection is case-insensitive."""
        from orchestrator.terminal.manager import dismiss_trust_prompt

        mock_capture.return_value = "TRUST THIS FOLDER"

        dismiss_trust_prompt("sess", "win")

        mock_keys.assert_called_once()

    @patch(f"{_MP}.send_keys")
    @patch(f"{_MP}.capture_output")
    @patch(f"{_MP}.time.sleep")
    def test_respects_max_checks(self, mock_sleep, mock_capture, mock_keys):
        """Stops polling after max_checks even if prompt never appears."""
        from orchestrator.terminal.manager import dismiss_trust_prompt

        mock_capture.return_value = "Welcome to Claude Code!\n\n> "

        dismiss_trust_prompt("sess", "win", max_checks=2, delay=0.5)

        assert mock_capture.call_count == 2
        mock_keys.assert_not_called()
        # Should sleep between checks (delay) and initial_wait
        sleep_calls = [c for c in mock_sleep.call_args_list if c != call(3.0)]
        assert len(sleep_calls) == 1  # one delay between the 2 checks


if __name__ == "__main__":
    unittest.main()
