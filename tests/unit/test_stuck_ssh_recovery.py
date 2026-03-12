"""Tests for stuck SSH recovery: _verify_pane_responsive, escalation in
_clean_pane_for_ssh, and retry-with-kill in reconnect Step 3 and setup.

All tmux/subprocess calls are mocked — no live tmux session is needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from orchestrator.session.reconnect import (
    _clean_pane_for_ssh,
    _verify_pane_responsive,
)

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
# _verify_pane_responsive
# ---------------------------------------------------------------------------


class TestVerifyPaneResponsive:
    """Test the marker-based pane responsiveness check."""

    def test_returns_true_when_marker_appears(self):
        """Pane responds with the marker → returns True."""
        captured_cmd = {}

        def fake_send_keys(sess, win, text, enter=True):
            # Extract the marker from the command being sent
            captured_cmd["text"] = text
            return True

        def fake_capture(sess, win, lines=15):
            # Build output that contains the markers from the actual command
            text = captured_cmd.get("text", "")
            # The full_command looks like:
            # echo __PANE_CHK_START_XXXXX__ && echo OK && echo __PANE_CHK_END_XXXXX__
            # Extract start/end markers
            import re

            m = re.search(r"(__PANE_CHK_START_\d+__)", text)
            if m:
                start = m.group(1)
                end = start.replace("START", "END")
                return f"{start}\nOK\n{end}\n$ "
            return "$ "

        with (
            patch("orchestrator.session.reconnect.send_keys", side_effect=fake_send_keys),
            patch("orchestrator.session.reconnect.capture_output", side_effect=fake_capture),
        ):
            result = _verify_pane_responsive("orch", "w1", timeout=3.0, poll_interval=0.1)

        assert result is True

    def test_returns_false_when_pane_is_stuck(self):
        """Pane never echoes the marker (stuck process) → returns False."""
        with (
            patch("orchestrator.session.reconnect.send_keys"),
            patch("orchestrator.session.reconnect.capture_output") as mock_cap,
        ):
            # Output shows the stuck rdev ssh — marker never appears
            mock_cap.return_value = "Starting ssh connection to user/rdev-vm...\n"

            result = _verify_pane_responsive("orch", "w1", timeout=1.0, poll_interval=0.2)

        assert result is False

    def test_polls_multiple_times_before_success(self):
        """Marker appears on the second poll attempt."""
        captured_cmd = {}
        call_count = {"n": 0}

        def fake_send_keys(sess, win, text, enter=True):
            captured_cmd["text"] = text
            return True

        def fake_capture(sess, win, lines=15):
            import re

            call_count["n"] += 1
            if call_count["n"] < 2:
                return "Starting ssh connection to ...\n"
            # Second poll: include marker output
            text = captured_cmd.get("text", "")
            m = re.search(r"(__PANE_CHK_START_\d+__)", text)
            if m:
                start = m.group(1)
                end = start.replace("START", "END")
                return f"{start}\nOK\n{end}\n$ "
            return "$ "

        with (
            patch("orchestrator.session.reconnect.send_keys", side_effect=fake_send_keys),
            patch("orchestrator.session.reconnect.capture_output", side_effect=fake_capture),
        ):
            result = _verify_pane_responsive("orch", "w1", timeout=3.0, poll_interval=0.1)

        assert result is True
        assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# _clean_pane_for_ssh escalation
# ---------------------------------------------------------------------------


class TestCleanPaneEscalation:
    """Test that _clean_pane_for_ssh escalates to kill+recreate when pane is unresponsive."""

    def test_kills_pane_when_unresponsive_after_ctrlc(self):
        """Normal case: no TUI, Ctrl-C sent, but pane doesn't respond → kill+recreate."""
        with (
            patch(
                "orchestrator.session.reconnect.check_tui_running_in_pane",
                return_value=False,
            ),
            patch("orchestrator.session.reconnect.send_keys") as mock_sk,
            patch(
                "orchestrator.session.reconnect._verify_pane_responsive",
                return_value=False,
            ),
            patch("orchestrator.session.reconnect.kill_window") as mock_kill,
            patch(
                "orchestrator.terminal.manager.ensure_window",
            ) as mock_ensure,
        ):
            _clean_pane_for_ssh("orch", "w1", cwd="/tmp/work")

        # Ctrl-C + Enter sent first
        mock_sk.assert_any_call("orch", "w1", "C-c", enter=False)
        mock_sk.assert_any_call("orch", "w1", "", enter=True)

        # Then kill + recreate
        mock_kill.assert_called_once_with("orch", "w1")
        mock_ensure.assert_called_once_with("orch", "w1", cwd="/tmp/work")

    def test_does_not_kill_when_responsive(self):
        """Normal case: no TUI, Ctrl-C sent, pane responds → no kill."""
        with (
            patch(
                "orchestrator.session.reconnect.check_tui_running_in_pane",
                return_value=False,
            ),
            patch("orchestrator.session.reconnect.send_keys"),
            patch(
                "orchestrator.session.reconnect._verify_pane_responsive",
                return_value=True,
            ),
            patch("orchestrator.session.reconnect.kill_window") as mock_kill,
        ):
            _clean_pane_for_ssh("orch", "w1", cwd="/tmp/work")

        mock_kill.assert_not_called()

    def test_tui_case_still_works(self):
        """TUI stuck case: TUI survives Ctrl-C → kill+recreate (existing behavior)."""
        with (
            patch(
                "orchestrator.session.reconnect.check_tui_running_in_pane",
                return_value=True,  # Always stuck TUI
            ),
            patch("orchestrator.session.reconnect.send_keys"),
            patch("orchestrator.session.reconnect.kill_window") as mock_kill,
            patch(
                "orchestrator.terminal.manager.ensure_window",
            ) as mock_ensure,
        ):
            _clean_pane_for_ssh("orch", "w1", cwd="/tmp")

        # TUI path kills + recreates (never reaches _verify_pane_responsive)
        mock_kill.assert_called_once_with("orch", "w1")
        mock_ensure.assert_called_once_with("orch", "w1", cwd="/tmp")


# ---------------------------------------------------------------------------
# Reconnect Step 3: retry-with-kill on timeout
# ---------------------------------------------------------------------------


class TestReconnectStep3Retry:
    """Test that reconnect_remote_worker retries with kill+recreate when
    wait_for_prompt times out on the first attempt."""

    @patch("orchestrator.session.reconnect._launch_claude_in_screen")
    @patch("orchestrator.session.reconnect.check_screen_exists_via_tmux")
    @patch("orchestrator.session.reconnect._copy_configs_to_remote")
    @patch("orchestrator.session.reconnect._ensure_local_configs_exist")
    @patch("orchestrator.session.reconnect._clean_pane_for_ssh")
    @patch("orchestrator.session.reconnect._ensure_tunnel")
    @patch("orchestrator.session.reconnect.check_tui_running_in_pane", return_value=False)
    @patch("orchestrator.session.reconnect.ensure_rdev_node")
    @patch(
        "orchestrator.session.health.check_screen_and_claude_remote",
        return_value=("alive", "mocked"),
    )
    def test_retries_with_kill_on_first_timeout(
        self,
        mock_screen_claude,
        mock_node,
        mock_tui,
        mock_tunnel,
        mock_clean,
        mock_configs,
        mock_copy,
        mock_screen_check,
        mock_launch,
    ):
        """First wait_for_prompt fails → kill+recreate → retry succeeds."""
        from orchestrator.session.reconnect import reconnect_remote_worker

        session = _make_session()
        repo = MagicMock()
        conn = MagicMock()

        wait_results = iter([False, True])  # First fails, second succeeds

        mock_screen_check.return_value = (False, False, None)

        with (
            patch(
                "orchestrator.session.health.check_worker_ssh_alive",
                return_value=False,
            ),
            patch("orchestrator.terminal.ssh.remote_connect"),
            patch(
                "orchestrator.terminal.ssh.wait_for_prompt",
                side_effect=lambda *a, **kw: next(wait_results),
            ),
            patch("orchestrator.session.reconnect.kill_window") as mock_kill,
            patch("orchestrator.terminal.manager.ensure_window") as mock_ensure_win,
            patch(
                "orchestrator.terminal.session._install_screen_if_needed",
                return_value=True,
            ),
            patch("orchestrator.session.reconnect.safe_send_keys"),
            patch("orchestrator.session.reconnect._kill_orphaned_screen"),
            patch("orchestrator.session.reconnect.time"),
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

        # kill_window called during retry
        mock_kill.assert_called()
        # ensure_window called to recreate pane
        mock_ensure_win.assert_called()

    @patch("orchestrator.session.reconnect._clean_pane_for_ssh")
    @patch("orchestrator.session.reconnect._ensure_tunnel")
    @patch("orchestrator.session.reconnect.check_tui_running_in_pane", return_value=False)
    @patch(
        "orchestrator.session.health.check_screen_and_claude_remote",
        return_value=("alive", "mocked"),
    )
    def test_raises_after_both_attempts_fail(
        self,
        mock_screen_claude,
        mock_tui,
        mock_tunnel,
        mock_clean,
    ):
        """Both wait_for_prompt attempts fail → RuntimeError raised."""
        from orchestrator.session.reconnect import reconnect_remote_worker

        session = _make_session()
        repo = MagicMock()
        conn = MagicMock()

        with (
            patch(
                "orchestrator.session.health.check_worker_ssh_alive",
                return_value=False,
            ),
            patch("orchestrator.terminal.ssh.remote_connect"),
            patch(
                "orchestrator.terminal.ssh.wait_for_prompt",
                return_value=False,  # Always times out
            ),
            patch("orchestrator.session.reconnect.kill_window"),
            patch("orchestrator.terminal.manager.ensure_window"),
            patch("orchestrator.session.reconnect.time"),
        ):
            with pytest.raises(RuntimeError, match="after kill\\+recreate retry"):
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

        # Session status should be set to error
        repo.update_session.assert_called_with(conn, session.id, status="error")


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
