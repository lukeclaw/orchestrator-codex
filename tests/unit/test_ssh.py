"""Tests for SSH helper functions."""

from unittest.mock import MagicMock, patch

from orchestrator.terminal.ssh import (
    _rdev_ssh_config_has_host,
    _remove_stale_known_hosts_old,
    ensure_rdev_ssh_config,
    is_rdev_host,
    is_remote_host,
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
