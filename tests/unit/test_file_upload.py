"""Unit tests for file upload endpoints (sessions + brain)."""

import base64
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from orchestrator.api.app import create_app
from orchestrator.state.db import get_memory_connection
from orchestrator.state.migrations.runner import apply_migrations

SAMPLE_CONTENT = b"print('hello world')\n"
SAMPLE_B64 = base64.b64encode(SAMPLE_CONTENT).decode()


@pytest.fixture
def client():
    conn = get_memory_connection()
    apply_migrations(conn)
    app = create_app(db=conn, test_mode=True)
    with TestClient(app) as c:
        yield c


def _create_session(client, name="test-worker", host="localhost"):
    """Helper: create a session and return its ID."""
    with patch(
        "orchestrator.api.routes.sessions.ensure_window",
        return_value=f"orchestrator:{name}",
    ):
        # rdev/ssh hosts trigger a background thread — mock it
        with patch("orchestrator.api.routes.sessions.threading"):
            resp = client.post("/api/sessions", json={"name": name, "host": host})
    assert resp.status_code == 201
    return resp.json()["id"]


class TestSessionUploadFile:
    def test_upload_supported_file(self, client, tmp_path):
        sid = _create_session(client)
        with patch(
            "orchestrator.terminal.file_sync.get_worker_tmp_dir",
            return_value=str(tmp_path),
        ):
            resp = client.post(
                f"/api/sessions/{sid}/upload-file",
                json={"file_data": SAMPLE_B64, "filename": "main.py"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["filename"] == "main.py"
        assert data["size"] == len(SAMPLE_CONTENT)

    def test_unsupported_file_rejected(self, client):
        sid = _create_session(client)
        resp = client.post(
            f"/api/sessions/{sid}/upload-file",
            json={"file_data": SAMPLE_B64, "filename": "malware.exe"},
        )
        assert resp.status_code == 415

    def test_session_not_found(self, client):
        resp = client.post(
            "/api/sessions/nonexistent/upload-file",
            json={"file_data": SAMPLE_B64, "filename": "test.py"},
        )
        assert resp.status_code == 404

    def test_invalid_base64(self, client):
        sid = _create_session(client)
        resp = client.post(
            f"/api/sessions/{sid}/upload-file",
            json={"file_data": "not-valid-base64!!!", "filename": "test.py"},
        )
        assert resp.status_code == 400

    def test_oversized_file_rejected(self, client):
        sid = _create_session(client)
        with patch("orchestrator.api.upload_utils.MAX_FILE_SIZE", 10):
            resp = client.post(
                f"/api/sessions/{sid}/upload-file",
                json={"file_data": SAMPLE_B64, "filename": "test.py"},
            )
        assert resp.status_code == 413

    def test_filename_sanitized(self, client, tmp_path):
        sid = _create_session(client)
        with patch(
            "orchestrator.terminal.file_sync.get_worker_tmp_dir",
            return_value=str(tmp_path),
        ):
            resp = client.post(
                f"/api/sessions/{sid}/upload-file",
                json={"file_data": SAMPLE_B64, "filename": "../../etc/passwd.py"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "passwd.py"

    def test_remote_sync_called(self, client, tmp_path):
        sid = _create_session(client, host="user/rdev-host")
        with (
            patch(
                "orchestrator.terminal.file_sync.get_worker_tmp_dir",
                return_value=str(tmp_path),
            ),
            patch(
                "orchestrator.terminal.file_sync.sync_file_to_remote",
                return_value=True,
            ) as mock_sync,
        ):
            resp = client.post(
                f"/api/sessions/{sid}/upload-file",
                json={"file_data": SAMPLE_B64, "filename": "test.py"},
            )
        assert resp.status_code == 200
        mock_sync.assert_called_once()

    def test_remote_sync_failure(self, client, tmp_path):
        sid = _create_session(client, host="user/rdev-host")
        with (
            patch(
                "orchestrator.terminal.file_sync.get_worker_tmp_dir",
                return_value=str(tmp_path),
            ),
            patch(
                "orchestrator.terminal.file_sync.sync_file_to_remote",
                return_value=False,
            ),
        ):
            resp = client.post(
                f"/api/sessions/{sid}/upload-file",
                json={"file_data": SAMPLE_B64, "filename": "test.py"},
            )
        assert resp.status_code == 502

    def test_dotfile_accepted(self, client, tmp_path):
        sid = _create_session(client)
        with patch(
            "orchestrator.terminal.file_sync.get_worker_tmp_dir",
            return_value=str(tmp_path),
        ):
            resp = client.post(
                f"/api/sessions/{sid}/upload-file",
                json={"file_data": SAMPLE_B64, "filename": ".gitignore"},
            )
        assert resp.status_code == 200

    def test_extensionless_accepted(self, client, tmp_path):
        sid = _create_session(client)
        with patch(
            "orchestrator.terminal.file_sync.get_worker_tmp_dir",
            return_value=str(tmp_path),
        ):
            resp = client.post(
                f"/api/sessions/{sid}/upload-file",
                json={"file_data": SAMPLE_B64, "filename": "Dockerfile"},
            )
        assert resp.status_code == 200


class TestBrainUploadFile:
    def _start_brain(self, client):
        """Start the brain so upload endpoint works."""
        with patch("orchestrator.api.routes.brain.tmux") as mock_tmux:
            mock_tmux.TMUX_SESSION = "orchestrator"
            mock_tmux.pane_foreground_command.return_value = None
            mock_tmux.ensure_window.return_value = "orchestrator:brain"
            mock_tmux.dismiss_trust_prompt.return_value = None
            with (
                patch("orchestrator.agents.deploy.deploy_brain_tmp_contents"),
                patch("orchestrator.api.routes.brain.get_path_export_command", return_value=""),
                patch(
                    "orchestrator.terminal.claude_update.should_update_before_start",
                    return_value=False,
                ),
            ):
                resp = client.post("/api/brain/start")
        assert resp.status_code == 200
        return resp.json()["session_id"]

    def test_upload_supported_file(self, client, tmp_path):
        self._start_brain(client)
        with patch(
            "orchestrator.api.upload_utils.save_uploaded_file",
            return_value=str(tmp_path / "test.py"),
        ):
            resp = client.post(
                "/api/brain/upload-file",
                json={"file_data": SAMPLE_B64, "filename": "test.py"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    def test_unsupported_file_rejected(self, client):
        self._start_brain(client)
        resp = client.post(
            "/api/brain/upload-file",
            json={"file_data": SAMPLE_B64, "filename": "virus.exe"},
        )
        assert resp.status_code == 415

    def test_brain_not_running(self, client):
        resp = client.post(
            "/api/brain/upload-file",
            json={"file_data": SAMPLE_B64, "filename": "test.py"},
        )
        assert resp.status_code == 400

    def test_oversized_file_rejected(self, client):
        self._start_brain(client)
        with patch("orchestrator.api.upload_utils.MAX_FILE_SIZE", 10):
            resp = client.post(
                "/api/brain/upload-file",
                json={"file_data": SAMPLE_B64, "filename": "test.py"},
            )
        assert resp.status_code == 413
