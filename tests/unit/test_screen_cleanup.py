"""Unit tests for screen session cleanup.

Tests _kill_orphaned_screen (session.py) uses anchored grep and iterates by PID.
"""

from unittest.mock import patch

import pytest

# Import reconnect first to resolve circular import between
# orchestrator.terminal.session <-> orchestrator.session.reconnect
import orchestrator.session.reconnect  # noqa: F401


class TestKillOrphanedScreen:
    """Tests for orchestrator.terminal.session._kill_orphaned_screen."""

    @patch("orchestrator.terminal.manager.send_keys")
    def test_sends_kill_command_that_iterates_by_pid(self, mock_send_keys):
        """Should send a command that lists matching PIDs and kills each individually."""
        from orchestrator.terminal.session import _kill_orphaned_screen

        with patch("orchestrator.terminal.session.time.sleep"):
            _kill_orphaned_screen("orch", "worker-1", "claude-abc123")

        mock_send_keys.assert_called_once()
        cmd = mock_send_keys.call_args[0][2]

        assert "screen -ls" in cmd
        assert "claude-abc123" in cmd
        assert "while read" in cmd
        assert "screen -X -S" in cmd
        assert "quit" in cmd

    @patch("orchestrator.terminal.manager.send_keys")
    def test_kills_by_full_session_id_not_bare_name(self, mock_send_keys):
        """The kill command must use the full $sid (pid.name) so it's unambiguous."""
        from orchestrator.terminal.session import _kill_orphaned_screen

        with patch("orchestrator.terminal.session.time.sleep"):
            _kill_orphaned_screen("orch", "w", "claude-xyz")

        cmd = mock_send_keys.call_args[0][2]
        assert "awk" in cmd
        assert "$1" in cmd

    @patch("orchestrator.terminal.manager.send_keys")
    def test_uses_anchored_grep(self, mock_send_keys):
        """grep must use -w to avoid matching session names that are superstrings."""
        from orchestrator.terminal.session import _kill_orphaned_screen

        with patch("orchestrator.terminal.session.time.sleep"):
            _kill_orphaned_screen("orch", "w", "claude-abc")

        cmd = mock_send_keys.call_args[0][2]
        assert "grep -w" in cmd


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
