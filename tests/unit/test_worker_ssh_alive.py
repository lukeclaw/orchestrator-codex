"""Tests for pane-scoped SSH alive check in orchestrator.session.health."""

import unittest
from unittest.mock import MagicMock, patch

from orchestrator.session.health import (
    _get_pane_pid,
    _has_ssh_in_process_tree,
    check_worker_ssh_alive,
)


class TestGetPanePid(unittest.TestCase):
    """Tests for _get_pane_pid."""

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_pid_on_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="93147\n")
        assert _get_pane_pid("orchestrator", "worker-a") == 93147

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_none_on_bad_exit(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _get_pane_pid("orchestrator", "no-such-win") is None

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_none_on_non_numeric(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not-a-pid\n")
        assert _get_pane_pid("orchestrator", "worker-a") is None

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_none_on_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="tmux", timeout=5)
        assert _get_pane_pid("orchestrator", "worker-a") is None


class TestHasSshInProcessTree(unittest.TestCase):
    """Tests for _has_ssh_in_process_tree."""

    PS_HEADER = "  PID  PPID COMM\n"

    def _mock_ps(self, lines: str):
        """Create a mock for subprocess.run returning ps output."""
        mock = MagicMock(returncode=0, stdout=self.PS_HEADER + lines)
        return mock

    @patch("orchestrator.session.health.subprocess.run")
    def test_connected_worker_has_ssh(self, mock_run):
        """shell(100) → rdev/python(200) → ssh(300) — should find ssh."""
        ps_output = (
            "  100     1 -zsh\n"
            "  200   100 python3.12\n"
            "  300   200 ssh\n"
        )
        mock_run.return_value = self._mock_ps(ps_output)
        assert _has_ssh_in_process_tree(100) is True

    @patch("orchestrator.session.health.subprocess.run")
    def test_disconnected_worker_no_ssh(self, mock_run):
        """shell(100) only — rdev and ssh have exited."""
        ps_output = "  100     1 -zsh\n"
        mock_run.return_value = self._mock_ps(ps_output)
        assert _has_ssh_in_process_tree(100) is False

    @patch("orchestrator.session.health.subprocess.run")
    def test_rdev_alive_but_ssh_exited(self, mock_run):
        """shell(100) → rdev/python(200) — ssh child gone (lingering rdev)."""
        ps_output = (
            "  100     1 -zsh\n"
            "  200   100 python3.12\n"
        )
        mock_run.return_value = self._mock_ps(ps_output)
        assert _has_ssh_in_process_tree(100) is False

    @patch("orchestrator.session.health.subprocess.run")
    def test_other_panes_ssh_not_matched(self, mock_run):
        """Another pane (PID 500) has ssh — should NOT match for pane 100."""
        ps_output = (
            "  100     1 -zsh\n"
            "  500     1 -zsh\n"
            "  600   500 python3.12\n"
            "  700   600 ssh\n"
        )
        mock_run.return_value = self._mock_ps(ps_output)
        assert _has_ssh_in_process_tree(100) is False

    @patch("orchestrator.session.health.subprocess.run")
    def test_deep_nesting(self, mock_run):
        """shell → rdev → wrapper → ssh — should still find ssh."""
        ps_output = (
            "  100     1 -zsh\n"
            "  200   100 python3.12\n"
            "  300   200 bash\n"
            "  400   300 ssh\n"
        )
        mock_run.return_value = self._mock_ps(ps_output)
        assert _has_ssh_in_process_tree(100) is True

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_false_on_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ps", timeout=5)
        assert _has_ssh_in_process_tree(100) is False


class TestCheckWorkerSshAlive(unittest.TestCase):
    """Integration tests for the full check_worker_ssh_alive flow."""

    @patch("orchestrator.session.health._has_ssh_in_process_tree", return_value=True)
    @patch("orchestrator.session.health._get_pane_pid", return_value=93147)
    def test_alive_when_ssh_in_tree(self, mock_pid, mock_ssh):
        assert check_worker_ssh_alive("orch", "worker-a", "subs-mt/sleepy-franklin") is True
        mock_pid.assert_called_once_with("orch", "worker-a")
        mock_ssh.assert_called_once_with(93147)

    @patch("orchestrator.session.health._has_ssh_in_process_tree", return_value=False)
    @patch("orchestrator.session.health._get_pane_pid", return_value=93147)
    def test_dead_when_no_ssh_in_tree(self, mock_pid, mock_ssh):
        assert check_worker_ssh_alive("orch", "worker-a", "subs-mt/sleepy-franklin") is False

    @patch("orchestrator.session.health._get_pane_pid", return_value=None)
    def test_dead_when_pane_not_found(self, mock_pid):
        assert check_worker_ssh_alive("orch", "no-window", "subs-mt/sleepy-franklin") is False

    @patch("orchestrator.session.health._has_ssh_in_process_tree", return_value=False)
    @patch("orchestrator.session.health._get_pane_pid", return_value=100)
    def test_edge_case_other_terminal_has_same_host(self, mock_pid, mock_ssh):
        """Even if another terminal has rdev ssh to the same host,
        only the pane's own process tree matters."""
        assert check_worker_ssh_alive("orch", "worker-a", "subs-mt/sleepy-franklin") is False


if __name__ == "__main__":
    unittest.main()
