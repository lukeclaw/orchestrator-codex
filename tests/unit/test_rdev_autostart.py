"""Tests for rdev auto-start utilities (_get_rdev_state, _ensure_rdev_running).

Verifies that stopped rdev hosts are automatically restarted before SSH
connections during reconnect and initial setup.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# _get_rdev_state
# ---------------------------------------------------------------------------


class TestGetRdevState:
    def test_parses_running(self):
        from orchestrator.session.reconnect import _get_rdev_state

        mock_result = MagicMock()
        mock_result.stdout = (
            "Name  | my-mp/my-session\nState | Running\nHost  | some-host.example.com\n"
        )
        with patch("orchestrator.session.reconnect.subprocess.run", return_value=mock_result):
            assert _get_rdev_state("my-mp/my-session") == "RUNNING"

    def test_parses_stopped(self):
        from orchestrator.session.reconnect import _get_rdev_state

        mock_result = MagicMock()
        mock_result.stdout = "Name  | my-mp/my-session\nState | Stopped\n"
        with patch("orchestrator.session.reconnect.subprocess.run", return_value=mock_result):
            assert _get_rdev_state("my-mp/my-session") == "STOPPED"

    def test_parses_creating(self):
        from orchestrator.session.reconnect import _get_rdev_state

        mock_result = MagicMock()
        mock_result.stdout = "State | Creating\n"
        with patch("orchestrator.session.reconnect.subprocess.run", return_value=mock_result):
            assert _get_rdev_state("my-mp/my-session") == "CREATING"

    def test_returns_none_on_timeout(self):
        from orchestrator.session.reconnect import _get_rdev_state

        with patch(
            "orchestrator.session.reconnect.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="rdev", timeout=15),
        ):
            assert _get_rdev_state("my-mp/my-session") is None

    def test_returns_none_on_bad_output(self):
        from orchestrator.session.reconnect import _get_rdev_state

        mock_result = MagicMock()
        mock_result.stdout = "Error: not found\n"
        with patch("orchestrator.session.reconnect.subprocess.run", return_value=mock_result):
            assert _get_rdev_state("my-mp/my-session") is None

    def test_returns_none_on_no_rdev_binary(self):
        from orchestrator.session.reconnect import _get_rdev_state

        with patch(
            "orchestrator.session.reconnect.subprocess.run",
            side_effect=FileNotFoundError("rdev not found"),
        ):
            assert _get_rdev_state("my-mp/my-session") is None


# ---------------------------------------------------------------------------
# _ensure_rdev_running
# ---------------------------------------------------------------------------


class TestEnsureRdevRunning:
    def test_skips_non_rdev_host(self):
        from orchestrator.session.reconnect import _ensure_rdev_running

        # Non-rdev host (no slash) should always return True
        with patch("orchestrator.session.reconnect._get_rdev_state") as mock_state:
            result = _ensure_rdev_running("sess-1", "some-plain-host.example.com")
            assert result is True
            mock_state.assert_not_called()

    def test_skips_localhost(self):
        from orchestrator.session.reconnect import _ensure_rdev_running

        with patch("orchestrator.session.reconnect._get_rdev_state") as mock_state:
            result = _ensure_rdev_running("sess-1", "localhost")
            assert result is True
            mock_state.assert_not_called()

    def test_already_running(self):
        from orchestrator.session.reconnect import _ensure_rdev_running

        with patch("orchestrator.session.reconnect._get_rdev_state", return_value="RUNNING"):
            assert _ensure_rdev_running("sess-1", "my-mp/my-rdev") is True

    def test_state_none_proceeds_optimistically(self):
        from orchestrator.session.reconnect import _ensure_rdev_running

        with patch("orchestrator.session.reconnect._get_rdev_state", return_value=None):
            assert _ensure_rdev_running("sess-1", "my-mp/my-rdev") is True

    @patch("orchestrator.session.reconnect._invalidate_rdev_cache")
    @patch("orchestrator.session.reconnect._set_reconnect_step")
    @patch("orchestrator.session.reconnect.time.sleep")
    def test_restarts_stopped_host(self, mock_sleep, mock_step, mock_invalidate):
        from orchestrator.session.reconnect import _ensure_rdev_running

        mock_restart_result = MagicMock()
        mock_restart_result.returncode = 0

        with (
            patch(
                "orchestrator.session.reconnect._get_rdev_state",
                side_effect=["STOPPED", "RUNNING"],
            ),
            patch(
                "orchestrator.session.reconnect.subprocess.run",
                return_value=mock_restart_result,
            ) as mock_run,
        ):
            assert _ensure_rdev_running("sess-1", "my-mp/my-rdev") is True

        # Should broadcast rdev_start step
        mock_step.assert_called_with("sess-1", "rdev_start")
        # Should call rdev restart
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == ["rdev", "restart", "my-mp/my-rdev"]
        # Should invalidate cache
        mock_invalidate.assert_called_once()

    @patch("orchestrator.session.reconnect._set_reconnect_step")
    @patch("orchestrator.session.reconnect.time.sleep")
    def test_restart_failure_nonzero_exit(self, mock_sleep, mock_step):
        from orchestrator.session.reconnect import _ensure_rdev_running

        mock_restart_result = MagicMock()
        mock_restart_result.returncode = 1
        mock_restart_result.stderr = "rdev error"

        with (
            patch("orchestrator.session.reconnect._get_rdev_state", return_value="STOPPED"),
            patch(
                "orchestrator.session.reconnect.subprocess.run",
                return_value=mock_restart_result,
            ),
        ):
            assert _ensure_rdev_running("sess-1", "my-mp/my-rdev") is False

    @patch("orchestrator.session.reconnect._set_reconnect_step")
    @patch("orchestrator.session.reconnect.time.sleep")
    def test_restart_timeout(self, mock_sleep, mock_step):
        from orchestrator.session.reconnect import _ensure_rdev_running

        with (
            patch("orchestrator.session.reconnect._get_rdev_state", return_value="STOPPED"),
            patch(
                "orchestrator.session.reconnect.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="rdev", timeout=120),
            ),
        ):
            assert _ensure_rdev_running("sess-1", "my-mp/my-rdev") is False

    @patch("orchestrator.session.reconnect._invalidate_rdev_cache")
    @patch("orchestrator.session.reconnect._set_reconnect_step")
    @patch("orchestrator.session.reconnect.time.sleep")
    def test_waits_for_creating_state(self, mock_sleep, mock_step, mock_invalidate):
        from orchestrator.session.reconnect import _ensure_rdev_running

        with patch(
            "orchestrator.session.reconnect._get_rdev_state",
            side_effect=["CREATING", "CREATING", "RUNNING"],
        ):
            assert _ensure_rdev_running("sess-1", "my-mp/my-rdev") is True

        mock_step.assert_called_with("sess-1", "rdev_start")
        mock_invalidate.assert_called_once()

    @patch("orchestrator.session.reconnect._set_reconnect_step")
    @patch("orchestrator.session.reconnect.time.sleep")
    def test_returns_false_for_deleted(self, mock_sleep, mock_step):
        from orchestrator.session.reconnect import _ensure_rdev_running

        with patch("orchestrator.session.reconnect._get_rdev_state", return_value="DELETED"):
            assert _ensure_rdev_running("sess-1", "my-mp/my-rdev") is False

    @patch("orchestrator.session.reconnect._set_reconnect_step")
    @patch("orchestrator.session.reconnect.time.sleep")
    def test_returns_false_for_error(self, mock_sleep, mock_step):
        from orchestrator.session.reconnect import _ensure_rdev_running

        with patch("orchestrator.session.reconnect._get_rdev_state", return_value="ERROR"):
            assert _ensure_rdev_running("sess-1", "my-mp/my-rdev") is False

    @patch("orchestrator.session.reconnect._set_reconnect_step")
    @patch("orchestrator.session.reconnect.time.sleep")
    def test_stopping_triggers_restart(self, mock_sleep, mock_step):
        """STOPPING state should be treated like STOPPED — restart it."""
        from orchestrator.session.reconnect import _ensure_rdev_running

        mock_restart_result = MagicMock()
        mock_restart_result.returncode = 0

        with (
            patch(
                "orchestrator.session.reconnect._get_rdev_state",
                side_effect=["STOPPING", "RUNNING"],
            ),
            patch(
                "orchestrator.session.reconnect.subprocess.run",
                return_value=mock_restart_result,
            ),
            patch("orchestrator.session.reconnect._invalidate_rdev_cache"),
        ):
            assert _ensure_rdev_running("sess-1", "my-mp/my-rdev") is True

    @patch("orchestrator.session.reconnect._set_reconnect_step")
    @patch("orchestrator.session.reconnect.time.sleep")
    def test_poll_timeout_after_restart(self, mock_sleep, mock_step):
        """If host doesn't reach RUNNING after restart within poll deadline, return False."""
        from orchestrator.session.reconnect import _ensure_rdev_running

        mock_restart_result = MagicMock()
        mock_restart_result.returncode = 0

        # time.time() returns increasing values that exceed the 60s deadline
        time_values = [100.0, 100.0, 170.0]  # start=100, poll at 170 > 100+60

        with (
            patch(
                "orchestrator.session.reconnect._get_rdev_state",
                side_effect=["STOPPED", "STARTING"],
            ),
            patch(
                "orchestrator.session.reconnect.subprocess.run",
                return_value=mock_restart_result,
            ),
            patch("orchestrator.session.reconnect.time.time", side_effect=time_values),
        ):
            assert _ensure_rdev_running("sess-1", "my-mp/my-rdev") is False


# ---------------------------------------------------------------------------
# Integration: reconnect_remote_worker with auto-start
# ---------------------------------------------------------------------------


def _make_session(**overrides):
    """Create a minimal mock session object."""
    defaults = {
        "id": "sess-rdev",
        "name": "worker-rdev",
        "host": "my-mp/my-rdev",
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


class TestReconnectWithAutoStart:
    @patch("orchestrator.session.reconnect._copy_configs_to_remote")
    @patch("orchestrator.session.reconnect._ensure_local_configs_exist")
    @patch("orchestrator.session.reconnect.subprocess")
    def test_stopped_host_autostart_then_reconnect(
        self,
        mock_subprocess,
        mock_configs,
        mock_copy,
    ):
        """Stopped host: auto-start succeeds, then normal reconnect proceeds."""
        from orchestrator.session.reconnect import reconnect_remote_worker

        session = _make_session()
        repo = MagicMock()
        conn = MagicMock()

        mock_rws = MagicMock()
        mock_rws.create_pty.return_value = "pty-test-456"
        mock_rws.execute.side_effect = [
            {"ptys": []},
            {"ptys": [{"pty_id": "pty-test-456", "alive": True}]},
        ]

        with (
            patch(
                "orchestrator.session.reconnect._ensure_rdev_running",
                return_value=True,
            ) as mock_ensure,
            patch("orchestrator.terminal.session._ensure_rws_ready", return_value=mock_rws),
            patch("orchestrator.session.reconnect._ensure_tunnel"),
            patch("orchestrator.session.reconnect._reconnect_rws_for_host"),
            patch(
                "orchestrator.session.reconnect._check_claude_session_exists_remote",
                return_value=False,
            ),
            patch(
                "orchestrator.terminal.session._build_claude_command",
                return_value="claude --session-id sess-rdev",
            ),
            patch("orchestrator.session.reconnect.time.sleep"),
        ):
            reconnect_remote_worker(
                conn,
                session,
                "orch",
                "w1",
                8093,
                "/tmp/orchestrator/workers/worker-rdev",
                repo,
                tunnel_manager=MagicMock(is_alive=MagicMock(return_value=False)),
            )

        mock_ensure.assert_called_once_with(session.id, session.host)
        mock_rws.create_pty.assert_called_once()

    @patch("orchestrator.session.reconnect.subprocess")
    def test_stopped_host_autostart_fails_sets_disconnected(
        self,
        mock_subprocess,
    ):
        """Stopped host: auto-start fails, session set to disconnected."""
        from orchestrator.session.reconnect import reconnect_remote_worker

        session = _make_session()
        repo = MagicMock()
        conn = MagicMock()

        with (
            patch(
                "orchestrator.session.reconnect._ensure_rdev_running",
                return_value=False,
            ),
            patch("orchestrator.session.reconnect.time.sleep"),
        ):
            reconnect_remote_worker(
                conn,
                session,
                "orch",
                "w1",
                8093,
                "/tmp/orchestrator/workers/worker-rdev",
                repo,
                tunnel_manager=MagicMock(),
            )

        # Should set status to disconnected
        repo.update_session.assert_any_call(conn, session.id, status="disconnected")


# ---------------------------------------------------------------------------
# Integration: setup_remote_worker with auto-start
# ---------------------------------------------------------------------------


class TestSetupWithAutoStart:
    @patch("orchestrator.session.reconnect._ensure_rdev_running", return_value=False)
    def test_setup_fails_when_rdev_stopped(self, mock_ensure, db):
        """setup_remote_worker returns error if rdev host can't be started."""
        from orchestrator.terminal.session import setup_remote_worker
        from scripts.seed_db import seed_all

        seed_all(db)

        result = setup_remote_worker(
            db,
            "session-id-789",
            "w1",
            "my-mp/my-rdev",
            "orchestrator",
            8093,
            tunnel_manager=MagicMock(),
        )

        assert result["ok"] is False
        assert "stopped" in result["error"].lower() or "started" in result["error"].lower()
