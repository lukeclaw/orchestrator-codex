"""Tests for RWS PTY routing in session API endpoints and brain sync.

Verifies that API endpoints correctly route to RWS daemon (via pty_input/pty_capture)
for remote sessions with rws_pty_id, and fall back to tmux for local/legacy sessions.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Patch path for get_remote_worker_server — the helpers import it locally,
# so we patch at the source module.
_RWS_GET = "orchestrator.terminal.remote_worker_server.get_remote_worker_server"


def _make_session(host="localhost", rws_pty_id=None, name="test-worker", status="working"):
    """Create a minimal session-like object for testing."""
    return SimpleNamespace(
        id="sess-123",
        name=name,
        host=host,
        rws_pty_id=rws_pty_id,
        status=status,
    )


# ---------------------------------------------------------------------------
# RemoteWorkerServer.write_to_pty / capture_pty
# ---------------------------------------------------------------------------


class TestRemoteWorkerServerClientMethods:
    """Test the new write_to_pty() and capture_pty() methods."""

    def test_write_to_pty_success(self):
        from orchestrator.terminal.remote_worker_server import RemoteWorkerServer

        rws = RemoteWorkerServer.__new__(RemoteWorkerServer)
        rws.host = "test-host"
        rws.execute = MagicMock(return_value={"status": "ok"})

        rws.write_to_pty("pty-abc", "hello\n")

        rws.execute.assert_called_once_with(
            {"action": "pty_input", "pty_id": "pty-abc", "data": "hello\n"}
        )

    def test_write_to_pty_error_raises(self):
        from orchestrator.terminal.remote_worker_server import RemoteWorkerServer

        rws = RemoteWorkerServer.__new__(RemoteWorkerServer)
        rws.host = "test-host"
        rws.execute = MagicMock(return_value={"error": "PTY not found"})

        with pytest.raises(RuntimeError, match="PTY input failed"):
            rws.write_to_pty("pty-abc", "hello\n")

    def test_capture_pty_success(self):
        from orchestrator.terminal.remote_worker_server import RemoteWorkerServer

        rws = RemoteWorkerServer.__new__(RemoteWorkerServer)
        rws.host = "test-host"
        rws.execute = MagicMock(return_value={"status": "ok", "output": "$ claude\n> Working..."})

        result = rws.capture_pty("pty-abc", lines=20)

        assert result == "$ claude\n> Working..."
        rws.execute.assert_called_once_with(
            {"action": "pty_capture", "pty_id": "pty-abc", "lines": 20}
        )

    def test_capture_pty_error_raises(self):
        from orchestrator.terminal.remote_worker_server import RemoteWorkerServer

        rws = RemoteWorkerServer.__new__(RemoteWorkerServer)
        rws.host = "test-host"
        rws.execute = MagicMock(return_value={"error": "PTY not found"})

        with pytest.raises(RuntimeError, match="PTY capture failed"):
            rws.capture_pty("pty-abc")

    def test_capture_pty_empty_output(self):
        from orchestrator.terminal.remote_worker_server import RemoteWorkerServer

        rws = RemoteWorkerServer.__new__(RemoteWorkerServer)
        rws.host = "test-host"
        rws.execute = MagicMock(return_value={"status": "ok"})

        result = rws.capture_pty("pty-abc")
        assert result == ""


# ---------------------------------------------------------------------------
# _write_to_rws_pty / _capture_rws_pty helpers
# ---------------------------------------------------------------------------


class TestWriteToRwsPty:
    """Test the _write_to_rws_pty helper in sessions.py."""

    def test_success(self):
        from orchestrator.api.routes.sessions import _write_to_rws_pty

        session = _make_session(host="user/rdev-vm", rws_pty_id="pty-abc")
        mock_rws = MagicMock()
        with patch(_RWS_GET, return_value=mock_rws):
            result = _write_to_rws_pty(session, "hello\n")

        assert result is True
        mock_rws.write_to_pty.assert_called_once_with("pty-abc", "hello\n")

    def test_failure_returns_false(self):
        from orchestrator.api.routes.sessions import _write_to_rws_pty

        session = _make_session(host="user/rdev-vm", rws_pty_id="pty-abc")
        mock_rws = MagicMock()
        mock_rws.write_to_pty.side_effect = RuntimeError("connection lost")
        with patch(_RWS_GET, return_value=mock_rws):
            result = _write_to_rws_pty(session, "hello\n")

        assert result is False


class TestCaptureRwsPty:
    """Test the _capture_rws_pty helper in sessions.py."""

    def test_success(self):
        from orchestrator.api.routes.sessions import _capture_rws_pty

        session = _make_session(host="user/rdev-vm", rws_pty_id="pty-abc")
        mock_rws = MagicMock()
        mock_rws.capture_pty.return_value = "terminal output"
        with patch(_RWS_GET, return_value=mock_rws):
            result = _capture_rws_pty(session, lines=20)

        assert result == "terminal output"
        mock_rws.capture_pty.assert_called_once_with("pty-abc", lines=20)

    def test_failure_returns_empty(self):
        from orchestrator.api.routes.sessions import _capture_rws_pty

        session = _make_session(host="user/rdev-vm", rws_pty_id="pty-abc")
        mock_rws = MagicMock()
        mock_rws.capture_pty.side_effect = RuntimeError("connection lost")
        with patch(_RWS_GET, return_value=mock_rws):
            result = _capture_rws_pty(session)

        assert result == ""


# ---------------------------------------------------------------------------
# _capture_preview routing
# ---------------------------------------------------------------------------


class TestCapturePreviewRouting:
    """Test that _capture_preview routes to RWS for remote+rws_pty_id sessions."""

    def test_rws_pty_session_uses_daemon(self):
        from orchestrator.api.routes.sessions import _capture_preview

        session = _make_session(host="user/rdev-vm", rws_pty_id="pty-abc")
        with patch(
            "orchestrator.api.routes.sessions._capture_rws_pty",
            return_value="rws output",
        ) as mock_cap:
            result = _capture_preview(session)

        assert result == "rws output"
        mock_cap.assert_called_once_with(session)

    def test_local_session_uses_tmux(self):
        from orchestrator.api.routes.sessions import _capture_preview

        session = _make_session(host="localhost")
        with (
            patch("orchestrator.api.routes.sessions.tmux_target", return_value=("s", "w")),
            patch(
                "orchestrator.api.routes.sessions.capture_pane_with_escapes",
                return_value="tmux output",
            ) as mock_cap,
        ):
            result = _capture_preview(session)

        assert result == "tmux output"
        mock_cap.assert_called_once_with("s", "w", lines=0)

    def test_legacy_remote_without_rws_pty_id_uses_tmux(self):
        from orchestrator.api.routes.sessions import _capture_preview

        session = _make_session(host="user/rdev-vm", rws_pty_id=None)
        with (
            patch("orchestrator.api.routes.sessions.tmux_target", return_value=("s", "w")),
            patch(
                "orchestrator.api.routes.sessions.capture_pane_with_escapes",
                return_value="tmux output",
            ),
        ):
            result = _capture_preview(session)

        assert result == "tmux output"

    def test_tmux_exception_returns_empty(self):
        from orchestrator.api.routes.sessions import _capture_preview

        session = _make_session(host="localhost")
        with (
            patch("orchestrator.api.routes.sessions.tmux_target", return_value=("s", "w")),
            patch(
                "orchestrator.api.routes.sessions.capture_pane_with_escapes",
                side_effect=Exception("tmux not running"),
            ),
        ):
            result = _capture_preview(session)

        assert result == ""


# ---------------------------------------------------------------------------
# Integration-style tests using FastAPI TestClient
# ---------------------------------------------------------------------------


@pytest.fixture
def rws_client():
    """Create a test client and a remote RWS PTY session (created via API)."""
    from fastapi.testclient import TestClient

    from orchestrator.api.app import create_app
    from orchestrator.state.db import get_memory_connection
    from orchestrator.state.migrations.runner import apply_migrations
    from orchestrator.state.repositories import sessions as sessions_repo

    conn = get_memory_connection()
    apply_migrations(conn)
    app = create_app(db=conn)

    with TestClient(app) as client:
        # Create session via API (after app lifespan has started)
        with patch(
            "orchestrator.api.routes.sessions.ensure_window",
            return_value="orchestrator:rws-test",
        ):
            with patch("orchestrator.api.routes.sessions.threading"):
                resp = client.post(
                    "/api/sessions",
                    json={"name": "rws-test", "host": "user/rdev-vm"},
                )
        sid = resp.json()["id"]

        # Set rws_pty_id and status directly in DB
        sessions_repo.update_session(conn, sid, rws_pty_id="pty-test-123", status="working")

        yield client, sid


class TestEndpointIntegration:
    """Test the full endpoint path with TestClient."""

    def test_send_uses_rws(self, rws_client):
        client, sid = rws_client
        mock_rws = MagicMock()
        with patch(_RWS_GET, return_value=mock_rws):
            resp = client.post(f"/api/sessions/{sid}/send", json={"message": "fix the bug"})

        assert resp.status_code == 200
        mock_rws.write_to_pty.assert_called_once_with("pty-test-123", "fix the bug\n")

    def test_type_uses_rws(self, rws_client):
        client, sid = rws_client
        mock_rws = MagicMock()
        with patch(_RWS_GET, return_value=mock_rws):
            resp = client.post(f"/api/sessions/{sid}/type", json={"text": "/tmp/img.png"})

        assert resp.status_code == 200
        mock_rws.write_to_pty.assert_called_once_with("pty-test-123", "/tmp/img.png")

    def test_paste_uses_bracketed_paste(self, rws_client):
        client, sid = rws_client
        text = "line1\nline2"
        mock_rws = MagicMock()
        with patch(_RWS_GET, return_value=mock_rws):
            resp = client.post(f"/api/sessions/{sid}/paste-to-pane", json={"text": text})

        assert resp.status_code == 200
        expected = f"\x1b[200~{text}\x1b[201~"
        mock_rws.write_to_pty.assert_called_once_with("pty-test-123", expected)

    def test_pause_sends_escape(self, rws_client):
        client, sid = rws_client
        mock_rws = MagicMock()
        with patch(_RWS_GET, return_value=mock_rws):
            resp = client.post(f"/api/sessions/{sid}/pause")

        assert resp.status_code == 200
        mock_rws.write_to_pty.assert_called_once_with("pty-test-123", "\x1b")

    def test_continue_sends_continue(self, rws_client):
        client, sid = rws_client
        mock_rws = MagicMock()
        with patch(_RWS_GET, return_value=mock_rws):
            resp = client.post(f"/api/sessions/{sid}/continue")

        assert resp.status_code == 200
        mock_rws.write_to_pty.assert_called_once_with("pty-test-123", "continue\n")

    def test_stop_sends_escape_and_clear(self, rws_client):
        client, sid = rws_client
        mock_rws = MagicMock()
        with patch(_RWS_GET, return_value=mock_rws):
            resp = client.post(f"/api/sessions/{sid}/stop")

        assert resp.status_code == 200
        calls = mock_rws.write_to_pty.call_args_list
        assert len(calls) == 2
        assert calls[0].args == ("pty-test-123", "\x1b")
        assert calls[1].args == ("pty-test-123", "/clear\n")

    def test_prepare_sends_escape_ctrlc_clear(self, rws_client):
        client, sid = rws_client
        mock_rws = MagicMock()
        with patch(_RWS_GET, return_value=mock_rws):
            resp = client.post(f"/api/sessions/{sid}/prepare-for-task")

        assert resp.status_code == 200
        calls = mock_rws.write_to_pty.call_args_list
        assert len(calls) == 3
        assert calls[0].args == ("pty-test-123", "\x1b")
        assert calls[1].args == ("pty-test-123", "\x03")
        assert calls[2].args == ("pty-test-123", "/clear\n")

    def test_preview_uses_rws(self, rws_client):
        client, sid = rws_client
        mock_rws = MagicMock()
        mock_rws.capture_pty.return_value = "Claude is working..."
        with patch(_RWS_GET, return_value=mock_rws):
            resp = client.get(f"/api/sessions/{sid}/preview")

        assert resp.status_code == 200
        assert resp.json()["content"] == "Claude is working..."
        mock_rws.capture_pty.assert_called_once_with("pty-test-123", lines=30)

    def test_list_sessions_with_preview_uses_rws(self, rws_client):
        client, sid = rws_client
        mock_rws = MagicMock()
        mock_rws.capture_pty.return_value = "live preview"
        with patch(_RWS_GET, return_value=mock_rws):
            resp = client.get("/api/sessions?include_preview=true")

        assert resp.status_code == 200
        sessions = resp.json()
        rws_session = next(s for s in sessions if s["id"] == sid)
        assert rws_session["preview"] == "live preview"
