"""Tests for session name-or-ID resolution.

The _resolve_session helper allows API endpoints to accept either a UUID
session ID or a human-readable worker name.
"""

from unittest.mock import patch

import pytest

from orchestrator.api.routes.sessions import _resolve_session


class TestResolveSession:
    """Unit tests for _resolve_session helper."""

    def test_resolve_by_id(self, db):
        """When given a valid session ID, returns the session."""
        from orchestrator.state.repositories import sessions as repo

        s = repo.create_session(db, "my-worker", "localhost")

        result = _resolve_session(db, s.id)
        assert result is not None
        assert result.id == s.id
        assert result.name == "my-worker"

    def test_resolve_by_name(self, db):
        """When given a worker name (not a UUID), returns the session."""
        from orchestrator.state.repositories import sessions as repo

        s = repo.create_session(db, "api-worker", "localhost")

        result = _resolve_session(db, "api-worker")
        assert result is not None
        assert result.id == s.id
        assert result.name == "api-worker"

    def test_resolve_not_found(self, db):
        """When neither ID nor name matches, returns None."""
        result = _resolve_session(db, "nonexistent")
        assert result is None

    def test_resolve_prefers_id_over_name(self, db):
        """If the input matches an ID, return that session even if another
        session has a matching name (unlikely but ensures deterministic behavior)."""
        from orchestrator.state.repositories import sessions as repo

        s1 = repo.create_session(db, "worker-a", "localhost")
        # Create another session whose name happens to equal s1's ID
        # (extremely unlikely in practice, but tests precedence)
        repo.create_session(db, s1.id, "localhost")

        result = _resolve_session(db, s1.id)
        # Should find by ID first, which is s1
        assert result.name == "worker-a"


class TestEndpointsAcceptName:
    """Verify that key session endpoints work when given a name instead of ID."""

    @patch("orchestrator.api.routes.sessions.is_remote_host", return_value=False)
    def test_get_session_by_name(self, _mock_remote, db):
        """GET /sessions/{name} should return the session."""
        from orchestrator.api.routes.sessions import get_session
        from orchestrator.state.repositories import sessions as repo

        s = repo.create_session(db, "ui-worker", "localhost")

        result = get_session("ui-worker", db=db)
        assert result["id"] == s.id
        assert result["name"] == "ui-worker"

    def test_get_session_by_name_404(self, db):
        """GET /sessions/{name} should 404 for unknown names."""
        from fastapi import HTTPException

        from orchestrator.api.routes.sessions import get_session

        with pytest.raises(HTTPException) as exc_info:
            get_session("no-such-worker", db=db)
        assert exc_info.value.status_code == 404

    @patch("orchestrator.api.routes.sessions.send_keys")
    @patch("orchestrator.api.routes.sessions.tmux_target", return_value=("orch", "win"))
    def test_pause_session_by_name(self, _mock_tmux, _mock_keys, db):
        """POST /sessions/{name}/pause should work with a name."""
        from orchestrator.api.routes.sessions import pause_session
        from orchestrator.state.repositories import sessions as repo

        s = repo.create_session(db, "pause-me", "localhost")

        result = pause_session("pause-me", db=db)
        assert result["ok"] is True

        # Verify status was updated using real ID
        updated = repo.get_session(db, s.id)
        assert updated.status == "paused"

    @patch("orchestrator.api.routes.sessions.send_keys")
    @patch("orchestrator.api.routes.sessions.send_keys_literal", create=True)
    @patch("orchestrator.api.routes.sessions.tmux_target", return_value=("orch", "win"))
    def test_stop_session_by_name(self, _mock_tmux, _mock_literal, _mock_keys, db):
        """POST /sessions/{name}/stop should work with a name."""
        from orchestrator.api.routes.sessions import stop_session
        from orchestrator.state.repositories import sessions as repo

        s = repo.create_session(db, "stop-me", "localhost")
        # Set to working first so stop is meaningful
        repo.update_session(db, s.id, status="working")

        with patch("orchestrator.terminal.manager.send_keys_literal"):
            result = stop_session("stop-me", db=db)
        assert result["ok"] is True

        updated = repo.get_session(db, s.id)
        assert updated.status == "idle"

    def test_update_session_by_name(self, db):
        """PATCH /sessions/{name} should work with a name."""
        from orchestrator.api.routes.sessions import SessionUpdate, update_session
        from orchestrator.state.repositories import sessions as repo

        s = repo.create_session(db, "patch-me", "localhost")

        body = SessionUpdate(status="working")
        result = update_session("patch-me", body, db=db)
        assert result["id"] == s.id
        assert result["status"] == "working"

    def test_session_preview_by_name(self, db):
        """GET /sessions/{name}/preview should work with a name."""
        from orchestrator.api.routes.sessions import session_preview
        from orchestrator.state.repositories import sessions as repo

        repo.create_session(db, "preview-me", "localhost")

        with patch("orchestrator.api.routes.sessions._capture_preview", return_value="$ hello"):
            result = session_preview("preview-me", db=db)
        assert result["content"] == "$ hello"
