"""Tests for check_screen_and_claude_remote and pane-attachment detection.

Covers:
- Screen detection via both `screen -ls` and `ps aux` methods
- The ps grep pattern matching various screen process formats
  (screen -S, screen -rd, SCREEN uppercase, pid.name format)
- Decision tree: alive, screen_only, dead, screen_detached
- Safety net: Claude running without screen (orphaned process)
- SSH failure handling (auth, timeout, connection refused)
- SSH alive pre-check gating
- Pane attachment detection via check_and_update_worker_health
"""

import subprocess
import unittest
from unittest.mock import MagicMock, patch

from orchestrator.session.health import (
    check_all_workers_health,
    check_and_update_worker_health,
    check_screen_and_claude_remote,
)
from orchestrator.state.models import Session

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
        mock_run.return_value = _ssh_result("", "Permission denied (publickey).", returncode=255)
        status, _ = check_screen_and_claude_remote(HOST, SESSION_ID)
        assert status == "screen_detached"

    @patch("orchestrator.session.health.subprocess.run")
    def test_connection_refused(self, mock_run):
        mock_run.return_value = _ssh_result("", "Connection refused", returncode=255)
        status, _ = check_screen_and_claude_remote(HOST, SESSION_ID)
        assert status == "screen_detached"

    @patch("orchestrator.session.health.subprocess.run")
    def test_connection_timed_out(self, mock_run):
        mock_run.return_value = _ssh_result("", "Connection timed out", returncode=255)
        status, _ = check_screen_and_claude_remote(HOST, SESSION_ID)
        assert status == "screen_detached"

    @patch("orchestrator.session.health.subprocess.run")
    def test_ssh_exit_255_empty_stdout(self, mock_run):
        mock_run.return_value = _ssh_result("", "Host key verification failed.", returncode=255)
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
        banner = "UNAUTHORIZED ACCESS TO THIS DEVICE IS PROHIBITED.\nWelcome to CBL-Mariner 2.0\n"
        mock_run.return_value = _ssh_result(_stdout(False, True, True), stderr=banner)
        status, _ = check_screen_and_claude_remote(HOST, SESSION_ID)
        assert status == "alive"


class TestClaudeSessionIdInGrepPattern(unittest.TestCase):
    """Verify the SSH grep checks for both session_id and claude_session_id."""

    @patch("orchestrator.session.health.subprocess.run")
    def test_grep_includes_both_ids_when_different(self, mock_run):
        """When claude_session_id differs from session_id, grep pattern has both."""
        mock_run.return_value = _ssh_result(_stdout(False, False, False))
        check_screen_and_claude_remote(
            HOST,
            SESSION_ID,
            claude_session_id="new-claude-id-xyz",
        )
        check_cmd = mock_run.call_args[0][0][-1]
        # grep -qE pattern should contain both IDs separated by |
        assert SESSION_ID in check_cmd
        assert "new-claude-id-xyz" in check_cmd
        assert f"({SESSION_ID}|new-claude-id-xyz)" in check_cmd

    @patch("orchestrator.session.health.subprocess.run")
    def test_grep_uses_single_id_when_same(self, mock_run):
        """When claude_session_id == session_id, no duplicate in pattern."""
        mock_run.return_value = _ssh_result(_stdout(False, False, False))
        check_screen_and_claude_remote(
            HOST,
            SESSION_ID,
            claude_session_id=SESSION_ID,
        )
        check_cmd = mock_run.call_args[0][0][-1]
        # Should just have the single ID, no pipe
        assert f"({SESSION_ID})" in check_cmd
        assert "|" not in check_cmd.split("grep -qE")[-1].split("&&")[0]

    @patch("orchestrator.session.health.subprocess.run")
    def test_grep_uses_single_id_when_claude_session_id_none(self, mock_run):
        """When claude_session_id is None, grep only has session_id."""
        mock_run.return_value = _ssh_result(_stdout(False, False, False))
        check_screen_and_claude_remote(
            HOST,
            SESSION_ID,
            claude_session_id=None,
        )
        check_cmd = mock_run.call_args[0][0][-1]
        assert f"({SESSION_ID})" in check_cmd


# ---------------------------------------------------------------------------
# Helpers for check_and_update_worker_health tests
# ---------------------------------------------------------------------------


def _make_remote_session(**overrides) -> Session:
    """Create a remote Session suitable for check_and_update_worker_health."""
    defaults = {
        "id": SESSION_ID,
        "name": "worker-ld3",
        "host": HOST,
        "work_dir": "/tmp/work",
        "status": "working",
    }
    defaults.update(overrides)
    return Session(**defaults)


# Common patch targets for check_and_update_worker_health tests
_HEALTH = "orchestrator.session.health"
_SCREEN_ALIVE = ("alive", "Screen + Claude running")


class TestPaneAttachmentDetection(unittest.TestCase):
    """Tests for RWS PTY health check scenarios for remote sessions.

    All remote sessions now route through _check_rws_pty_health, which queries
    the RWS daemon for PTY status instead of using the legacy screen/TUI path.
    """

    @patch("orchestrator.terminal.remote_worker_server._server_pool")
    @patch(f"{_HEALTH}.is_remote_host", return_value=True)
    @patch(f"{_HEALTH}.repo")
    def test_rws_pty_dead_returns_disconnected(
        self,
        mock_repo,
        mock_is_remote,
        mock_pool,
    ):
        """RWS PTY dead → disconnected + needs_reconnect."""
        session = _make_remote_session(rws_pty_id="pty-123")
        tunnel_mgr = MagicMock()
        tunnel_mgr.is_alive.return_value = True

        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"ptys": [{"pty_id": "pty-123", "alive": False}]}
        mock_pool.get.return_value = mock_rws

        result = check_and_update_worker_health(MagicMock(), session, tunnel_manager=tunnel_mgr)

        assert result["alive"] is False
        assert result["status"] == "disconnected"
        assert result["needs_reconnect"] is True

    @patch("orchestrator.terminal.remote_worker_server._server_pool")
    @patch(f"{_HEALTH}.is_remote_host", return_value=True)
    @patch(f"{_HEALTH}.repo")
    def test_alive_and_pty_alive_returns_alive(
        self,
        mock_repo,
        mock_is_remote,
        mock_pool,
    ):
        """RWS PTY alive → alive with tunnel_alive."""
        session = _make_remote_session(rws_pty_id="pty-123")
        tunnel_mgr = MagicMock()
        tunnel_mgr.is_alive.return_value = True

        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"ptys": [{"pty_id": "pty-123", "alive": True}]}
        mock_pool.get.return_value = mock_rws

        result = check_and_update_worker_health(MagicMock(), session, tunnel_manager=tunnel_mgr)

        assert result["alive"] is True
        assert result["tunnel_alive"] is True

    @patch("orchestrator.terminal.remote_worker_server._server_pool")
    @patch(f"{_HEALTH}.is_remote_host", return_value=True)
    @patch(f"{_HEALTH}.repo")
    def test_tunnel_restart_success_still_checks_pty(
        self,
        mock_repo,
        mock_is_remote,
        mock_pool,
    ):
        """Tunnel dead → restart succeeds → PTY alive → alive + tunnel_reconnected.

        Ensures the tunnel restart success path still performs the PTY check.
        """
        session = _make_remote_session(rws_pty_id="pty-123")
        tunnel_mgr = MagicMock()
        tunnel_mgr.is_alive.return_value = False  # tunnel initially dead
        tunnel_mgr.restart_tunnel.return_value = 12345  # restart succeeds

        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"ptys": [{"pty_id": "pty-123", "alive": True}]}
        mock_pool.get.return_value = mock_rws

        result = check_and_update_worker_health(MagicMock(), session, tunnel_manager=tunnel_mgr)

        assert result["alive"] is True
        assert result["tunnel_reconnected"] is True
        assert result["tunnel_alive"] is True

    @patch("orchestrator.terminal.remote_worker_server._server_pool")
    @patch(f"{_HEALTH}.is_remote_host", return_value=True)
    @patch(f"{_HEALTH}.repo")
    def test_tunnel_restart_success_and_pty_alive_returns_alive(
        self,
        mock_repo,
        mock_is_remote,
        mock_pool,
    ):
        """Tunnel dead → restart succeeds → PTY alive → alive + tunnel_reconnected."""
        session = _make_remote_session(rws_pty_id="pty-123", status="waiting")
        tunnel_mgr = MagicMock()
        tunnel_mgr.is_alive.return_value = False
        tunnel_mgr.restart_tunnel.return_value = 12345

        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"ptys": [{"pty_id": "pty-123", "alive": True}]}
        mock_pool.get.return_value = mock_rws

        result = check_and_update_worker_health(MagicMock(), session, tunnel_manager=tunnel_mgr)

        assert result["alive"] is True
        assert result["status"] == "waiting"
        assert result["tunnel_reconnected"] is True
        assert result["tunnel_alive"] is True


class TestRdevRecoveryStatus(unittest.TestCase):
    """Test that rdev recovery via RWS PTY returns the updated status, not the stale one."""

    def _make_rws_mock(self, pty_id="pty-123", alive=True):
        """Create a mock RWS that responds to pty_list with the given PTY status."""
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"ptys": [{"pty_id": pty_id, "alive": alive}]}
        return mock_rws

    @patch("orchestrator.terminal.remote_worker_server._server_pool")
    @patch(f"{_HEALTH}.is_remote_host", return_value=True)
    @patch(f"{_HEALTH}.repo")
    def test_recovery_from_disconnected_returns_waiting(
        self,
        mock_repo,
        mock_is_remote,
        mock_pool,
    ):
        """Rdev worker recovering from disconnected should return status='waiting'."""
        session = _make_remote_session(status="disconnected", rws_pty_id="pty-123")
        tunnel_mgr = MagicMock()
        tunnel_mgr.is_alive.return_value = True

        mock_pool.get.return_value = self._make_rws_mock()

        result = check_and_update_worker_health(MagicMock(), session, tunnel_manager=tunnel_mgr)

        assert result["alive"] is True
        assert result["status"] == "waiting"  # not stale "disconnected"
        mock_repo.update_session.assert_called_once()

    @patch("orchestrator.terminal.remote_worker_server._server_pool")
    @patch(f"{_HEALTH}.is_remote_host", return_value=True)
    @patch(f"{_HEALTH}.repo")
    def test_recovery_from_error_returns_waiting(
        self,
        mock_repo,
        mock_is_remote,
        mock_pool,
    ):
        """Rdev worker recovering from error should return status='waiting'."""
        session = _make_remote_session(status="error", rws_pty_id="pty-123")
        tunnel_mgr = MagicMock()
        tunnel_mgr.is_alive.return_value = True

        mock_pool.get.return_value = self._make_rws_mock()

        result = check_and_update_worker_health(MagicMock(), session, tunnel_manager=tunnel_mgr)

        assert result["alive"] is True
        assert result["status"] == "waiting"  # not stale "error"

    @patch("orchestrator.terminal.remote_worker_server._server_pool")
    @patch(f"{_HEALTH}.is_remote_host", return_value=True)
    @patch(f"{_HEALTH}.repo")
    def test_recovery_from_screen_detached_returns_waiting(
        self,
        mock_repo,
        mock_is_remote,
        mock_pool,
    ):
        """Rdev worker recovering from screen_detached should return status='waiting'."""
        session = _make_remote_session(status="screen_detached", rws_pty_id="pty-123")
        tunnel_mgr = MagicMock()
        tunnel_mgr.is_alive.return_value = True

        mock_pool.get.return_value = self._make_rws_mock()

        result = check_and_update_worker_health(MagicMock(), session, tunnel_manager=tunnel_mgr)

        assert result["alive"] is True
        assert result["status"] == "waiting"

    @patch("orchestrator.terminal.remote_worker_server._server_pool")
    @patch(f"{_HEALTH}.is_remote_host", return_value=True)
    @patch(f"{_HEALTH}.repo")
    def test_working_status_not_changed_on_alive(
        self,
        mock_repo,
        mock_is_remote,
        mock_pool,
    ):
        """Rdev worker already 'working' should keep that status."""
        session = _make_remote_session(status="working", rws_pty_id="pty-123")
        tunnel_mgr = MagicMock()
        tunnel_mgr.is_alive.return_value = True

        mock_pool.get.return_value = self._make_rws_mock()

        result = check_and_update_worker_health(MagicMock(), session, tunnel_manager=tunnel_mgr)

        assert result["alive"] is True
        assert result["status"] == "working"
        mock_repo.update_session.assert_not_called()


class TestDisconnectedWorkersHealthCheckedFirst(unittest.TestCase):
    """Tests that check_all_workers_health health-checks disconnected workers
    before jumping to reconnect, so self-recovered workers skip the heavier
    reconnect path."""

    @patch(f"{_HEALTH}.is_user_active", return_value=False)
    @patch(f"{_HEALTH}.check_and_update_worker_health")
    @patch(f"{_HEALTH}.repo")
    def test_disconnected_auto_reconnect_alive_skips_reconnect(
        self,
        mock_repo,
        mock_health,
        mock_active,
    ):
        """Disconnected + auto_reconnect + health alive → no reconnect needed."""
        session = Session(
            id=SESSION_ID,
            name="worker-ld3",
            host="localhost",
            work_dir="/tmp",
            status="disconnected",
            auto_reconnect=True,
        )
        mock_health.return_value = {
            "alive": True,
            "status": "waiting",
            "reason": "Claude running in pane",
        }
        db = MagicMock()

        with patch("orchestrator.session.reconnect.trigger_reconnect") as mock_reconnect:
            results = check_all_workers_health(db, [session])

        assert session.name in results["alive"]
        assert session.name not in results.get("auto_reconnected", [])
        mock_reconnect.assert_not_called()

    @patch(f"{_HEALTH}.is_user_active", return_value=False)
    @patch(f"{_HEALTH}.check_and_update_worker_health")
    @patch(f"{_HEALTH}.repo")
    def test_disconnected_auto_reconnect_dead_triggers_reconnect(
        self,
        mock_repo,
        mock_health,
        mock_active,
    ):
        """Disconnected + auto_reconnect + health dead → reconnect triggered."""
        session = Session(
            id=SESSION_ID,
            name="worker-ld3",
            host="localhost",
            work_dir="/tmp",
            status="disconnected",
            auto_reconnect=True,
        )
        mock_health.return_value = {
            "alive": False,
            "status": "disconnected",
            "reason": "No Claude process",
            "needs_reconnect": True,
        }
        db = MagicMock()

        with patch("orchestrator.session.reconnect.trigger_reconnect") as mock_reconnect:
            results = check_all_workers_health(db, [session])

        assert session.name in results["auto_reconnected"]
        mock_reconnect.assert_called_once()

    @patch(f"{_HEALTH}.check_and_update_worker_health")
    @patch(f"{_HEALTH}.repo")
    def test_disconnected_no_auto_reconnect_alive_recovers(
        self,
        mock_repo,
        mock_health,
    ):
        """Disconnected + auto_reconnect=False + health alive → appears in alive list."""
        session = Session(
            id=SESSION_ID,
            name="worker-ld3",
            host="localhost",
            work_dir="/tmp",
            status="disconnected",
            auto_reconnect=False,
        )
        mock_health.return_value = {
            "alive": True,
            "status": "waiting",
            "reason": "Claude running",
        }
        db = MagicMock()

        with patch("orchestrator.session.reconnect.trigger_reconnect") as mock_reconnect:
            results = check_all_workers_health(db, [session])

        assert session.name in results["alive"]
        assert session.name not in results["disconnected"]
        mock_reconnect.assert_not_called()

    @patch(f"{_HEALTH}.check_and_update_worker_health")
    @patch(f"{_HEALTH}.repo")
    def test_disconnected_no_auto_reconnect_dead_stays_disconnected(
        self,
        mock_repo,
        mock_health,
    ):
        """Disconnected + auto_reconnect=False + health dead → stays in disconnected list."""
        session = Session(
            id=SESSION_ID,
            name="worker-ld3",
            host="localhost",
            work_dir="/tmp",
            status="disconnected",
            auto_reconnect=False,
        )
        mock_health.return_value = {
            "alive": False,
            "status": "disconnected",
            "reason": "No Claude process",
            "needs_reconnect": True,
        }
        db = MagicMock()

        with patch("orchestrator.session.reconnect.trigger_reconnect") as mock_reconnect:
            results = check_all_workers_health(db, [session])

        assert session.name in results["disconnected"]
        assert session.name not in results["alive"]
        mock_reconnect.assert_not_called()

    @patch(f"{_HEALTH}.is_user_active", return_value=False)
    @patch(f"{_HEALTH}.check_and_update_worker_health")
    @patch(f"{_HEALTH}.repo")
    def test_disconnected_health_check_exception_falls_through_to_reconnect(
        self,
        mock_repo,
        mock_health,
        mock_active,
    ):
        """If health pre-check raises, still falls through to reconnect."""
        session = Session(
            id=SESSION_ID,
            name="worker-ld3",
            host="localhost",
            work_dir="/tmp",
            status="disconnected",
            auto_reconnect=True,
        )
        mock_health.side_effect = Exception("tmux error")
        db = MagicMock()

        with patch("orchestrator.session.reconnect.trigger_reconnect") as mock_reconnect:
            results = check_all_workers_health(db, [session])

        assert session.name in results["auto_reconnected"]
        mock_reconnect.assert_called_once()


class TestAutoReconnectDeferral(unittest.TestCase):
    """Tests for deferring auto-reconnect when user is actively typing."""

    def _make_detached_session(self, **overrides):
        defaults = {
            "id": SESSION_ID,
            "name": "worker-ld3",
            "host": HOST,
            "work_dir": "/tmp/work",
            "status": "screen_detached",
            "auto_reconnect": True,
        }
        defaults.update(overrides)
        return Session(**defaults)

    @patch(f"{_HEALTH}.is_user_active", return_value=True)
    @patch(f"{_HEALTH}.check_and_update_worker_health")
    @patch(f"{_HEALTH}.repo")
    def test_defers_reconnect_when_user_active(
        self,
        mock_repo,
        mock_health,
        mock_active,
    ):
        """User active → reconnect skipped, session appears in deferred list."""
        session = self._make_detached_session()
        mock_health.return_value = {
            "alive": False,
            "status": "screen_detached",
            "reason": "detached",
            "needs_reconnect": True,
        }
        db = MagicMock()

        with patch("orchestrator.session.reconnect.trigger_reconnect") as mock_reconnect:
            results = check_all_workers_health(db, [session])

        assert session.name in results["deferred"]
        assert session.name not in results["auto_reconnected"]
        mock_reconnect.assert_not_called()
        mock_active.assert_called_once_with(session.id)

    @patch(f"{_HEALTH}.is_user_active", return_value=False)
    @patch(f"{_HEALTH}.check_and_update_worker_health")
    @patch(f"{_HEALTH}.repo")
    def test_proceeds_with_reconnect_when_user_inactive(
        self,
        mock_repo,
        mock_health,
        mock_active,
    ):
        """User not active → reconnect fires normally."""
        session = self._make_detached_session()
        mock_health.return_value = {
            "alive": False,
            "status": "screen_detached",
            "reason": "detached",
            "needs_reconnect": True,
        }
        db = MagicMock()

        with patch("orchestrator.session.reconnect.trigger_reconnect") as mock_reconnect:
            results = check_all_workers_health(db, [session])

        assert session.name in results["auto_reconnected"]
        assert session.name not in results["deferred"]
        mock_reconnect.assert_called_once()
        mock_active.assert_called_once_with(session.id)
