"""Integration tests for all API endpoints."""

from unittest.mock import patch

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
        with patch("orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:worker-1"):
            resp = client.post("/api/sessions", json={
                "name": "worker-1", "host": "rdev1.example.com"
            })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "worker-1"
        assert data["status"] == "idle"

    def test_get_session(self, client):
        with patch("orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:w1"):
            create = client.post("/api/sessions", json={"name": "w1", "host": "h1"})
        sid = create.json()["id"]
        resp = client.get(f"/api/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "w1"

    def test_get_session_not_found(self, client):
        resp = client.get("/api/sessions/nonexistent")
        assert resp.status_code == 404

    def test_update_session(self, client):
        with patch("orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:w2"):
            create = client.post("/api/sessions", json={"name": "w2", "host": "h2"})
        sid = create.json()["id"]
        resp = client.patch(f"/api/sessions/{sid}", json={"status": "working"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "working"

    def test_delete_session(self, client):
        with patch("orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:del"):
            create = client.post("/api/sessions", json={"name": "del", "host": "h"})
        sid = create.json()["id"]
        resp = client.delete(f"/api/sessions/{sid}")
        assert resp.status_code == 200
        assert client.get(f"/api/sessions/{sid}").status_code == 404

    def test_create_rdev_session(self, client):
        """Creating a session with rdev host returns 'connecting' and starts background setup."""
        with patch("orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:rdev-w1"):
            with patch("orchestrator.api.routes.sessions.threading") as mock_thread:
                resp = client.post("/api/sessions", json={
                    "name": "rdev-w1", "host": "subs-mt/sleepy-franklin"
                })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "rdev-w1"
        assert data["status"] == "connecting"
        mock_thread.Thread.assert_called_once()
        mock_thread.Thread.return_value.start.assert_called_once()

    def test_create_local_session_unchanged(self, client):
        """Creating a session with a non-rdev host still uses the old path."""
        with patch("orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:local-w1"):
            resp = client.post("/api/sessions", json={
                "name": "local-w1", "host": "localhost"
            })
        assert resp.status_code == 201
        assert resp.json()["status"] == "idle"

    def test_session_serialization_includes_tunnel_pane(self, client):
        """GET /sessions should include tunnel_pane field."""
        with patch("orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:tp1"):
            create = client.post("/api/sessions", json={"name": "tp1", "host": "localhost"})
        sid = create.json()["id"]
        resp = client.get(f"/api/sessions/{sid}")
        assert "tunnel_pane" in resp.json()
        assert resp.json()["tunnel_pane"] is None

    def test_delete_rdev_session_kills_tunnel(self, client):
        """Deleting an rdev session also kills the tunnel window."""
        with patch("orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:rdev-del"):
            with patch("orchestrator.api.routes.sessions.threading"):
                resp = client.post("/api/sessions", json={
                    "name": "rdev-del", "host": "subs-mt/test"
                })
        sid = resp.json()["id"]
        # Manually set tunnel_pane in DB
        conn = client.app.state.conn
        conn.execute("UPDATE sessions SET tunnel_pane = ? WHERE id = ?", ("orchestrator:rdev-del-tunnel", sid))
        conn.commit()

        with patch("orchestrator.api.routes.sessions.kill_window") as mock_kill:
            resp = client.delete(f"/api/sessions/{sid}")
        assert resp.status_code == 200
        # kill_window should have been called for the tunnel
        calls = [str(c) for c in mock_kill.call_args_list]
        assert any("rdev-del-tunnel" in c for c in calls)


# --- Projects ---

class TestProjects:
    def test_crud(self, client):
        # Create
        resp = client.post("/api/projects", json={
            "name": "Test Project", "description": "Test"
        })
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
        resp = client.post("/api/tasks", json={
            "project_id": proj["id"], "title": "Do something"
        })
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
                mock_tmux.ensure_window.return_value = "orchestrator:brain"
                mock_tmux.send_keys.return_value = True
                client.post("/api/brain/start")
                resp = client.post("/api/brain/start")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Brain already running"

    def test_brain_start_after_stopped(self, client):
        """Restarting brain after stop reuses the existing session record."""
        with patch("orchestrator.api.routes.brain.time.sleep"):  # Speed up test
            with patch("orchestrator.api.routes.brain.tmux") as mock_tmux:
                mock_tmux.ensure_window.return_value = "orchestrator:brain"
                mock_tmux.send_keys.return_value = True
                first = client.post("/api/brain/start")
            first_id = first.json()["session_id"]

            with patch("orchestrator.api.routes.brain.tmux") as mock_tmux:
                mock_tmux.send_keys.return_value = True
                client.post("/api/brain/stop")

            with patch("orchestrator.api.routes.brain.tmux") as mock_tmux:
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
            mock_tmux.ensure_window.side_effect = RuntimeError("tmux not available")
            resp = client.post("/api/brain/start")
        assert resp.status_code == 500
        assert "Failed to start brain" in resp.json()["detail"]

    def test_brain_session_appears_in_sessions_list(self, client):
        """Brain session should appear in GET /api/sessions with all fields."""
        with patch("orchestrator.api.routes.brain.time.sleep"):  # Speed up test
            with patch("orchestrator.api.routes.brain.tmux") as mock_tmux:
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
        assert "tunnel_pane" in s
        assert s["tunnel_pane"] is None

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
        for path in ["/workers", "/workers/abc-123", "/projects", "/projects/xyz",
                     "/context", "/settings"]:
            resp = client.get(path)
            assert resp.status_code == 200, f"GET {path} returned {resp.status_code}"

    def test_api_routes_not_caught_by_catchall(self, client):
        """API endpoints should return JSON, not the SPA HTML."""
        resp = client.get("/api/brain/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data
