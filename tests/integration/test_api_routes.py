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


# --- Health ---

class TestHealth:
    def test_health_check(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["sessions"]["total"] == 0


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


# --- Decisions ---

class TestDecisions:
    def test_create_and_respond(self, client):
        # Create
        resp = client.post("/api/decisions", json={
            "question": "Use Redis?", "options": ["Yes", "No"],
            "urgency": "high"
        })
        assert resp.status_code == 201
        did = resp.json()["id"]

        # List pending
        resp = client.get("/api/decisions/pending")
        assert len(resp.json()) == 1
        assert resp.json()[0]["urgency"] == "high"

        # Respond
        resp = client.post(f"/api/decisions/{did}/respond", json={
            "response": "Yes"
        })
        assert resp.json()["status"] == "responded"
        assert resp.json()["response"] == "Yes"

        # No more pending
        resp = client.get("/api/decisions/pending")
        assert len(resp.json()) == 0

    def test_dismiss_decision(self, client):
        create = client.post("/api/decisions", json={"question": "Ignore?"})
        did = create.json()["id"]
        resp = client.post(f"/api/decisions/{did}/dismiss")
        assert resp.json()["status"] == "dismissed"


# --- Reporting ---

class TestReporting:
    def test_report_event(self, client):
        with patch("orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:rep-worker"):
            client.post("/api/sessions", json={"name": "rep-worker", "host": "h"})
        resp = client.post("/api/report", json={
            "session": "rep-worker", "event": "task_progress",
            "data": {"task": "Feature X", "progress": 50}
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_request_decision_via_reporting(self, client):
        with patch("orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:dec-worker"):
            client.post("/api/sessions", json={"name": "dec-worker", "host": "h"})
        resp = client.post("/api/decision", json={
            "session": "dec-worker", "question": "Which DB?",
            "options": ["PG", "MySQL"]
        })
        assert resp.json()["ok"] is True

        pending = client.get("/api/decisions/pending").json()
        assert len(pending) == 1
        assert pending[0]["question"] == "Which DB?"

    def test_get_guidance(self, client):
        with patch("orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:guide-worker"):
            client.post("/api/sessions", json={"name": "guide-worker", "host": "h"})
        resp = client.get("/api/guidance?session=guide-worker")
        assert resp.status_code == 200
        assert resp.json()["session"] == "guide-worker"


# --- Chat ---

class TestChat:
    def test_chat_placeholder(self, client):
        # Mock the key so we always test the fallback path
        with patch("orchestrator.api.routes.chat.get_validated_key", return_value=None):
            resp = client.post("/api/chat", json={"message": "What's happening?"})
        assert resp.status_code == 200
        data = resp.json()
        assert "response" in data
        assert "What's happening?" in data["response"]

    def test_chat_status(self, client):
        resp = client.get("/api/chat/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "key_found" in data


# --- Dashboard ---

class TestDashboard:
    def test_dashboard_loads(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Claude Orchestrator" in resp.text
