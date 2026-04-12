"""Tests for SSH helper functions."""

import subprocess
from unittest.mock import MagicMock, patch

from orchestrator.terminal.ssh import (
    _kill_control_sockets_for_host,
    _rdev_ssh_config_has_host,
    _remove_rdev_ssh_config_host,
    _remove_stale_known_hosts_old,
    ensure_rdev_ssh_config,
    is_rdev_host,
    is_remote_host,
    refresh_rdev_ssh_config,
    remote_connect,
)


class TestIsRdevHost:
    def test_valid_rdev_host(self):
        assert is_rdev_host("subs-mt/sleepy-franklin") is True

    def test_valid_rdev_host_dashes(self):
        assert is_rdev_host("jobs-mt/epic-turing") is True

    def test_localhost_not_rdev(self):
        assert is_rdev_host("localhost") is False

    def test_regular_ssh_host(self):
        assert is_rdev_host("rdev1.example.com") is False

    def test_empty_string(self):
        assert is_rdev_host("") is False

    def test_leading_slash(self):
        assert is_rdev_host("/something") is False

    def test_trailing_slash(self):
        assert is_rdev_host("something/") is False

    def test_multiple_slashes(self):
        assert is_rdev_host("a/b/c") is False

    def test_just_a_slash(self):
        assert is_rdev_host("/") is False

    def test_ip_address(self):
        assert is_rdev_host("192.168.1.1") is False


class TestIsRemoteHost:
    def test_localhost_is_not_remote(self):
        assert is_remote_host("localhost") is False

    def test_rdev_host_is_remote(self):
        assert is_remote_host("subs-mt/sleepy-franklin") is True

    def test_ssh_hostname_is_remote(self):
        assert is_remote_host("user@hostname.example.com") is True

    def test_ip_address_is_remote(self):
        assert is_remote_host("192.168.1.100") is True

    def test_simple_hostname_is_remote(self):
        assert is_remote_host("myserver") is True

    def test_local_is_not_remote(self):
        assert is_remote_host("local") is False

    def test_loopback_ipv4_is_not_remote(self):
        assert is_remote_host("127.0.0.1") is False

    def test_loopback_ipv6_is_not_remote(self):
        assert is_remote_host("::1") is False

    def test_localhost_case_insensitive(self):
        assert is_remote_host("Localhost") is False
        assert is_remote_host("LOCAL") is False

    def test_empty_string_is_remote(self):
        # Empty string is not "localhost" so it's treated as remote
        assert is_remote_host("") is True


class TestRemoteConnect:
    @patch("orchestrator.terminal.ssh.send_keys")
    def test_rdev_host_uses_rdev_ssh(self, mock_send_keys):
        mock_send_keys.return_value = True
        result = remote_connect("orch", "w1", "subs-mt/sleepy-franklin")
        assert result is True
        mock_send_keys.assert_called_once_with(
            "orch", "w1", "rdev ssh subs-mt/sleepy-franklin --non-tmux"
        )

    @patch("orchestrator.terminal.ssh.send_keys")
    def test_generic_ssh_host_uses_plain_ssh(self, mock_send_keys):
        mock_send_keys.return_value = True
        result = remote_connect("orch", "w1", "user@myhost.example.com")
        assert result is True
        mock_send_keys.assert_called_once_with("orch", "w1", "ssh user@myhost.example.com")

    @patch("orchestrator.terminal.ssh.send_keys")
    def test_ip_host_uses_plain_ssh(self, mock_send_keys):
        mock_send_keys.return_value = True
        result = remote_connect("orch", "w1", "192.168.1.100")
        assert result is True
        mock_send_keys.assert_called_once_with("orch", "w1", "ssh 192.168.1.100")

    @patch("orchestrator.terminal.ssh._remove_stale_known_hosts_old")
    @patch("orchestrator.terminal.ssh.send_keys")
    def test_rdev_host_removes_known_hosts_old(self, mock_send_keys, mock_remove):
        """Should remove stale known_hosts.old before rdev ssh."""
        mock_send_keys.return_value = True
        remote_connect("orch", "w1", "subs-mt/sleepy-franklin")
        mock_remove.assert_called_once()

    @patch("orchestrator.terminal.ssh._remove_stale_known_hosts_old")
    @patch("orchestrator.terminal.ssh.send_keys")
    def test_plain_ssh_does_not_remove_known_hosts_old(self, mock_send_keys, mock_remove):
        """Should NOT remove known_hosts.old for plain SSH hosts."""
        mock_send_keys.return_value = True
        remote_connect("orch", "w1", "user@myhost.example.com")
        mock_remove.assert_not_called()


class TestRemoveStaleKnownHostsOld:
    def test_removes_existing_file(self, tmp_path):
        """Should remove the file when it exists."""
        old_file = tmp_path / "known_hosts.old"
        old_file.write_text("stale data")

        with patch("orchestrator.terminal.ssh._KNOWN_HOSTS_OLD", str(old_file)):
            _remove_stale_known_hosts_old()

        assert not old_file.exists()

    def test_noop_when_file_missing(self, tmp_path):
        """Should not raise when file doesn't exist."""
        with patch(
            "orchestrator.terminal.ssh._KNOWN_HOSTS_OLD",
            str(tmp_path / "nonexistent"),
        ):
            _remove_stale_known_hosts_old()  # should not raise


class TestRdevSshConfigHasHost:
    def test_finds_existing_host(self, tmp_path):
        config = tmp_path / "config.rdev"
        config.write_text(
            "Host subs-backend/happy-einstein subs-backend_happy-einstein\n"
            "  HostName rdev-aks-wus3-12.example.com\n"
            "  Port 42410\n"
        )
        with patch("orchestrator.terminal.ssh._RDEV_SSH_CONFIG", str(config)):
            assert _rdev_ssh_config_has_host("subs-backend/happy-einstein") is True

    def test_missing_host(self, tmp_path):
        config = tmp_path / "config.rdev"
        config.write_text(
            "Host subs-backend/happy-einstein subs-backend_happy-einstein\n"
            "  HostName rdev-aks-wus3-12.example.com\n"
        )
        with patch("orchestrator.terminal.ssh._RDEV_SSH_CONFIG", str(config)):
            assert _rdev_ssh_config_has_host("subs-backend/envious-valley") is False

    def test_missing_config_file(self, tmp_path):
        with patch("orchestrator.terminal.ssh._RDEV_SSH_CONFIG", str(tmp_path / "nope")):
            assert _rdev_ssh_config_has_host("subs-backend/envious-valley") is False

    def test_finds_underscore_alias(self, tmp_path):
        config = tmp_path / "config.rdev"
        config.write_text("Host mp/session mp_session\n  HostName example.com\n")
        with patch("orchestrator.terminal.ssh._RDEV_SSH_CONFIG", str(config)):
            assert _rdev_ssh_config_has_host("mp/session") is True


class TestEnsureRdevSshConfig:
    def test_non_rdev_host_returns_true(self):
        """Non-rdev hosts skip entirely."""
        assert ensure_rdev_ssh_config("plain-host.example.com") is True

    def test_existing_config_skips_rdev_ssh(self, tmp_path):
        """When config entry already exists, no subprocess is spawned."""
        config = tmp_path / "config.rdev"
        config.write_text("Host mp/session mp_session\n  HostName x.com\n")
        with patch("orchestrator.terminal.ssh._RDEV_SSH_CONFIG", str(config)):
            result = ensure_rdev_ssh_config("mp/session")
        assert result is True

    @patch("orchestrator.terminal.ssh.subprocess.Popen")
    def test_spawns_rdev_ssh_when_config_missing(self, mock_popen, tmp_path):
        """When config is missing, should spawn rdev ssh and poll for entry."""
        config = tmp_path / "config.rdev"

        mock_proc = MagicMock()
        call_count = 0

        def poll_side_effect():
            nonlocal call_count
            call_count += 1
            # Simulate rdev writing the config on second poll
            if call_count >= 2 and not config.exists():
                config.write_text("Host mp/new-rdev mp_new-rdev\n  HostName x.com\n")
            return None  # process still running

        mock_proc.poll.side_effect = poll_side_effect
        mock_popen.return_value = mock_proc

        with patch("orchestrator.terminal.ssh._RDEV_SSH_CONFIG", str(config)):
            result = ensure_rdev_ssh_config("mp/new-rdev", timeout=30)

        assert result is True
        mock_popen.assert_called_once()
        assert "rdev" in mock_popen.call_args[0][0]
        mock_proc.terminate.assert_called_once()

    @patch("orchestrator.terminal.ssh.subprocess.Popen")
    def test_returns_false_when_process_dies_without_config(self, mock_popen, tmp_path):
        """If rdev ssh exits before writing config, return False."""
        config = tmp_path / "config.rdev"

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # process already exited
        mock_popen.return_value = mock_proc

        with patch("orchestrator.terminal.ssh._RDEV_SSH_CONFIG", str(config)):
            result = ensure_rdev_ssh_config("mp/bad-rdev", timeout=30)

        assert result is False

    @patch(
        "orchestrator.terminal.ssh.subprocess.Popen",
        side_effect=FileNotFoundError("rdev not found"),
    )
    def test_returns_false_when_rdev_cli_missing(self, mock_popen, tmp_path):
        """If rdev CLI is not installed, return False."""
        with patch("orchestrator.terminal.ssh._RDEV_SSH_CONFIG", str(tmp_path / "nope")):
            result = ensure_rdev_ssh_config("mp/session")
        assert result is False


# =========================================================================
# Tests for _remove_rdev_ssh_config_host
# =========================================================================


class TestRemoveRdevSshConfigHost:
    def test_removes_host_block(self, tmp_path):
        """Should remove the matching host block and keep others."""
        config = tmp_path / "config.rdev"
        config.write_text(
            "Host mp/keep mp_keep\n"
            "  HostName keep.example.com\n"
            "  Port 1111\n"
            "Host mp/remove mp_remove\n"
            "  HostName remove.example.com\n"
            "  Port 2222\n"
            "Host mp/also-keep mp_also-keep\n"
            "  HostName also.example.com\n"
            "  Port 3333\n"
        )
        with patch("orchestrator.terminal.ssh._RDEV_SSH_CONFIG", str(config)):
            result = _remove_rdev_ssh_config_host("mp/remove")

        assert result is True
        remaining = config.read_text()
        assert "mp/keep" in remaining
        assert "mp/also-keep" in remaining
        assert "mp/remove" not in remaining
        assert "remove.example.com" not in remaining
        assert "Port 2222" not in remaining

    def test_removes_last_host_block(self, tmp_path):
        """Removing the only host should leave the file with no Host blocks."""
        config = tmp_path / "config.rdev"
        config.write_text("Host mp/only mp_only\n  HostName only.example.com\n  Port 4444\n")
        with patch("orchestrator.terminal.ssh._RDEV_SSH_CONFIG", str(config)):
            result = _remove_rdev_ssh_config_host("mp/only")

        assert result is True
        assert config.read_text().strip() == ""

    def test_missing_host_returns_false(self, tmp_path):
        """Removing a nonexistent host should return False."""
        config = tmp_path / "config.rdev"
        config.write_text("Host mp/other mp_other\n  HostName x.com\n")
        with patch("orchestrator.terminal.ssh._RDEV_SSH_CONFIG", str(config)):
            result = _remove_rdev_ssh_config_host("mp/nonexistent")

        assert result is False
        # Original content unchanged
        assert "mp/other" in config.read_text()

    def test_missing_file_returns_false(self, tmp_path):
        """Missing config file should return False gracefully."""
        with patch("orchestrator.terminal.ssh._RDEV_SSH_CONFIG", str(tmp_path / "nope")):
            result = _remove_rdev_ssh_config_host("mp/session")

        assert result is False


# =========================================================================
# Tests for _kill_control_sockets_for_host
# =========================================================================


class TestKillControlSocketsForHost:
    @patch("orchestrator.terminal.ssh.subprocess.Popen")
    def test_calls_ssh_exit(self, mock_popen):
        """Should spawn ssh -O exit with the correct ControlPath."""
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc
        _kill_control_sockets_for_host("mp/session")
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "ssh"
        assert "-O" in cmd
        assert "exit" in cmd
        assert "mp/session" in cmd
        mock_proc.wait.assert_called_once_with(timeout=5)

    @patch(
        "orchestrator.terminal.ssh.subprocess.Popen",
        side_effect=OSError("no socket"),
    )
    def test_failure_is_silent(self, mock_popen):
        """Errors should not propagate."""
        _kill_control_sockets_for_host("mp/session")  # should not raise

    @patch("orchestrator.terminal.ssh.subprocess.Popen")
    def test_timeout_is_silent(self, mock_popen):
        """Timeout should not propagate."""
        mock_proc = MagicMock()
        mock_proc.wait.side_effect = subprocess.TimeoutExpired("ssh", 5)
        mock_popen.return_value = mock_proc
        _kill_control_sockets_for_host("mp/session")  # should not raise


# =========================================================================
# Tests for refresh_rdev_ssh_config
# =========================================================================


class TestRefreshRdevSshConfig:
    def test_non_rdev_host_returns_true(self):
        """Non-rdev hosts should skip entirely."""
        assert refresh_rdev_ssh_config("plain-host.example.com") is True

    @patch("orchestrator.terminal.ssh._kill_control_sockets_for_host")
    @patch("orchestrator.terminal.ssh.subprocess.Popen")
    def test_removes_old_and_creates_new(self, mock_popen, mock_kill_ctrl, tmp_path):
        """Full flow: remove old entry, spawn rdev ssh, create new entry."""
        config = tmp_path / "config.rdev"
        config.write_text("Host mp/session mp_session\n  HostName old.example.com\n  Port 11111\n")

        mock_proc = MagicMock()
        call_count = 0

        def poll_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                # Simulate rdev writing new config
                config.write_text(
                    "Host mp/session mp_session\n  HostName new.example.com\n  Port 22222\n"
                )
            return None

        mock_proc.poll.side_effect = poll_side_effect
        mock_popen.return_value = mock_proc

        with (
            patch("orchestrator.terminal.ssh._RDEV_SSH_CONFIG", str(config)),
            patch("orchestrator.terminal.ssh._last_refresh", {}),
        ):
            result = refresh_rdev_ssh_config("mp/session", timeout=30)

        assert result is True
        mock_kill_ctrl.assert_called_once_with("mp/session")
        mock_popen.assert_called_once()
        mock_proc.terminate.assert_called_once()
        # Verify new content
        assert "new.example.com" in config.read_text()

    def test_cooldown_prevents_rapid_refresh(self, tmp_path):
        """Second call within cooldown period should skip subprocess."""
        config = tmp_path / "config.rdev"
        config.write_text("Host mp/cooldown-test mp_cooldown-test\n  HostName x.com\n")

        import orchestrator.terminal.ssh as ssh_mod

        # Use the module's (possibly virtual) time.monotonic — the conftest
        # _VirtualClock replaces ssh_mod.time, so monotonic() returns a
        # virtual-clock value, not the real one.
        ssh_mod._last_refresh["mp/cooldown-test"] = ssh_mod.time.monotonic()
        try:
            with (
                patch("orchestrator.terminal.ssh._RDEV_SSH_CONFIG", str(config)),
                patch("orchestrator.terminal.ssh._kill_control_sockets_for_host") as mock_kill,
                patch("orchestrator.terminal.ssh.subprocess.Popen") as mock_popen,
            ):
                result = refresh_rdev_ssh_config("mp/cooldown-test")
        finally:
            ssh_mod._last_refresh.pop("mp/cooldown-test", None)
            ssh_mod._refresh_locks.pop("mp/cooldown-test", None)

        assert result is True
        mock_popen.assert_not_called()
        mock_kill.assert_not_called()

    @patch("orchestrator.terminal.ssh._kill_control_sockets_for_host")
    @patch("orchestrator.terminal.ssh.subprocess.Popen")
    def test_returns_false_on_timeout(self, mock_popen, mock_kill_ctrl, tmp_path):
        """Should return False if rdev ssh fails to write config."""
        config = tmp_path / "config.rdev"
        # No initial config — and rdev ssh will also fail to create one

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # process already exited
        mock_popen.return_value = mock_proc

        with (
            patch("orchestrator.terminal.ssh._RDEV_SSH_CONFIG", str(config)),
            patch("orchestrator.terminal.ssh._last_refresh", {}),
        ):
            result = refresh_rdev_ssh_config("mp/session", timeout=5)

        assert result is False

    @patch("orchestrator.terminal.ssh.subprocess.Popen")
    def test_kills_control_socket_before_removing_config(self, mock_popen, tmp_path):
        """ControlMaster kill must happen before config removal."""
        config = tmp_path / "config.rdev"
        config.write_text("Host mp/session mp_session\n  HostName x.com\n")

        operation_order = []

        def track_popen(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "-O" in cmd:
                operation_order.append("ssh_exit")
            mock_proc = MagicMock()
            mock_proc.poll.return_value = 1
            return mock_proc

        mock_popen.side_effect = track_popen

        def track_remove(host):
            operation_order.append("remove_config")
            config.write_text("")
            return True

        with (
            patch("orchestrator.terminal.ssh._RDEV_SSH_CONFIG", str(config)),
            patch("orchestrator.terminal.ssh._last_refresh", {}),
            patch(
                "orchestrator.terminal.ssh._remove_rdev_ssh_config_host",
                side_effect=track_remove,
            ),
        ):
            refresh_rdev_ssh_config("mp/session", timeout=5)

        assert operation_order.index("ssh_exit") < operation_order.index("remove_config"), (
            "ControlMaster kill must happen before config removal"
        )
