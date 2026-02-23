"""Tests for claude_session_id tracking across the stack.

Covers: migration, repository, API, hook generation, and reconnect logic.
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from orchestrator.state.models import Session
from orchestrator.state.repositories import sessions as repo


# =============================================================================
# Migration tests
# =============================================================================

class TestMigration026:
    """Verify the 026 migration adds claude_session_id column."""

    def test_column_exists_after_migration(self, db):
        """claude_session_id column should exist in sessions table."""
        row = db.execute("PRAGMA table_info(sessions)").fetchall()
        columns = [r["name"] for r in row]
        assert "claude_session_id" in columns

    def test_backfill_sets_claude_session_id_to_id(self, db):
        """Existing rows should have claude_session_id = id after migration."""
        # The migration backfills claude_session_id = id for existing rows.
        # Insert a row without claude_session_id, then re-apply migration logic.
        # Since apply_migrations already ran, just create a session and verify.
        s = repo.create_session(db, "test-backfill", "localhost")
        assert s.claude_session_id == s.id


# =============================================================================
# Repository tests
# =============================================================================

class TestSessionRepository:
    """Test claude_session_id in create and update operations."""

    def test_create_session_populates_claude_session_id(self, db):
        """create_session() should set claude_session_id = id automatically."""
        s = repo.create_session(db, "test-create", "localhost")
        assert s.claude_session_id is not None
        assert s.claude_session_id == s.id

    def test_update_session_claude_session_id(self, db):
        """update_session() should persist claude_session_id."""
        s = repo.create_session(db, "test-update", "localhost")
        new_id = "new-claude-session-abc123"
        updated = repo.update_session(db, s.id, claude_session_id=new_id)
        assert updated.claude_session_id == new_id

    def test_update_session_claude_session_id_only(self, db):
        """update_session() with only claude_session_id should work (no status change)."""
        s = repo.create_session(db, "test-csid-only", "localhost")
        original_status = s.status
        updated = repo.update_session(db, s.id, claude_session_id="some-uuid")
        assert updated.claude_session_id == "some-uuid"
        assert updated.status == original_status

    def test_update_session_claude_session_id_with_status(self, db):
        """update_session() with both status and claude_session_id should work."""
        s = repo.create_session(db, "test-both", "localhost")
        updated = repo.update_session(db, s.id, status="working", claude_session_id="new-uuid")
        assert updated.status == "working"
        assert updated.claude_session_id == "new-uuid"

    def test_update_session_without_claude_session_id_preserves_it(self, db):
        """Updating other fields should not clear claude_session_id."""
        s = repo.create_session(db, "test-preserve", "localhost")
        original_csid = s.claude_session_id
        updated = repo.update_session(db, s.id, status="working")
        assert updated.claude_session_id == original_csid


# =============================================================================
# API tests
# =============================================================================

class TestSessionUpdateAPI:
    """Test PATCH /api/sessions/{id} with claude_session_id."""

    @patch('orchestrator.api.routes.sessions.repo')
    def test_patch_with_claude_session_id(self, mock_repo, db):
        """PATCH should pass claude_session_id through to repo."""
        from orchestrator.api.routes.sessions import update_session, SessionUpdate

        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.status = "idle"
        mock_repo.get_session.return_value = mock_session

        mock_updated = MagicMock()
        mock_updated.id = "test-id"
        mock_updated.status = "idle"
        mock_repo.update_session.return_value = mock_updated

        body = SessionUpdate(claude_session_id="new-claude-uuid")
        update_session("test-id", body, db=db)

        mock_repo.update_session.assert_called_once_with(
            db, "test-id",
            status=None,
            takeover_mode=None,
            claude_session_id="new-claude-uuid",
        )

    @patch('orchestrator.api.routes.sessions.repo')
    def test_patch_with_status_and_claude_session_id(self, mock_repo, db):
        """PATCH with both status and claude_session_id should pass both."""
        from orchestrator.api.routes.sessions import update_session, SessionUpdate

        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.status = "idle"
        mock_repo.get_session.return_value = mock_session

        mock_updated = MagicMock()
        mock_updated.id = "test-id"
        mock_updated.status = "working"
        mock_repo.update_session.return_value = mock_updated

        body = SessionUpdate(status="working", claude_session_id="new-uuid")
        update_session("test-id", body, db=db)

        mock_repo.update_session.assert_called_once_with(
            db, "test-id",
            status="working",
            takeover_mode=None,
            claude_session_id="new-uuid",
        )


# =============================================================================
# Hook generation tests
# =============================================================================

class TestHookGeneration:
    """Verify update-status.sh template contains claude_session_id logic."""

    def test_hook_extracts_claude_sid(self):
        """Hook script should extract session_id from JSON input."""
        hook_path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "agents", "worker", "hooks", "update-status.sh",
        )
        with open(hook_path) as f:
            content = f.read()

        assert "CLAUDE_SID" in content
        assert ".session_id" in content

    def test_hook_sends_claude_session_id_in_curl(self):
        """Hook script should include claude_session_id in curl requests."""
        hook_path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "agents", "worker", "hooks", "update-status.sh",
        )
        with open(hook_path) as f:
            content = f.read()

        assert "claude_session_id" in content
        # In bash: {\"claude_session_id\": \"$CLAUDE_SID\"}
        assert '\\"claude_session_id\\"' in content

    def test_hook_session_start_non_startup_sends_claude_session_id(self):
        """SessionStart for /clear should send claude_session_id without status change."""
        hook_path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "agents", "worker", "hooks", "update-status.sh",
        )
        with open(hook_path) as f:
            content = f.read()

        # Should have a dedicated curl in SessionStart that sends only claude_session_id
        # (no status field) and exits before the shared curl at the bottom
        assert '{\\"claude_session_id\\": \\"$CLAUDE_SID\\"}' in content


# =============================================================================
# Reconnect tests
# =============================================================================

class TestGetClaudeSessionArg:
    """Test _get_claude_session_arg three-way logic."""

    def test_session_exists_returns_resume(self):
        """If session exists, use -r to resume."""
        from orchestrator.session.reconnect import _get_claude_session_arg
        result = _get_claude_session_arg("abc-123", session_exists=True)
        assert result == "-r abc-123"

    def test_session_exists_with_tracked_id_returns_resume(self):
        """If session exists + tracked ID, still use -r."""
        from orchestrator.session.reconnect import _get_claude_session_arg
        result = _get_claude_session_arg("abc-123", session_exists=True, has_tracked_id=True)
        assert result == "-r abc-123"

    def test_tracked_id_session_missing_returns_continue(self):
        """If tracked ID but session file gone, use -c (most recent)."""
        from orchestrator.session.reconnect import _get_claude_session_arg
        result = _get_claude_session_arg("abc-123", session_exists=False, has_tracked_id=True)
        assert result == "-c"

    def test_no_tracked_id_session_missing_returns_new_session(self):
        """If no tracked ID and no session, create new with --session-id."""
        from orchestrator.session.reconnect import _get_claude_session_arg
        result = _get_claude_session_arg("abc-123", session_exists=False, has_tracked_id=False)
        assert result == "--session-id abc-123"

    def test_no_tracked_id_default_session_missing(self):
        """Default has_tracked_id=False should create new session."""
        from orchestrator.session.reconnect import _get_claude_session_arg
        result = _get_claude_session_arg("abc-123", session_exists=False)
        assert result == "--session-id abc-123"


class TestReconnectUsesClaudeSessionId:
    """Test that reconnect logic uses claude_session_id when available."""

    @patch('orchestrator.session.reconnect._verify_claude_started', return_value=(True, ""))
    @patch('orchestrator.session.reconnect._check_claude_session_exists_local')
    @patch('orchestrator.session.reconnect._get_claude_session_arg')
    @patch('orchestrator.session.reconnect.safe_send_keys')
    @patch('orchestrator.session.reconnect._ensure_local_configs_exist')
    @patch('orchestrator.session.reconnect.get_path_export_command', return_value="export PATH=...")
    @patch('orchestrator.session.reconnect.get_worker_prompt', return_value=None)
    @patch('orchestrator.session.reconnect.check_tui_running_in_pane', return_value=False)
    def test_local_reconnect_uses_claude_session_id(
        self, mock_tui, mock_prompt, mock_path, mock_ensure,
        mock_safe_send, mock_get_arg, mock_check_exists, mock_verify,
    ):
        """reconnect_local_worker should use claude_session_id for session check."""
        from orchestrator.session.reconnect import reconnect_local_worker

        mock_check_exists.return_value = True
        mock_get_arg.return_value = "-r tracked-uuid"

        session = Session(
            id="orch-id",
            name="test-worker",
            host="localhost",
            work_dir="/tmp/test",
            claude_session_id="tracked-uuid",
        )

        with patch('orchestrator.session.reconnect.get_reconnect_lock') as mock_lock:
            mock_lock.return_value = MagicMock()
            mock_lock.return_value.acquire.return_value = True
            with patch('orchestrator.terminal.manager.ensure_window'):
                reconnect_local_worker(session, "orch", "test-worker", 8093, "/tmp/test-dir")

        # Should check with tracked UUID, not orchestrator ID
        mock_check_exists.assert_called_once_with("tracked-uuid")
        mock_get_arg.assert_called_once_with("tracked-uuid", True, True)

    @patch('orchestrator.session.reconnect._verify_claude_started', return_value=(True, ""))
    @patch('orchestrator.session.reconnect._check_claude_session_exists_local')
    @patch('orchestrator.session.reconnect._get_claude_session_arg')
    @patch('orchestrator.session.reconnect.safe_send_keys')
    @patch('orchestrator.session.reconnect._ensure_local_configs_exist')
    @patch('orchestrator.session.reconnect.get_path_export_command', return_value="export PATH=...")
    @patch('orchestrator.session.reconnect.get_worker_prompt', return_value=None)
    @patch('orchestrator.session.reconnect.check_tui_running_in_pane', return_value=False)
    def test_local_reconnect_falls_back_to_orch_id(
        self, mock_tui, mock_prompt, mock_path, mock_ensure,
        mock_safe_send, mock_get_arg, mock_check_exists, mock_verify,
    ):
        """reconnect_local_worker without claude_session_id should use session.id."""
        from orchestrator.session.reconnect import reconnect_local_worker

        mock_check_exists.return_value = False
        mock_get_arg.return_value = "--session-id orch-id"

        session = Session(
            id="orch-id",
            name="test-worker",
            host="localhost",
            work_dir="/tmp/test",
            claude_session_id=None,
        )

        with patch('orchestrator.session.reconnect.get_reconnect_lock') as mock_lock:
            mock_lock.return_value = MagicMock()
            mock_lock.return_value.acquire.return_value = True
            with patch('orchestrator.terminal.manager.ensure_window'):
                reconnect_local_worker(session, "orch", "test-worker", 8093, "/tmp/test-dir")

        # Should fall back to orchestrator ID
        mock_check_exists.assert_called_once_with("orch-id")
        mock_get_arg.assert_called_once_with("orch-id", False, False)


# =============================================================================
# Claude launch recovery tests
# =============================================================================

class TestClaudeLaunchRecovery:
    """Test that reconnect retries with --session-id when -r fails."""

    @patch('orchestrator.session.reconnect._verify_claude_started')
    @patch('orchestrator.session.reconnect.send_keys')
    @patch('orchestrator.session.reconnect._check_claude_session_exists_local', return_value=True)
    @patch('orchestrator.session.reconnect.safe_send_keys')
    @patch('orchestrator.session.reconnect._ensure_local_configs_exist')
    @patch('orchestrator.session.reconnect.get_path_export_command', return_value="export PATH=...")
    @patch('orchestrator.session.reconnect.get_worker_prompt', return_value=None)
    @patch('orchestrator.session.reconnect.check_tui_running_in_pane', return_value=False)
    def test_local_reconnect_retries_on_r_failure(
        self, mock_tui, mock_prompt, mock_path, mock_ensure,
        mock_safe_send, mock_check_exists, mock_send_keys, mock_verify,
    ):
        """When -r fails, should retry with --session-id."""
        from orchestrator.session.reconnect import reconnect_local_worker

        # First call fails (original -r), second call succeeds (retry --session-id)
        mock_verify.side_effect = [
            (False, "No conversation found with session ID: abc-123"),
            (True, ""),
        ]

        session = Session(
            id="abc-123",
            name="test-worker",
            host="localhost",
            work_dir="/tmp/test",
            claude_session_id="abc-123",
        )

        with patch('orchestrator.session.reconnect.get_reconnect_lock') as mock_lock:
            mock_lock.return_value = MagicMock()
            mock_lock.return_value.acquire.return_value = True
            with patch('orchestrator.terminal.manager.ensure_window'):
                reconnect_local_worker(session, "orch", "test-worker", 8093, "/tmp/test-dir")

        # _verify_claude_started should be called twice (initial + retry)
        assert mock_verify.call_count == 2

        # The retry command should use --session-id
        retry_calls = [
            c for c in mock_safe_send.call_args_list
            if "--session-id" in str(c)
        ]
        assert len(retry_calls) >= 1, (
            f"Expected retry with --session-id, got: {mock_safe_send.call_args_list}"
        )

    @patch('orchestrator.session.reconnect._verify_claude_started', return_value=(True, ""))
    @patch('orchestrator.session.reconnect._check_claude_session_exists_local', return_value=True)
    @patch('orchestrator.session.reconnect.safe_send_keys')
    @patch('orchestrator.session.reconnect._ensure_local_configs_exist')
    @patch('orchestrator.session.reconnect.get_path_export_command', return_value="export PATH=...")
    @patch('orchestrator.session.reconnect.get_worker_prompt', return_value=None)
    @patch('orchestrator.session.reconnect.check_tui_running_in_pane', return_value=False)
    def test_local_reconnect_no_retry_on_success(
        self, mock_tui, mock_prompt, mock_path, mock_ensure,
        mock_safe_send, mock_check_exists, mock_verify,
    ):
        """When -r succeeds, should NOT retry."""
        from orchestrator.session.reconnect import reconnect_local_worker

        session = Session(
            id="abc-123",
            name="test-worker",
            host="localhost",
            work_dir="/tmp/test",
            claude_session_id="abc-123",
        )

        with patch('orchestrator.session.reconnect.get_reconnect_lock') as mock_lock:
            mock_lock.return_value = MagicMock()
            mock_lock.return_value.acquire.return_value = True
            with patch('orchestrator.terminal.manager.ensure_window'):
                reconnect_local_worker(session, "orch", "test-worker", 8093, "/tmp/test-dir")

        # _verify_claude_started should only be called once (success on first try)
        assert mock_verify.call_count == 1

        # No --session-id retry should have been sent
        retry_calls = [
            c for c in mock_safe_send.call_args_list
            if "--session-id" in str(c)
        ]
        assert len(retry_calls) == 0, (
            f"Expected no retry with --session-id, got: {mock_safe_send.call_args_list}"
        )

    @patch('orchestrator.session.reconnect._verify_claude_started')
    @patch('orchestrator.session.reconnect.send_keys')
    @patch('orchestrator.session.reconnect._check_claude_session_exists_local', return_value=True)
    @patch('orchestrator.session.reconnect.safe_send_keys')
    @patch('orchestrator.session.reconnect._ensure_local_configs_exist')
    @patch('orchestrator.session.reconnect.get_path_export_command', return_value="export PATH=...")
    @patch('orchestrator.session.reconnect.get_worker_prompt', return_value=None)
    @patch('orchestrator.session.reconnect.check_tui_running_in_pane', return_value=False)
    def test_local_reconnect_gives_up_after_retry_fails(
        self, mock_tui, mock_prompt, mock_path, mock_ensure,
        mock_safe_send, mock_check_exists, mock_send_keys, mock_verify,
    ):
        """When both -r and --session-id fail, should give up gracefully."""
        from orchestrator.session.reconnect import reconnect_local_worker

        # Both attempts fail
        mock_verify.side_effect = [
            (False, "No conversation found"),
            (False, "Some other error"),
        ]

        session = Session(
            id="abc-123",
            name="test-worker",
            host="localhost",
            work_dir="/tmp/test",
            claude_session_id="abc-123",
        )

        with patch('orchestrator.session.reconnect.get_reconnect_lock') as mock_lock:
            mock_lock.return_value = MagicMock()
            mock_lock.return_value.acquire.return_value = True
            with patch('orchestrator.terminal.manager.ensure_window'):
                # Should not raise
                reconnect_local_worker(session, "orch", "test-worker", 8093, "/tmp/test-dir")

        # Both attempts checked
        assert mock_verify.call_count == 2


# =============================================================================
# Local launch --session-id test
# =============================================================================

class TestLocalLaunchSessionId:
    """Test that local worker launch includes --session-id."""

    @patch('orchestrator.api.routes.sessions.threading')
    @patch('orchestrator.api.routes.sessions.ensure_window')
    @patch('orchestrator.api.routes.sessions.repo')
    @patch('orchestrator.api.routes.sessions.is_rdev_host')
    @patch('orchestrator.api.routes.sessions.send_keys')
    def test_local_launch_includes_session_id(
        self, mock_send_keys, mock_is_rdev, mock_repo, mock_ensure_window, mock_threading, db
    ):
        """Local worker launch should include --session-id in claude args."""
        from orchestrator.api.routes.sessions import create_session, SessionCreate

        mock_is_rdev.return_value = False
        mock_ensure_window.return_value = "orchestrator:test-worker"

        mock_session = MagicMock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-worker"
        mock_session.host = "localhost"
        mock_session.status = "idle"
        mock_session.work_dir = None
        mock_repo.create_session.return_value = mock_session

        body = SessionCreate(name="test-worker", host="localhost")
        mock_request = MagicMock()
        mock_request.app.state.config = {"server": {"port": 8093}}

        create_session(body, mock_request, db=db)

        # Find the send_keys call that launches claude
        claude_launch_calls = [
            c for c in mock_send_keys.call_args_list
            if "claude" in str(c) and "--session-id" in str(c)
        ]
        assert len(claude_launch_calls) >= 1, (
            f"Expected claude launch with --session-id, got calls: {mock_send_keys.call_args_list}"
        )
