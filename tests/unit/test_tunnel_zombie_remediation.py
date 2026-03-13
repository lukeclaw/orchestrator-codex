"""Tests for zombie port auto-remediation in ReverseTunnelManager.

When start_tunnel() detects 'remote port forwarding failed', it should:
1. SSH to remote and kill the zombie process holding the port
2. Retry the tunnel start once
3. Return None if the retry also fails (no infinite loop)

The full remediation → retry flow is tested by
test_fails_on_remote_forward_failure in test_tunnel.py.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.session.tunnel import ReverseTunnelManager


def _make_manager(tmp_path):
    return ReverseTunnelManager(api_port=8093, log_dir=str(tmp_path))


@pytest.mark.allow_subprocess
class TestKillRemotePortHolder:
    """Unit tests for _kill_remote_port_holder."""

    @patch("orchestrator.session.tunnel.subprocess.run")
    def test_success(self, mock_run, tmp_path):
        """Should SSH and run fuser -k."""
        mgr = _make_manager(tmp_path)
        mock_run.return_value = MagicMock(returncode=0)

        result = mgr._kill_remote_port_holder("user/vm", 8093, "worker-1")

        assert result is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "ssh" in cmd
        assert "user/vm" in cmd
        assert "fuser -k 8093/tcp" in cmd[-1]

    @patch("orchestrator.session.tunnel.subprocess.run")
    def test_timeout(self, mock_run, tmp_path):
        """SSH timeout should be handled gracefully."""
        mgr = _make_manager(tmp_path)
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=15)

        result = mgr._kill_remote_port_holder("user/vm", 8093, "worker-1")

        assert result is False

    @patch("orchestrator.session.tunnel.subprocess.run")
    def test_ssh_error(self, mock_run, tmp_path):
        """SSH failure should be handled gracefully."""
        mgr = _make_manager(tmp_path)
        mock_run.side_effect = OSError("No route to host")

        result = mgr._kill_remote_port_holder("user/vm", 8093, "worker-1")

        assert result is False


@pytest.mark.allow_subprocess
class TestStartTunnelRetryParameter:
    """Test that start_tunnel accepts _is_retry keyword argument."""

    def test_retry_parameter_accepted(self, tmp_path):
        """start_tunnel(_is_retry=True) runs without error."""
        mgr = _make_manager(tmp_path)

        proc = MagicMock()
        proc.pid = 999
        proc.poll.return_value = None

        with patch("orchestrator.session.tunnel.subprocess.Popen", return_value=proc):
            result = mgr.start_tunnel("s1", "worker-1", "user/vm", _is_retry=True)

        # Process alive, no log failure → success
        assert result == 999
