"""Tests for SSH helper functions."""

from unittest.mock import patch

from orchestrator.terminal.ssh import is_rdev_host, is_remote_host, remote_connect


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

    def test_empty_string_is_remote(self):
        # Empty string is not "localhost" so it's treated as remote
        assert is_remote_host("") is True


class TestRemoteConnect:
    @patch("orchestrator.terminal.ssh.send_keys")
    def test_rdev_host_uses_rdev_ssh(self, mock_send_keys):
        mock_send_keys.return_value = True
        result = remote_connect("orch", "w1", "subs-mt/sleepy-franklin")
        assert result is True
        mock_send_keys.assert_called_once_with("orch", "w1", "rdev ssh subs-mt/sleepy-franklin --non-tmux")

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
