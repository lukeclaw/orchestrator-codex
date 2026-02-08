"""Tests for SSH helper functions."""

from orchestrator.terminal.ssh import is_rdev_host


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
