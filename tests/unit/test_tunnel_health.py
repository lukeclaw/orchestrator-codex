"""Tests for tunnel health probing, kill escalation, and periodic monitoring.

Covers:
- Active tunnel probing (probe_tunnel_connectivity)
- kill_tunnel_processes SIGKILL escalation
- find_tunnel_pids process discovery
- Periodic tunnel health monitor loop (subprocess-based)
- Reconnect backoff (exponential delay, no hard limit)
"""

import signal
import time
from unittest.mock import MagicMock, call, patch

import pytest

from orchestrator.session import tunnel
from orchestrator.session.health import _ReconnectBackoff


@pytest.fixture(autouse=True)
def reset_tunnel_cache():
    """Reset tunnel module global cache before and after each test."""
    tunnel._tunnel_cache = {}
    tunnel._cache_timestamp = 0
    yield
    tunnel._tunnel_cache = {}
    tunnel._cache_timestamp = 0


# ==============================================================================
# probe_tunnel_connectivity
# ==============================================================================


class TestProbeTunnelConnectivity:
    """Tests for the active tunnel probe that SSHes to remote and curls the API."""

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_true_on_http_200(self, mock_run):
        """Should return True when curl returns HTTP 200."""
        from orchestrator.session.health import probe_tunnel_connectivity

        mock_run.return_value = MagicMock(stdout="200", stderr="", returncode=0)

        assert probe_tunnel_connectivity("user/rdev-vm") is True
        mock_run.assert_called_once()
        # Verify SSH command structure
        args = mock_run.call_args[0][0]
        assert args[0] == "ssh"
        assert "user/rdev-vm" in args
        # BatchMode=yes should appear somewhere in the args
        full_cmd = " ".join(args)
        assert "BatchMode=yes" in full_cmd

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_false_on_http_error(self, mock_run):
        """Should return False when curl returns non-200 status."""
        from orchestrator.session.health import probe_tunnel_connectivity

        mock_run.return_value = MagicMock(stdout="000", stderr="Connection refused", returncode=0)

        assert probe_tunnel_connectivity("user/rdev-vm") is False

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_false_on_timeout(self, mock_run):
        """Should return False when SSH+curl times out."""
        import subprocess

        from orchestrator.session.health import probe_tunnel_connectivity

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=8)

        assert probe_tunnel_connectivity("user/rdev-vm") is False

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_false_on_ssh_failure(self, mock_run):
        """Should return False when SSH itself fails."""
        from orchestrator.session.health import probe_tunnel_connectivity

        mock_run.return_value = MagicMock(stdout="", stderr="Connection refused", returncode=255)

        assert probe_tunnel_connectivity("user/rdev-vm") is False

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_false_on_exception(self, mock_run):
        """Should return False on unexpected exceptions."""
        from orchestrator.session.health import probe_tunnel_connectivity

        mock_run.side_effect = OSError("Network error")

        assert probe_tunnel_connectivity("user/rdev-vm") is False

    @patch("orchestrator.session.health.subprocess.run")
    def test_custom_port(self, mock_run):
        """Should use the specified remote port."""
        from orchestrator.session.health import probe_tunnel_connectivity

        mock_run.return_value = MagicMock(stdout="200", stderr="", returncode=0)

        probe_tunnel_connectivity("user/rdev-vm", remote_port=9999)

        args = mock_run.call_args[0][0]
        # The curl command is the last argument to SSH
        curl_cmd = args[-1]
        assert "9999" in curl_cmd

    @patch("orchestrator.session.health.subprocess.run")
    def test_handles_quoted_http_code(self, mock_run):
        """Should handle curl output with quotes around the HTTP code."""
        from orchestrator.session.health import probe_tunnel_connectivity

        # curl -w '%{http_code}' may return with quotes
        mock_run.return_value = MagicMock(stdout="'200'", stderr="", returncode=0)

        assert probe_tunnel_connectivity("user/rdev-vm") is True


# ==============================================================================
# find_tunnel_pids
# ==============================================================================


class TestFindTunnelPids:
    """Tests for finding SSH tunnel PIDs by host."""

    @patch("orchestrator.session.health.subprocess.run")
    def test_finds_matching_pids(self, mock_run):
        """Should find PIDs of SSH -N -R tunnel processes for the host."""
        from orchestrator.session.health import find_tunnel_pids

        ps_output = (
            "USER       PID  %CPU %MEM COMMAND\n"
            "yuqiu    11111   0.0  0.0 ssh -o StrictHostKeyChecking=no -N -R 8093:127.0.0.1:8093 user/rdev-vm\n"  # noqa: E501
            "yuqiu    22222   0.0  0.0 ssh -N -L 4200:localhost:4200 user/rdev-vm\n"  # noqa: E501  -L not -R
            "yuqiu    33333   0.0  0.0 ssh -o Foo=bar -N -R 9093:127.0.0.1:9093 other/host\n"  # noqa: E501
        )
        mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)

        pids = find_tunnel_pids("user/rdev-vm")

        assert 11111 in pids
        assert 22222 not in pids  # -L tunnel, not -R
        assert 33333 not in pids  # different host

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_empty_on_no_matches(self, mock_run):
        """Should return empty list if no tunnel processes found."""
        from orchestrator.session.health import find_tunnel_pids

        mock_run.return_value = MagicMock(stdout="USER PID COMMAND\n", returncode=0)

        assert find_tunnel_pids("user/rdev-vm") == []

    @patch("orchestrator.session.health.subprocess.run")
    def test_returns_empty_on_timeout(self, mock_run):
        """Should return empty list on subprocess timeout."""
        import subprocess

        from orchestrator.session.health import find_tunnel_pids

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ps", timeout=5)

        assert find_tunnel_pids("user/rdev-vm") == []

    @patch("orchestrator.session.health.subprocess.run")
    def test_skips_grep_lines(self, mock_run):
        """Should skip grep processes in ps output."""
        from orchestrator.session.health import find_tunnel_pids

        ps_output = (
            "yuqiu    11111   0.0  0.0 ssh -N -R 8093:127.0.0.1:8093 user/rdev-vm\n"
            "yuqiu    99999   0.0  0.0 grep ssh -N -R user/rdev-vm\n"
        )
        mock_run.return_value = MagicMock(stdout=ps_output, returncode=0)

        pids = find_tunnel_pids("user/rdev-vm")

        assert 11111 in pids
        assert 99999 not in pids


# ==============================================================================
# kill_tunnel_processes (SIGKILL escalation)
# ==============================================================================


class TestKillTunnelProcesses:
    """Tests for the robust kill with SIGTERM → SIGKILL escalation."""

    @patch("orchestrator.session.health._is_pid_alive")
    @patch("orchestrator.session.health.os.kill")
    @patch("orchestrator.session.health.find_tunnel_pids")
    def test_kills_with_sigterm(self, mock_find, mock_kill, mock_alive):
        """Should send SIGTERM first and succeed if process exits."""
        from orchestrator.session.health import kill_tunnel_processes

        mock_find.return_value = [1234]
        # Process dies after SIGTERM (not alive on first check)
        mock_alive.return_value = False

        result = kill_tunnel_processes("user/rdev-vm", graceful_timeout=1.0)

        assert result == 1
        mock_kill.assert_called_once_with(1234, signal.SIGTERM)

    @patch("orchestrator.session.health._is_pid_alive")
    @patch("orchestrator.session.health.os.kill")
    @patch("orchestrator.session.health.find_tunnel_pids")
    def test_escalates_to_sigkill(self, mock_find, mock_kill, mock_alive):
        """Should escalate to SIGKILL if process survives SIGTERM."""
        from orchestrator.session.health import kill_tunnel_processes

        mock_find.return_value = [5678]
        # Process stays alive through all checks (SIGTERM doesn't work)
        mock_alive.return_value = True

        result = kill_tunnel_processes("user/rdev-vm", graceful_timeout=0.1)

        assert result == 1
        # Should have sent SIGTERM then SIGKILL
        kill_calls = mock_kill.call_args_list
        assert call(5678, signal.SIGTERM) in kill_calls
        assert call(5678, signal.SIGKILL) in kill_calls

    @patch("orchestrator.session.health.find_tunnel_pids")
    def test_returns_zero_when_no_processes(self, mock_find):
        """Should return 0 when no tunnel processes found."""
        from orchestrator.session.health import kill_tunnel_processes

        mock_find.return_value = []

        assert kill_tunnel_processes("user/rdev-vm") == 0

    @patch("orchestrator.session.health._is_pid_alive")
    @patch("orchestrator.session.health.os.kill")
    @patch("orchestrator.session.health.find_tunnel_pids")
    def test_handles_multiple_processes(self, mock_find, mock_kill, mock_alive):
        """Should kill all matching tunnel processes."""
        from orchestrator.session.health import kill_tunnel_processes

        mock_find.return_value = [1111, 2222]
        mock_alive.return_value = False  # Both die after SIGTERM

        result = kill_tunnel_processes("user/rdev-vm", graceful_timeout=1.0)

        assert result == 2
        assert call(1111, signal.SIGTERM) in mock_kill.call_args_list
        assert call(2222, signal.SIGTERM) in mock_kill.call_args_list

    @patch("orchestrator.session.health._is_pid_alive")
    @patch("orchestrator.session.health.os.kill")
    @patch("orchestrator.session.health.find_tunnel_pids")
    def test_handles_process_lookup_error(self, mock_find, mock_kill, mock_alive):
        """Should handle ProcessLookupError (process already dead)."""
        from orchestrator.session.health import kill_tunnel_processes

        mock_find.return_value = [1234]
        mock_kill.side_effect = ProcessLookupError
        mock_alive.return_value = False

        # Should not raise
        result = kill_tunnel_processes("user/rdev-vm", graceful_timeout=0.1)
        assert result == 1


# ==============================================================================
# Periodic tunnel health monitor
# ==============================================================================


class TestTunnelHealthLoop:
    """Tests for the periodic tunnel health monitoring loop (subprocess-based)."""

    @patch("orchestrator.session.tunnel_monitor.sessions_repo")
    async def test_check_all_tunnels_skips_local(self, mock_repo):
        """Should skip local workers (host=localhost)."""
        from orchestrator.session.tunnel_monitor import _check_all_tunnels

        mock_session = MagicMock()
        mock_session.host = "localhost"
        mock_session.status = "waiting"
        mock_session.id = "sess-1"
        mock_repo.list_sessions.return_value = [mock_session]

        mock_tm = MagicMock()

        await _check_all_tunnels(MagicMock(), mock_tm)

        mock_tm.is_alive.assert_not_called()

    @patch("orchestrator.session.tunnel_monitor.sessions_repo")
    async def test_check_all_tunnels_skips_disconnected(self, mock_repo):
        """Should skip disconnected workers."""
        from orchestrator.session.tunnel_monitor import _check_all_tunnels

        mock_session = MagicMock()
        mock_session.host = "user/rdev-vm"
        mock_session.status = "disconnected"
        mock_session.id = "sess-1"
        mock_repo.list_sessions.return_value = [mock_session]

        mock_tm = MagicMock()

        await _check_all_tunnels(MagicMock(), mock_tm)

        mock_tm.is_alive.assert_not_called()

    @patch("orchestrator.session.tunnel_monitor.sessions_repo")
    async def test_check_all_tunnels_skips_connecting(self, mock_repo):
        """Should skip workers in connecting state."""
        from orchestrator.session.tunnel_monitor import _check_all_tunnels

        mock_session = MagicMock()
        mock_session.host = "user/rdev-vm"
        mock_session.status = "connecting"
        mock_session.id = "sess-1"
        mock_repo.list_sessions.return_value = [mock_session]

        mock_tm = MagicMock()

        await _check_all_tunnels(MagicMock(), mock_tm)

        mock_tm.is_alive.assert_not_called()

    @patch("orchestrator.session.tunnel_monitor.sessions_repo")
    async def test_check_all_tunnels_skips_alive(self, mock_repo):
        """Should not restart when tunnel is alive."""
        from orchestrator.session.tunnel_monitor import _check_all_tunnels

        mock_session = MagicMock()
        mock_session.host = "user/rdev-vm"
        mock_session.status = "waiting"
        mock_session.name = "w1"
        mock_session.id = "sess-1"
        mock_repo.list_sessions.return_value = [mock_session]

        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = True

        await _check_all_tunnels(MagicMock(), mock_tm)

        mock_tm.is_alive.assert_called_once_with("sess-1")
        mock_tm.restart_tunnel.assert_not_called()

    @patch("orchestrator.session.tunnel_monitor.sessions_repo")
    async def test_check_all_tunnels_restarts_dead(self, mock_repo):
        """Should restart tunnel via tunnel_manager when dead."""
        from orchestrator.session.tunnel_monitor import _check_all_tunnels

        mock_session = MagicMock()
        mock_session.host = "user/rdev-vm"
        mock_session.status = "waiting"
        mock_session.name = "w1"
        mock_session.id = "sess-1"
        mock_repo.list_sessions.return_value = [mock_session]

        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = False
        mock_tm.restart_tunnel.return_value = 99999
        mock_tm.get_failure_info.return_value = (0, None)

        await _check_all_tunnels(MagicMock(), mock_tm)

        mock_tm.is_alive.assert_called_once_with("sess-1")
        mock_tm.restart_tunnel.assert_called_once_with("sess-1", "w1", "user/rdev-vm")

    @patch("orchestrator.session.tunnel_monitor.sessions_repo")
    async def test_check_all_tunnels_updates_db_on_restart(self, mock_repo):
        """Should update tunnel_pid in DB after successful restart."""
        from orchestrator.session.tunnel_monitor import _check_all_tunnels

        mock_conn = MagicMock()
        mock_session = MagicMock()
        mock_session.host = "user/rdev-vm"
        mock_session.status = "idle"
        mock_session.name = "w1"
        mock_session.id = "sess-1"
        mock_repo.list_sessions.return_value = [mock_session]

        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = False
        mock_tm.restart_tunnel.return_value = 12345
        mock_tm.get_failure_info.return_value = (0, None)

        await _check_all_tunnels(mock_conn, mock_tm)

        mock_repo.update_session.assert_called_once_with(mock_conn, "sess-1", tunnel_pid=12345)

    @patch("orchestrator.session.tunnel_monitor.sessions_repo")
    async def test_check_all_tunnels_marks_disconnected_on_restart_failure(self, mock_repo):
        """Should mark session disconnected when restart fails."""
        from orchestrator.session.tunnel_monitor import _check_all_tunnels

        mock_session = MagicMock()
        mock_session.host = "user/rdev-vm"
        mock_session.status = "waiting"
        mock_session.name = "w1"
        mock_session.id = "sess-1"
        mock_repo.list_sessions.return_value = [mock_session]

        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = False
        mock_tm.restart_tunnel.return_value = None
        mock_tm.get_failure_info.return_value = (0, None)

        mock_conn = MagicMock()
        await _check_all_tunnels(mock_conn, mock_tm)

        mock_repo.update_session.assert_called_once_with(mock_conn, "sess-1", status="disconnected")

    @patch("orchestrator.session.tunnel_monitor.sessions_repo")
    async def test_skips_restart_after_max_failures_but_marks_disconnected(self, mock_repo):
        """Should not attempt restart after max failures, but should mark disconnected."""
        from orchestrator.session.tunnel_monitor import _check_all_tunnels

        mock_session = MagicMock()
        mock_session.host = "user/rdev-vm"
        mock_session.status = "waiting"
        mock_session.name = "w1"
        mock_session.id = "sess-1"
        mock_repo.list_sessions.return_value = [mock_session]

        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = False
        mock_tm.get_failure_info.return_value = (5, "bind: Address already in use")

        mock_conn = MagicMock()
        await _check_all_tunnels(mock_conn, mock_tm)

        # Should NOT attempt restart
        mock_tm.restart_tunnel.assert_not_called()
        # Should mark disconnected — worker is unreachable
        mock_repo.update_session.assert_called_once_with(mock_conn, "sess-1", status="disconnected")

    @patch("orchestrator.session.tunnel_monitor.sessions_repo")
    async def test_logs_attempt_count_on_restart(self, mock_repo, caplog):
        """Should log the attempt number when restarting."""
        import logging

        from orchestrator.session.tunnel_monitor import _check_all_tunnels

        mock_session = MagicMock()
        mock_session.host = "user/rdev-vm"
        mock_session.status = "waiting"
        mock_session.name = "w1"
        mock_session.id = "sess-1"
        mock_repo.list_sessions.return_value = [mock_session]

        mock_tm = MagicMock()
        mock_tm.is_alive.return_value = False
        mock_tm.get_failure_info.return_value = (2, None)
        mock_tm.restart_tunnel.return_value = 99999

        with caplog.at_level(logging.WARNING, logger="orchestrator.session.tunnel_monitor"):
            await _check_all_tunnels(MagicMock(), mock_tm)

        assert "attempt 3" in caplog.text


# ==============================================================================
# Integration: reconnect_tunnel_only uses ReverseTunnelManager
# ==============================================================================


class TestReconnectTunnelOnlyKillEscalation:
    """Verify reconnect_tunnel_only uses tunnel_manager for subprocess-based restart."""

    def test_restart_via_tunnel_manager(self, db):
        """Should call tunnel_manager.restart_tunnel and update DB on success."""
        from orchestrator.session.reconnect import reconnect_tunnel_only

        mock_session = MagicMock()
        mock_session.name = "w1"
        mock_session.host = "user/rdev-vm"
        mock_session.id = "sess-123"

        mock_repo = MagicMock()
        mock_tm = MagicMock()
        mock_tm.restart_tunnel.return_value = 55555

        result = reconnect_tunnel_only(
            db, mock_session, "orchestrator", 8093, mock_repo, tunnel_manager=mock_tm
        )

        assert result is True
        mock_tm.restart_tunnel.assert_called_once_with("sess-123", "w1", "user/rdev-vm")
        mock_repo.update_session.assert_called_once_with(db, "sess-123", tunnel_pid=55555)


class TestReconnectBackoff:
    """Exponential backoff for auto-reconnect — no hard attempt limit."""

    def test_first_attempt_not_skipped(self):
        bo = _ReconnectBackoff()
        assert bo.should_skip("s1") is False

    def test_skips_after_failure(self):
        bo = _ReconnectBackoff()
        bo.record_attempt("s1")
        bo.record_failure("s1")
        # Within the 15s base delay window
        assert bo.should_skip("s1") is True

    def test_allows_after_delay(self):
        bo = _ReconnectBackoff()
        bo.record_attempt("s1")
        bo.record_failure("s1")
        # Pretend the attempt was 20s ago (past the 15s base delay)
        with bo._lock:
            bo._last_attempt["s1"] = time.time() - 20
        assert bo.should_skip("s1") is False

    def test_exponential_growth(self):
        bo = _ReconnectBackoff()
        # 3 consecutive failures → delay = 15 * 2^2 = 60s
        for _ in range(3):
            bo.record_attempt("s1")
            bo.record_failure("s1")
        with bo._lock:
            bo._last_attempt["s1"] = time.time() - 50
        # 50s elapsed < 60s delay → still skipped
        assert bo.should_skip("s1") is True
        with bo._lock:
            bo._last_attempt["s1"] = time.time() - 65
        # 65s elapsed > 60s delay → allowed
        assert bo.should_skip("s1") is False

    def test_caps_at_max_delay(self):
        bo = _ReconnectBackoff()
        # 10 failures → raw delay = 15 * 2^9 = 7680s, capped at 300s
        for _ in range(10):
            bo.record_attempt("s1")
            bo.record_failure("s1")
        with bo._lock:
            bo._last_attempt["s1"] = time.time() - 305
        # 305s > 300s cap → allowed (never stuck forever)
        assert bo.should_skip("s1") is False

    def test_resets_on_success(self):
        bo = _ReconnectBackoff()
        bo.record_attempt("s1")
        bo.record_failure("s1")
        assert bo.should_skip("s1") is True
        bo.record_success("s1")
        assert bo.should_skip("s1") is False

    def test_cleanup_removes_tracking(self):
        bo = _ReconnectBackoff()
        bo.record_attempt("s1")
        bo.record_failure("s1")
        bo.cleanup("s1")
        assert bo.should_skip("s1") is False

    def test_independent_sessions(self):
        bo = _ReconnectBackoff()
        bo.record_attempt("s1")
        bo.record_failure("s1")
        assert bo.should_skip("s1") is True
        assert bo.should_skip("s2") is False
