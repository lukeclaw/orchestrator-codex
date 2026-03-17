"""Unit tests for migrate_legacy_screen_sessions()."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest


def _make_session(
    session_id="sess-1",
    name="worker-1",
    host="user/rdev-vm",
    status="working",
    rws_pty_id=None,
):
    s = MagicMock()
    s.id = session_id
    s.name = name
    s.host = host
    s.status = status
    s.rws_pty_id = rws_pty_id
    return s


class TestMigrateLegacyScreenSessions:
    """Test the startup migration hook."""

    @patch("subprocess.run")
    @patch("orchestrator.core.lifecycle.sessions")
    @patch("orchestrator.terminal.ssh.is_remote_host")
    def test_migrates_legacy_remote_sessions(self, mock_is_remote, mock_sessions, mock_run):
        """Remote + no rws_pty_id + working -> SSH kill + status=disconnected."""
        from orchestrator.core.lifecycle import migrate_legacy_screen_sessions

        session = _make_session(status="working", rws_pty_id=None)
        mock_sessions.list_sessions.return_value = [session]
        mock_is_remote.return_value = True
        conn = MagicMock()

        migrate_legacy_screen_sessions(conn)

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert "ssh" in call_args[0][0]
        assert "screen" in call_args[0][0][-1]
        mock_sessions.update_session.assert_called_once_with(conn, "sess-1", status="disconnected")

    @patch("subprocess.run")
    @patch("orchestrator.core.lifecycle.sessions")
    @patch("orchestrator.terminal.ssh.is_remote_host")
    def test_skips_local_sessions(self, mock_is_remote, mock_sessions, mock_run):
        """host=localhost -> no SSH, unchanged."""
        from orchestrator.core.lifecycle import migrate_legacy_screen_sessions

        session = _make_session(host="localhost", status="working", rws_pty_id=None)
        mock_sessions.list_sessions.return_value = [session]
        mock_is_remote.return_value = False
        conn = MagicMock()

        migrate_legacy_screen_sessions(conn)

        mock_run.assert_not_called()
        mock_sessions.update_session.assert_not_called()

    @patch("subprocess.run")
    @patch("orchestrator.core.lifecycle.sessions")
    @patch("orchestrator.terminal.ssh.is_remote_host")
    def test_skips_rws_pty_sessions(self, mock_is_remote, mock_sessions, mock_run):
        """rws_pty_id set -> no SSH."""
        from orchestrator.core.lifecycle import migrate_legacy_screen_sessions

        session = _make_session(status="working", rws_pty_id="pty-123")
        mock_sessions.list_sessions.return_value = [session]
        mock_is_remote.return_value = True
        conn = MagicMock()

        migrate_legacy_screen_sessions(conn)

        mock_run.assert_not_called()
        mock_sessions.update_session.assert_not_called()

    @patch("subprocess.run")
    @patch("orchestrator.core.lifecycle.sessions")
    @patch("orchestrator.terminal.ssh.is_remote_host")
    def test_skips_idle_sessions(self, mock_is_remote, mock_sessions, mock_run):
        """status=idle -> no SSH."""
        from orchestrator.core.lifecycle import migrate_legacy_screen_sessions

        session = _make_session(status="idle", rws_pty_id=None)
        mock_sessions.list_sessions.return_value = [session]
        mock_is_remote.return_value = True
        conn = MagicMock()

        migrate_legacy_screen_sessions(conn)

        mock_run.assert_not_called()
        mock_sessions.update_session.assert_not_called()

    @pytest.mark.parametrize(
        "status", ["working", "waiting", "screen_detached", "connecting", "error"]
    )
    @patch("subprocess.run")
    @patch("orchestrator.core.lifecycle.sessions")
    @patch("orchestrator.terminal.ssh.is_remote_host")
    def test_migrates_all_active_legacy_statuses(
        self, mock_is_remote, mock_sessions, mock_run, status
    ):
        """Any active legacy status (not idle/disconnected) should trigger migration."""
        from orchestrator.core.lifecycle import migrate_legacy_screen_sessions

        session = _make_session(status=status, rws_pty_id=None)
        mock_sessions.list_sessions.return_value = [session]
        mock_is_remote.return_value = True
        conn = MagicMock()

        migrate_legacy_screen_sessions(conn)

        mock_run.assert_called_once()
        mock_sessions.update_session.assert_called_once_with(conn, "sess-1", status="disconnected")

    @patch("subprocess.run")
    @patch("orchestrator.core.lifecycle.sessions")
    @patch("orchestrator.terminal.ssh.is_remote_host")
    def test_skips_disconnected_sessions(self, mock_is_remote, mock_sessions, mock_run):
        """status=disconnected + no rws_pty_id -> skip (not legacy, just cleared on disconnect)."""
        from orchestrator.core.lifecycle import migrate_legacy_screen_sessions

        session = _make_session(status="disconnected", rws_pty_id=None)
        mock_sessions.list_sessions.return_value = [session]
        mock_is_remote.return_value = True
        conn = MagicMock()

        migrate_legacy_screen_sessions(conn)

        mock_run.assert_not_called()
        mock_sessions.update_session.assert_not_called()

    @patch("subprocess.run")
    @patch("orchestrator.core.lifecycle.sessions")
    @patch("orchestrator.terminal.ssh.is_remote_host")
    def test_ssh_failure_best_effort(self, mock_is_remote, mock_sessions, mock_run):
        """TimeoutExpired -> no exception, status still disconnected."""
        from orchestrator.core.lifecycle import migrate_legacy_screen_sessions

        session = _make_session(status="working", rws_pty_id=None)
        mock_sessions.list_sessions.return_value = [session]
        mock_is_remote.return_value = True
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=10)
        conn = MagicMock()

        # Should not raise
        migrate_legacy_screen_sessions(conn)

        mock_sessions.update_session.assert_called_once_with(conn, "sess-1", status="disconnected")

    @patch("subprocess.run")
    @patch("orchestrator.core.lifecycle.sessions")
    @patch("orchestrator.terminal.ssh.is_remote_host")
    def test_no_legacy_sessions_noop(self, mock_is_remote, mock_sessions, mock_run):
        """Empty -> no calls."""
        from orchestrator.core.lifecycle import migrate_legacy_screen_sessions

        mock_sessions.list_sessions.return_value = []
        conn = MagicMock()

        migrate_legacy_screen_sessions(conn)

        mock_run.assert_not_called()
        mock_sessions.update_session.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
