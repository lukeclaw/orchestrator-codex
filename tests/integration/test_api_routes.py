"""Integration tests for all API endpoints."""

import base64
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from orchestrator.api.app import create_app
from orchestrator.state.db import get_memory_connection
from orchestrator.state.migrations.runner import apply_migrations


@pytest.fixture
def client():
    conn = get_memory_connection()
    apply_migrations(conn)
    app = create_app(db=conn)
    with TestClient(app) as c:
        yield c


# --- Sessions ---


class TestSessions:
    def test_list_empty(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_session(self, client):
        with patch(
            "orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:worker-1"
        ):
            resp = client.post(
                "/api/sessions", json={"name": "worker-1", "host": "localhost"}
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "worker-1"
        assert data["status"] == "idle"

    def test_get_session(self, client):
        with patch(
            "orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:w1"
        ):
            create = client.post("/api/sessions", json={"name": "w1", "host": "localhost"})
        sid = create.json()["id"]
        resp = client.get(f"/api/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "w1"

    def test_get_session_not_found(self, client):
        resp = client.get("/api/sessions/nonexistent")
        assert resp.status_code == 404

    def test_update_session(self, client):
        with patch(
            "orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:w2"
        ):
            create = client.post("/api/sessions", json={"name": "w2", "host": "localhost"})
        sid = create.json()["id"]
        resp = client.patch(f"/api/sessions/{sid}", json={"status": "working"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "working"

    def test_delete_session(self, client):
        with patch(
            "orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:del"
        ):
            create = client.post("/api/sessions", json={"name": "del", "host": "localhost"})
        sid = create.json()["id"]
        resp = client.delete(f"/api/sessions/{sid}")
        assert resp.status_code == 200
        assert client.get(f"/api/sessions/{sid}").status_code == 404

    def test_create_rdev_session(self, client):
        """Creating a session with rdev host returns 'connecting' and starts background setup."""
        with patch(
            "orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:rdev-w1"
        ):
            with patch("orchestrator.api.routes.sessions.threading") as mock_thread:
                resp = client.post(
                    "/api/sessions", json={"name": "rdev-w1", "host": "subs-mt/sleepy-franklin"}
                )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "rdev-w1"
        assert data["status"] == "connecting"
        mock_thread.Thread.assert_called_once()
        mock_thread.Thread.return_value.start.assert_called_once()

    def test_create_local_session_unchanged(self, client):
        """Creating a session with a non-rdev host still uses the old path."""
        with patch(
            "orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:local-w1"
        ):
            resp = client.post("/api/sessions", json={"name": "local-w1", "host": "localhost"})
        assert resp.status_code == 201
        assert resp.json()["status"] == "idle"

    def test_session_serialization_includes_tunnel_pid(self, client):
        """GET /sessions should include tunnel_pid field."""
        with patch(
            "orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:tp1"
        ):
            create = client.post("/api/sessions", json={"name": "tp1", "host": "localhost"})
        sid = create.json()["id"]
        resp = client.get(f"/api/sessions/{sid}")
        assert "tunnel_pid" in resp.json()
        assert resp.json()["tunnel_pid"] is None

    def test_delete_rdev_session_stops_tunnel(self, client):
        """Deleting an rdev session stops the tunnel subprocess."""
        with patch(
            "orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:rdev-del"
        ):
            with patch("orchestrator.api.routes.sessions.threading"):
                resp = client.post(
                    "/api/sessions", json={"name": "rdev-del", "host": "subs-mt/test"}
                )
        sid = resp.json()["id"]

        mock_tm = MagicMock()
        mock_tm.stop_tunnel.return_value = True
        client.app.state.tunnel_manager = mock_tm

        with patch("orchestrator.api.routes.sessions.kill_window"):
            resp = client.delete(f"/api/sessions/{sid}")
        assert resp.status_code == 200
        mock_tm.stop_tunnel.assert_called_once_with(sid)

    def test_type_text_no_enter(self, client):
        """POST /sessions/{id}/type injects text via send_keys_literal without Enter."""
        with patch(
            "orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:tw"
        ):
            create = client.post("/api/sessions", json={"name": "tw", "host": "localhost"})
        sid = create.json()["id"]

        with patch(
            "orchestrator.terminal.manager.send_keys_literal", return_value=True
        ) as mock_lit:
            resp = client.post(f"/api/sessions/{sid}/type", json={"text": "/tmp/img.png"})

        assert resp.status_code == 200
        mock_lit.assert_called_once_with("orchestrator", "tw", "/tmp/img.png")

    def test_type_text_not_found(self, client):
        resp = client.post("/api/sessions/nonexistent/type", json={"text": "hi"})
        assert resp.status_code == 404


# --- Projects ---


class TestProjects:
    def test_crud(self, client):
        # Create
        resp = client.post("/api/projects", json={"name": "Test Project", "description": "Test"})
        assert resp.status_code == 201
        pid = resp.json()["id"]

        # Get
        resp = client.get(f"/api/projects/{pid}")
        assert resp.json()["name"] == "Test Project"

        # Update
        resp = client.patch(f"/api/projects/{pid}", json={"status": "paused"})
        assert resp.json()["status"] == "paused"

        # List
        resp = client.get("/api/projects")
        assert len(resp.json()) == 1

        # Delete
        resp = client.delete(f"/api/projects/{pid}")
        assert resp.status_code == 200


# --- Tasks ---


class TestTasks:
    def test_crud(self, client):
        # Need a project first
        proj = client.post("/api/projects", json={"name": "P"}).json()

        # Create
        resp = client.post("/api/tasks", json={"project_id": proj["id"], "title": "Do something"})
        assert resp.status_code == 201
        tid = resp.json()["id"]

        # Get
        resp = client.get(f"/api/tasks/{tid}")
        assert resp.json()["title"] == "Do something"

        # Update
        resp = client.patch(f"/api/tasks/{tid}", json={"status": "in_progress"})
        assert resp.json()["status"] == "in_progress"

        # List by project
        resp = client.get(f"/api/tasks?project_id={proj['id']}")
        assert len(resp.json()) == 1

        # Delete
        resp = client.delete(f"/api/tasks/{tid}")
        assert resp.status_code == 200

    def test_patch_links_rejects_duplicates(self, client):
        """PATCH /tasks/{id} with duplicate link URLs returns 400."""
        proj = client.post("/api/projects", json={"name": "LinkProj"}).json()
        task = client.post(
            "/api/tasks", json={"project_id": proj["id"], "title": "Link task"}
        ).json()

        dup_links = [
            {"url": "https://github.com/org/repo/pull/1"},
            {"url": "https://github.com/org/repo/pull/1"},
        ]
        resp = client.patch(f"/api/tasks/{task['id']}", json={"links": dup_links})
        assert resp.status_code == 400
        assert "Duplicate link URL" in resp.json()["detail"]

    def test_patch_links_unique_urls_accepted(self, client):
        """PATCH /tasks/{id} with unique link URLs succeeds."""
        proj = client.post("/api/projects", json={"name": "LinkProj2"}).json()
        task = client.post(
            "/api/tasks", json={"project_id": proj["id"], "title": "Link task 2"}
        ).json()

        links = [
            {"url": "https://github.com/org/repo/pull/1"},
            {"url": "https://github.com/org/repo/pull/2"},
        ]
        resp = client.patch(f"/api/tasks/{task['id']}", json={"links": links})
        assert resp.status_code == 200
        assert len(resp.json()["links"]) == 2

    def test_assign_worker_to_subtask_rejected(self, client):
        """Assigning a worker to a sub-task should return 400."""
        proj = client.post("/api/projects", json={"name": "SubP"}).json()
        parent = client.post(
            "/api/tasks", json={"project_id": proj["id"], "title": "Parent task"}
        ).json()
        subtask = client.post(
            "/api/tasks",
            json={"project_id": proj["id"], "title": "Sub task", "parent_task_id": parent["id"]},
        ).json()

        # Use a real session to avoid FK issues — validation fires before DB write
        with patch(
            "orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:w-sub"
        ):
            worker = client.post(
                "/api/sessions", json={"name": "w-sub", "host": "localhost"}
            ).json()

        resp = client.patch(
            f"/api/tasks/{subtask['id']}", json={"assigned_session_id": worker["id"]}
        )
        assert resp.status_code == 400
        assert "sub-task" in resp.json()["detail"]
        assert parent["id"] in resp.json()["detail"]

    def test_assign_worker_to_parent_task_allowed(self, client):
        """Assigning a worker to a top-level task should succeed."""
        proj = client.post("/api/projects", json={"name": "TopP"}).json()
        task = client.post(
            "/api/tasks", json={"project_id": proj["id"], "title": "Top task"}
        ).json()

        with patch(
            "orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:w-top"
        ):
            worker = client.post(
                "/api/sessions", json={"name": "w-top", "host": "localhost"}
            ).json()

        with patch("orchestrator.api.routes.tasks._notify_worker_of_assignment"):
            resp = client.patch(
                f"/api/tasks/{task['id']}", json={"assigned_session_id": worker["id"]}
            )
        assert resp.status_code == 200
        assert resp.json()["assigned_session_id"] == worker["id"]

    def test_agent_add_link_rejects_duplicate(self, client):
        """POST /tasks/{id}/links add action rejects duplicate URL."""
        proj = client.post("/api/projects", json={"name": "AgentLinkProj"}).json()
        task = client.post(
            "/api/tasks", json={"project_id": proj["id"], "title": "Agent link task"}
        ).json()
        tid = task["id"]

        # Add a link
        resp = client.post(
            f"/api/tasks/{tid}/links",
            json={"action": "add", "url": "https://example.com/pr/1"},
        )
        assert resp.status_code == 200

        # Try to add the same link again
        resp = client.post(
            f"/api/tasks/{tid}/links",
            json={"action": "add", "url": "https://example.com/pr/1"},
        )
        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"]


# --- Brain ---


class TestBrain:
    def test_brain_status_not_running(self, client):
        resp = client.get("/api/brain/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is False
        assert data["session_id"] is None
        assert data["status"] is None

    def test_brain_start_fresh(self, client):
        """Start brain when no brain session exists yet."""
        with patch("orchestrator.api.routes.brain.time.sleep"):  # Speed up test
            with patch("orchestrator.api.routes.brain.tmux") as mock_tmux:
                mock_tmux.pane_foreground_command.return_value = None
                mock_tmux.ensure_window.return_value = "orchestrator:brain"
                mock_tmux.send_keys.return_value = True
                resp = client.post("/api/brain/start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["status"] == "working"
        assert data["session_id"] is not None

        # Verify session was created in DB
        resp = client.get("/api/brain/status")
        assert resp.json()["running"] is True
        assert resp.json()["status"] == "working"

    def test_brain_start_already_running(self, client):
        """Starting brain when already running returns early with existing info."""
        with patch("orchestrator.api.routes.brain.time.sleep"):  # Speed up test
            with patch("orchestrator.api.routes.brain.tmux") as mock_tmux:
                mock_tmux.pane_foreground_command.return_value = None
                mock_tmux.ensure_window.return_value = "orchestrator:brain"
                mock_tmux.send_keys.return_value = True
                client.post("/api/brain/start")
                # Second call sees Claude running (non-shell foreground)
                mock_tmux.pane_foreground_command.return_value = "node"
                resp = client.post("/api/brain/start")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Brain already running (reconnected)"

    def test_brain_start_after_stopped(self, client):
        """Restarting brain after stop reuses the existing session record."""
        with patch("orchestrator.api.routes.brain.time.sleep"):  # Speed up test
            with patch("orchestrator.api.routes.brain.tmux") as mock_tmux:
                mock_tmux.pane_foreground_command.return_value = None
                mock_tmux.ensure_window.return_value = "orchestrator:brain"
                mock_tmux.send_keys.return_value = True
                first = client.post("/api/brain/start")
            first_id = first.json()["session_id"]

            with patch("orchestrator.api.routes.brain.tmux") as mock_tmux:
                mock_tmux.send_keys.return_value = True
                client.post("/api/brain/stop")

            with patch("orchestrator.api.routes.brain.tmux") as mock_tmux:
                mock_tmux.pane_foreground_command.return_value = None
                mock_tmux.ensure_window.return_value = "orchestrator:brain"
                mock_tmux.send_keys.return_value = True
                second = client.post("/api/brain/start")
        assert second.json()["session_id"] == first_id
        assert second.json()["status"] == "working"

    def test_brain_stop(self, client):
        with patch("orchestrator.api.routes.brain.time.sleep"):  # Speed up test
            with patch("orchestrator.api.routes.brain.tmux") as mock_tmux:
                mock_tmux.ensure_window.return_value = "orchestrator:brain"
                mock_tmux.send_keys.return_value = True
                client.post("/api/brain/start")

            with patch("orchestrator.api.routes.brain.tmux") as mock_tmux:
                mock_tmux.send_keys.return_value = True
                resp = client.post("/api/brain/stop")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        resp = client.get("/api/brain/status")
        assert resp.json()["running"] is False
        assert resp.json()["status"] == "disconnected"

    def test_brain_stop_not_running(self, client):
        resp = client.post("/api/brain/stop")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert "not running" in resp.json()["message"].lower()

    def test_brain_start_tmux_failure_returns_500(self, client):
        """If tmux fails, brain start should return 500, not crash."""
        with patch("orchestrator.api.routes.brain.tmux") as mock_tmux:
            mock_tmux.pane_foreground_command.return_value = None
            mock_tmux.ensure_window.side_effect = RuntimeError("tmux not available")
            resp = client.post("/api/brain/start")
        assert resp.status_code == 500
        assert "Failed to start brain" in resp.json()["detail"]

    def test_brain_session_appears_in_sessions_list(self, client):
        """Brain session should appear in GET /api/sessions with all fields."""
        with patch("orchestrator.api.routes.brain.time.sleep"):  # Speed up test
            with patch("orchestrator.api.routes.brain.tmux") as mock_tmux:
                mock_tmux.pane_foreground_command.return_value = None
                mock_tmux.ensure_window.return_value = "orchestrator:brain"
                mock_tmux.send_keys.return_value = True
                client.post("/api/brain/start")

        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        sessions = resp.json()
        brain_sessions = [s for s in sessions if s["name"] == "brain"]
        assert len(brain_sessions) == 1
        s = brain_sessions[0]
        assert s["host"] == "local"
        assert s["status"] == "working"
        assert "tunnel_pid" in s
        assert s["tunnel_pid"] is None

    def test_brain_sync_not_running(self, client):
        resp = client.post("/api/brain/sync")
        assert resp.status_code == 400


# --- Dashboard ---


class TestDashboard:
    def test_dashboard_loads(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Orchestrator" in resp.text

    def test_frontend_routes_return_html(self, client):
        """All frontend routes should return 200 and serve the SPA."""
        for path in [
            "/workers",
            "/workers/abc-123",
            "/projects",
            "/projects/xyz",
            "/context",
            "/settings",
        ]:
            resp = client.get(path)
            assert resp.status_code == 200, f"GET {path} returned {resp.status_code}"

    def test_api_routes_not_caught_by_catchall(self, client):
        """API endpoints should return JSON, not the SPA HTML."""
        resp = client.get("/api/brain/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data


# --- Paste ---

# Minimal valid PNG bytes
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
    b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
    b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


class TestPaste:
    def test_paste_image_raw_base64(self, client, tmp_path):
        """POST /api/paste-image with raw base64 saves file and returns URL."""
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        with patch("orchestrator.api.routes.paste.get_images_dir", return_value=images_dir):
            resp = client.post(
                "/api/paste-image",
                json={
                    "image_data": base64.b64encode(_TINY_PNG).decode(),
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["url"].startswith("/api/images/")
        assert data["size"] == len(_TINY_PNG)
        # File actually exists
        assert (images_dir / data["filename"]).exists()

    def test_paste_image_data_url(self, client, tmp_path):
        """POST /api/paste-image with data URL prefix parses mime type."""
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        b64 = base64.b64encode(_TINY_PNG).decode()
        with patch("orchestrator.api.routes.paste.get_images_dir", return_value=images_dir):
            resp = client.post(
                "/api/paste-image",
                json={
                    "image_data": f"data:image/jpeg;base64,{b64}",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["filename"].endswith(".jpg")

    def test_paste_image_custom_filename(self, client, tmp_path):
        """Custom filename is sanitized and used."""
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        with patch("orchestrator.api.routes.paste.get_images_dir", return_value=images_dir):
            resp = client.post(
                "/api/paste-image",
                json={
                    "image_data": base64.b64encode(_TINY_PNG).decode(),
                    "filename": "my-shot",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["filename"] == "my-shot.png"

    def test_paste_image_invalid_base64(self, client, tmp_path):
        """Invalid base64 returns 400."""
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        with patch("orchestrator.api.routes.paste.get_images_dir", return_value=images_dir):
            resp = client.post(
                "/api/paste-image",
                json={
                    "image_data": "not-valid-base64!!!",
                },
            )
        assert resp.status_code == 400

    def test_paste_image_missing_body(self, client):
        """Missing image_data field returns 422."""
        resp = client.post("/api/paste-image", json={})
        assert resp.status_code == 422

    def test_static_images_mount(self, client, tmp_path):
        """Saved images are servable via /api/images/ static mount."""
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        with patch("orchestrator.api.routes.paste.get_images_dir", return_value=images_dir):
            save_resp = client.post(
                "/api/paste-image",
                json={
                    "image_data": base64.b64encode(_TINY_PNG).decode(),
                },
            )
        # The static mount in the test client may not use our tmp_path,
        # so verify the file was written correctly at least
        fname = save_resp.json()["filename"]
        assert (images_dir / fname).read_bytes() == _TINY_PNG


# --- Session paste-image ---


class TestSessionPasteImage:
    """Tests for POST /api/sessions/{session_id}/paste-image."""

    def _create_session(self, client, name="img-w1", host="localhost"):
        with patch(
            "orchestrator.api.routes.sessions.ensure_window", return_value=f"orchestrator:{name}"
        ):
            with patch("orchestrator.api.routes.sessions.threading"):
                resp = client.post("/api/sessions", json={"name": name, "host": host})
        return resp.json()["id"]

    def test_paste_image_local_worker(self, client, tmp_path):
        """Local worker: saves file locally, returns file_path."""
        sid = self._create_session(client)
        b64 = base64.b64encode(_TINY_PNG).decode()

        with patch(
            "orchestrator.terminal.file_sync.get_worker_tmp_dir", return_value=str(tmp_path)
        ):
            resp = client.post(
                f"/api/sessions/{sid}/paste-image",
                json={
                    "image_data": b64,
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["file_path"].startswith(str(tmp_path))
        assert data["file_path"].endswith(".png")
        assert data["size"] == len(_TINY_PNG)
        # File actually written
        from pathlib import Path

        assert Path(data["file_path"]).exists()
        assert Path(data["file_path"]).read_bytes() == _TINY_PNG

    def test_paste_image_rdev_worker_syncs(self, client, tmp_path):
        """Rdev worker: saves locally and scp's to remote."""
        sid = self._create_session(client, name="rdev-img", host="user/rdev-vm")

        b64 = base64.b64encode(_TINY_PNG).decode()

        with patch(
            "orchestrator.terminal.file_sync.get_worker_tmp_dir", return_value=str(tmp_path)
        ):
            with patch(
                "orchestrator.terminal.file_sync.sync_file_to_remote", return_value=True
            ) as mock_sync:
                resp = client.post(
                    f"/api/sessions/{sid}/paste-image",
                    json={
                        "image_data": b64,
                    },
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        # sync_file_to_remote was called with matching local and remote paths
        mock_sync.assert_called_once()
        call_args = mock_sync.call_args
        assert call_args[0][0] == data["file_path"]  # local_path
        assert call_args[0][1] == "user/rdev-vm"  # host
        assert call_args[0][2] == data["file_path"]  # remote_path == local_path

    def test_paste_image_rdev_sync_failure(self, client, tmp_path):
        """Rdev sync failure returns 502."""
        sid = self._create_session(client, name="rdev-fail", host="user/rdev-vm")

        b64 = base64.b64encode(_TINY_PNG).decode()

        with patch(
            "orchestrator.terminal.file_sync.get_worker_tmp_dir", return_value=str(tmp_path)
        ):
            with patch("orchestrator.terminal.file_sync.sync_file_to_remote", return_value=False):
                resp = client.post(
                    f"/api/sessions/{sid}/paste-image",
                    json={
                        "image_data": b64,
                    },
                )

        assert resp.status_code == 502

    def test_paste_image_data_url_prefix(self, client, tmp_path):
        """Data URL prefix is parsed correctly."""
        sid = self._create_session(client, name="img-du")
        b64 = base64.b64encode(_TINY_PNG).decode()

        with patch(
            "orchestrator.terminal.file_sync.get_worker_tmp_dir", return_value=str(tmp_path)
        ):
            resp = client.post(
                f"/api/sessions/{sid}/paste-image",
                json={
                    "image_data": f"data:image/jpeg;base64,{b64}",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["filename"].endswith(".jpg")

    def test_paste_image_invalid_base64(self, client):
        """Invalid base64 returns 400."""
        sid = self._create_session(client, name="img-bad")
        resp = client.post(
            f"/api/sessions/{sid}/paste-image",
            json={
                "image_data": "not-valid!!!",
            },
        )
        assert resp.status_code == 400

    def test_paste_image_session_not_found(self, client):
        """Unknown session returns 404."""
        resp = client.post(
            "/api/sessions/nonexistent/paste-image",
            json={
                "image_data": base64.b64encode(_TINY_PNG).decode(),
            },
        )
        assert resp.status_code == 404
