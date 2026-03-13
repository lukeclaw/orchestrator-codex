"""Tests for remote worker reconnect via RWS PTY and setup retry logic.

All tmux/subprocess calls are mocked -- no live tmux session is needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(**overrides):
    """Create a minimal mock session object."""
    defaults = {
        "id": "sess-stuck",
        "name": "worker-stuck",
        "host": "user/rdev-vm",
        "status": "disconnected",
        "work_dir": "/tmp/work",
        "claude_session_id": None,
        "auto_reconnect": False,
        "rws_pty_id": None,
    }
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# Reconnect via RWS PTY
# ---------------------------------------------------------------------------


class TestReconnectStep3Retry:
    """Test that reconnect_remote_worker creates an RWS PTY successfully
    or sets status to error when _ensure_rws_ready fails."""

    @patch("orchestrator.session.reconnect._copy_configs_to_remote")
    @patch("orchestrator.session.reconnect._ensure_local_configs_exist")
    @patch("orchestrator.session.reconnect.subprocess")
    def test_creates_rws_pty_successfully(
        self,
        mock_reconnect_subprocess,
        mock_configs,
        mock_copy,
    ):
        """Reconnect creates an RWS PTY when _ensure_rws_ready succeeds."""
        from orchestrator.session.reconnect import reconnect_remote_worker

        session = _make_session()
        repo = MagicMock()
        conn = MagicMock()

        mock_rws = MagicMock()
        mock_rws.create_pty.return_value = "pty-test-123"

        with (
            patch(
                "orchestrator.terminal.session._ensure_rws_ready",
                return_value=mock_rws,
            ),
            patch(
                "orchestrator.session.reconnect._ensure_tunnel",
            ),
            patch(
                "orchestrator.session.reconnect._reconnect_rws_for_host",
            ),
            patch(
                "orchestrator.session.reconnect._check_claude_session_exists_remote",
                return_value=False,
            ),
            patch(
                "orchestrator.terminal.session._build_claude_command",
                return_value="claude --session-id sess-stuck",
            ),
            patch("orchestrator.session.reconnect.time.sleep"),
        ):
            reconnect_remote_worker(
                conn,
                session,
                "orch",
                "w1",
                8093,
                "/tmp/orchestrator/workers/worker-stuck",
                repo,
                tunnel_manager=MagicMock(is_alive=MagicMock(return_value=False)),
            )

        # Should have created a PTY
        mock_rws.create_pty.assert_called_once()

        # Should have updated session with pty_id and status
        update_calls = repo.update_session.call_args_list
        pty_update = [c for c in update_calls if "rws_pty_id" in str(c)]
        assert len(pty_update) >= 1, f"Should update rws_pty_id, got: {update_calls}"

    @patch("orchestrator.session.reconnect.subprocess")
    def test_sets_error_when_rws_fails(
        self,
        mock_reconnect_subprocess,
    ):
        """Reconnect sets status to error when _ensure_rws_ready fails."""
        from orchestrator.session.reconnect import reconnect_remote_worker

        session = _make_session()
        repo = MagicMock()
        conn = MagicMock()

        with (
            patch(
                "orchestrator.terminal.session._ensure_rws_ready",
                side_effect=RuntimeError("RWS not available"),
            ),
            patch("orchestrator.session.reconnect.time.sleep"),
            pytest.raises(RuntimeError, match="RWS not available"),
        ):
            reconnect_remote_worker(
                conn,
                session,
                "orch",
                "w1",
                8093,
                "/tmp/orchestrator/workers/worker-stuck",
                repo,
                tunnel_manager=None,
            )

        # Session status should be set to disconnected
        repo.update_session.assert_called_with(conn, session.id, status="disconnected")


# ---------------------------------------------------------------------------
# setup_remote_worker: retry-with-kill on timeout
# ---------------------------------------------------------------------------


class TestSetupRemoteWorkerRetry:
    """Test setup_remote_worker with the new RWS PTY architecture.

    setup_remote_worker now uses SSH subprocess calls, deploy_worker_tmp_contents,
    _copy_dir_to_remote_ssh, and RWS PTY creation instead of the old
    remote_connect/wait_for_prompt/screen flow.
    """

    def test_setup_succeeds_with_rws_pty(self):
        """Full RWS PTY setup path succeeds when all dependencies are mocked."""
        from orchestrator.terminal.session import setup_remote_worker

        conn = MagicMock()
        mock_rws = MagicMock()
        mock_rws.create_pty.return_value = "pty-123"
        mock_rws.execute.return_value = {"ptys": [{"pty_id": "pty-123", "alive": True}]}

        with (
            patch("orchestrator.terminal.session._copy_dir_to_remote_ssh", return_value=True),
            patch(
                "orchestrator.agents.deploy.deploy_worker_tmp_contents",
                return_value=["bin/lib.sh"],
            ),
            patch("orchestrator.terminal.session.subprocess.run"),
            patch("orchestrator.terminal.session._ensure_rws_ready", return_value=mock_rws),
            patch("orchestrator.terminal.session.time"),
            patch("orchestrator.terminal.session.is_rdev_host", return_value=False),
            patch("orchestrator.state.repositories.sessions.update_session"),
        ):
            result = setup_remote_worker(
                conn,
                "sess-setup",
                "worker-setup",
                "generic-host",
                api_port=8093,
            )

        assert result["ok"] is True
        mock_rws.create_pty.assert_called_once()

    def test_returns_error_when_ssh_copy_fails(self):
        """setup_remote_worker returns error when SSH copy fails."""
        from orchestrator.terminal.session import setup_remote_worker

        conn = MagicMock()

        with (
            patch("orchestrator.terminal.session._copy_dir_to_remote_ssh", return_value=False),
            patch(
                "orchestrator.agents.deploy.deploy_worker_tmp_contents",
                return_value=["bin/lib.sh"],
            ),
            patch("orchestrator.terminal.session.subprocess.run"),
            patch("orchestrator.terminal.session.time"),
            patch("orchestrator.terminal.session.is_rdev_host", return_value=False),
        ):
            result = setup_remote_worker(
                conn,
                "sess-setup-fail",
                "worker-fail",
                "generic-host",
                api_port=8093,
            )

        assert result["ok"] is False
        assert "Failed to copy files" in result["error"]
