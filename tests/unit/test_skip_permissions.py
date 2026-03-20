"""Tests for claude.skip_permissions setting and its effect on command construction."""

from unittest.mock import patch

from orchestrator.state.repositories.config import get_config_value, set_config


class TestSkipPermissionsSetting:
    """Tests for reading the claude.skip_permissions config value."""

    def test_default_false_when_not_set(self, db):
        """Returns False when config key is absent (default)."""
        value = get_config_value(db, "claude.skip_permissions", default=False)
        assert value is False

    def test_returns_true_when_set_true(self, db):
        """Returns True when config value is True."""
        set_config(db, "claude.skip_permissions", True)
        value = get_config_value(db, "claude.skip_permissions", default=False)
        assert value is True

    def test_returns_false_when_set_false(self, db):
        """Returns False when config value is explicitly False."""
        set_config(db, "claude.skip_permissions", False)
        value = get_config_value(db, "claude.skip_permissions", default=False)
        assert value is False


class TestBuildClaudeCommandSkipPermissions:
    """Tests that _build_claude_command conditionally includes the flag."""

    @patch("orchestrator.terminal.session.is_rdev_host", return_value=False)
    def test_flag_absent_when_skip_permissions_false(self, _mock_rdev):
        from orchestrator.terminal.session import _build_claude_command

        cmd = _build_claude_command(
            "sid-123",
            "some-host",
            "/tmp/orchestrator/workers/w1",
            "/home/user/repo",
            skip_permissions=False,
        )
        assert "--dangerously-skip-permissions" not in cmd

    @patch("orchestrator.terminal.session.is_rdev_host", return_value=False)
    def test_flag_present_when_skip_permissions_true(self, _mock_rdev):
        from orchestrator.terminal.session import _build_claude_command

        cmd = _build_claude_command(
            "sid-123",
            "some-host",
            "/tmp/orchestrator/workers/w1",
            "/home/user/repo",
            skip_permissions=True,
        )
        assert "--dangerously-skip-permissions" in cmd

    @patch("orchestrator.terminal.session.is_rdev_host", return_value=False)
    def test_flag_absent_by_default(self, _mock_rdev):
        """Default parameter value should not include the flag."""
        from orchestrator.terminal.session import _build_claude_command

        cmd = _build_claude_command(
            "sid-123",
            "some-host",
            "/tmp/orchestrator/workers/w1",
            "/home/user/repo",
        )
        assert "--dangerously-skip-permissions" not in cmd

    @patch("orchestrator.terminal.session.is_rdev_host", return_value=False)
    def test_other_args_still_present(self, _mock_rdev):
        """Other arguments (session-id, settings) are always present."""
        from orchestrator.terminal.session import _build_claude_command

        cmd = _build_claude_command(
            "sid-123",
            "some-host",
            "/tmp/orchestrator/workers/w1",
            "/home/user/repo",
            skip_permissions=True,
        )
        assert "--session-id sid-123" in cmd
        assert "--settings /tmp/orchestrator/workers/w1/configs/settings.json" in cmd
        assert "--dangerously-skip-permissions" in cmd
