"""Tests for check_screen_and_claude_remote in orchestrator.session.health.

Covers:
- Screen detection via both `screen -ls` and `ps aux` methods
- The ps grep pattern matching various screen process formats
  (screen -S, screen -rd, SCREEN uppercase, pid.name format)
- Decision tree: alive, screen_only, dead, screen_detached
- Safety net: Claude running without screen (orphaned process)
- SSH failure handling (auth, timeout, connection refused)
- SSH alive pre-check gating
"""

import subprocess
import unittest
from unittest.mock import MagicMock, patch

from orchestrator.session.health import check_screen_and_claude_remote

SESSION_ID = "b98879e8-1c79-4776-8b18-adc698fb661f"
SCREEN_NAME = f"claude-{SESSION_ID}"
HOST = "yuqiu-ld3.linkedin.biz"


def _ssh_result(stdout: str, stderr: str = "", returncode: int = 0):
    """Build a mock subprocess.run return value."""
    return MagicMock(stdout=stdout, stderr=stderr, returncode=returncode)


def _stdout(screen_ls: bool, screen_ps: bool, claude: bool) -> str:
    """Build stdout string from boolean flags."""
    parts = [
        "SCREEN_EXISTS" if screen_ls else "NO_SCREEN",
        "SCREEN_PS_EXISTS" if screen_ps else "NO_SCREEN_PS",
        "CLAUDE_RUNNING" if claude else "NO_CLAUDE",
    ]
    return "\n".join(parts) + "\n"


class TestScreenAndClaudeRemoteDecisionTree(unittest.TestCase):
    """Tests for the four-branch decision tree in check_screen_and_claude_remote."""

    @patch("orchestrator.session.health.subprocess.run")
    def test_alive_both_screen_and_claude(self, mock_run):
        """screen exists + Claude running → alive."""
        mock_run.return_value = _ssh_result(_stdout(True, True, True))
        status, reason = check_screen_and_claude_remote(HOST, SESSION_ID)
        assert status == "alive"
        assert "Claude is running" in reason

    @patch("orchestrator.session.health.subprocess.run")
    def test_alive_screen_ps_only(self, mock_run):
        """screen -ls misses but ps finds it + Claude running → alive."""
        mock_run.return_value = _ssh_result(_stdout(False, True, True))
        status, _ = check_screen_and_claude_remote(HOST, SESSION_ID)
        assert status == "alive"

    @patch("orchestrator.session.health.subprocess.run")
    def test_screen_only_no_claude(self, mock_run):
        """Screen exists but Claude not running → screen_only."""
        mock_run.return_value = _ssh_result(_stdout(True, False, False))
        status, reason = check_screen_and_claude_remote(HOST, SESSION_ID)
        assert status == "screen_only"
        assert "Claude not running" in reason

    @patch("orchestrator.session.health.subprocess.run")
    def test_dead_nothing_found(self, mock_run):
        """Neither screen nor Claude found → dead."""
        mock_run.return_value = _ssh_result(_stdout(False, False, False))
        status, reason = check_screen_and_claude_remote(HOST, SESSION_ID)
        assert status == "dead"
        assert SCREEN_NAME in reason

    @patch("orchestrator.session.health.subprocess.run")
    def test_claude_running_without_screen_returns_alive(self, mock_run):
        """Claude running but screen not found (orphaned) → alive, not dead.

        This is the key regression test: before the fix, this returned "dead"
        causing false disconnections when the screen parent process died but
        Claude survived as an orphan.
        """
        mock_run.return_value = _ssh_result(_stdout(False, False, True))
        status, reason = check_screen_and_claude_remote(HOST, SESSION_ID)
        assert status == "alive"
        assert "without screen" in reason
        assert "orphaned" in reason


class TestSSHFailureHandling(unittest.TestCase):
    """Tests for SSH transport failure cases."""

    @patch("orchestrator.session.health.subprocess.run")
    def test_permission_denied(self, mock_run):
        mock_run.return_value = _ssh_result(
            "", "Permission denied (publickey).", returncode=255
        )
        status, _ = check_screen_and_claude_remote(HOST, SESSION_ID)
        assert status == "screen_detached"

    @patch("orchestrator.session.health.subprocess.run")
    def test_connection_refused(self, mock_run):
        mock_run.return_value = _ssh_result(
            "", "Connection refused", returncode=255
        )
        status, _ = check_screen_and_claude_remote(HOST, SESSION_ID)
        assert status == "screen_detached"

    @patch("orchestrator.session.health.subprocess.run")
    def test_connection_timed_out(self, mock_run):
        mock_run.return_value = _ssh_result(
            "", "Connection timed out", returncode=255
        )
        status, _ = check_screen_and_claude_remote(HOST, SESSION_ID)
        assert status == "screen_detached"

    @patch("orchestrator.session.health.subprocess.run")
    def test_ssh_exit_255_empty_stdout(self, mock_run):
        mock_run.return_value = _ssh_result(
            "", "Host key verification failed.", returncode=255
        )
        status, _ = check_screen_and_claude_remote(HOST, SESSION_ID)
        assert status == "screen_detached"

    @patch("orchestrator.session.health.subprocess.run")
    def test_subprocess_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=10)
        status, _ = check_screen_and_claude_remote(HOST, SESSION_ID)
        assert status == "screen_detached"

    @patch("orchestrator.session.health.subprocess.run")
    def test_unexpected_exception(self, mock_run):
        mock_run.side_effect = OSError("Network is unreachable")
        status, _ = check_screen_and_claude_remote(HOST, SESSION_ID)
        assert status == "screen_detached"


class TestSSHAlivePreCheck(unittest.TestCase):
    """Tests for the SSH alive pre-check gating."""

    @patch("orchestrator.session.health.check_worker_ssh_alive")
    @patch("orchestrator.session.health.subprocess.run")
    def test_ssh_dead_returns_dead_immediately(self, mock_run, mock_ssh):
        """When SSH alive check fails, returns dead without running remote check."""
        mock_ssh.return_value = False
        status, reason = check_screen_and_claude_remote(
            HOST, SESSION_ID, tmux_sess="orch", tmux_win="ld3"
        )
        assert status == "dead"
        assert "SSH session appears disconnected" in reason
        mock_run.assert_not_called()

    @patch("orchestrator.session.health.check_worker_ssh_alive")
    @patch("orchestrator.session.health.subprocess.run")
    def test_ssh_alive_proceeds_to_remote_check(self, mock_run, mock_ssh):
        """When SSH alive check passes, proceeds to the remote screen check."""
        mock_ssh.return_value = True
        mock_run.return_value = _ssh_result(_stdout(True, True, True))
        status, _ = check_screen_and_claude_remote(
            HOST, SESSION_ID, tmux_sess="orch", tmux_win="ld3"
        )
        assert status == "alive"
        mock_run.assert_called_once()

    @patch("orchestrator.session.health.subprocess.run")
    def test_no_tmux_args_skips_ssh_check(self, mock_run):
        """When tmux_sess/tmux_win not provided, skips SSH alive check."""
        mock_run.return_value = _ssh_result(_stdout(True, True, True))
        status, _ = check_screen_and_claude_remote(HOST, SESSION_ID)
        assert status == "alive"


class TestGrepPatternMatchesScreenFormats(unittest.TestCase):
    """Verify the SSH command's grep pattern handles all screen process formats.

    The grep pattern `[s]creen.*{screen_name}` with -i must match:
    - `screen -S claude-xxx` (initial creation, lowercase)
    - `screen -rd claude-xxx` (reattach without PID prefix)
    - `screen -rd 3284354.claude-xxx` (reattach with PID.name format)
    - `SCREEN -S claude-xxx` (GNU Screen uppercase server process)
    """

    @patch("orchestrator.session.health.subprocess.run")
    def test_captures_check_cmd_with_case_insensitive_grep(self, mock_run):
        """The SSH command uses grep -qi for case-insensitive screen matching."""
        mock_run.return_value = _ssh_result(_stdout(False, False, False))
        check_screen_and_claude_remote(HOST, SESSION_ID)

        # Extract the check_cmd sent to SSH
        call_args = mock_run.call_args
        ssh_cmd_list = call_args[0][0]
        check_cmd = ssh_cmd_list[-1]  # last arg is the remote command

        # Verify grep uses -qi (case-insensitive + quiet)
        assert "grep -qi" in check_cmd

        # Verify pattern has no space before screen_name
        # (so it matches both "screen -S name" and "screen -rd pid.name")
        assert f"[s]creen.*{SCREEN_NAME}" in check_cmd

        # Verify the old broken pattern is NOT present
        assert f"[s]creen .* {SCREEN_NAME}" not in check_cmd

    @patch("orchestrator.session.health.subprocess.run")
    def test_screen_ls_with_banner_stderr(self, mock_run):
        """SSH banner in stderr should not affect parsing of stdout results."""
        banner = (
            "UNAUTHORIZED ACCESS TO THIS DEVICE IS PROHIBITED.\n"
            "Welcome to CBL-Mariner 2.0\n"
        )
        mock_run.return_value = _ssh_result(
            _stdout(False, True, True), stderr=banner
        )
        status, _ = check_screen_and_claude_remote(HOST, SESSION_ID)
        assert status == "alive"
