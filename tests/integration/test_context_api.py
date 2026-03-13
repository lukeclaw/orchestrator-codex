"""Integration tests for context API endpoints."""

import pytest
from fastapi.testclient import TestClient

from orchestrator.api.app import create_app
from orchestrator.state.db import get_memory_connection
from orchestrator.state.migrations.runner import apply_migrations


@pytest.fixture
def client():
    conn = get_memory_connection()
    apply_migrations(conn)
    app = create_app(db=conn, test_mode=True)
    with TestClient(app) as c:
        yield c


class TestContextAPI:
    def test_list_empty(self, client):
        resp = client.get("/api/context")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_global(self, client):
        resp = client.post(
            "/api/context",
            json={
                "title": "Test item",
                "content": "Some content",
                "category": "note",
                "source": "user",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Test item"
        assert data["scope"] == "global"
        assert data["project_id"] is None
        assert data["category"] == "note"

    def test_create_project_scoped(self, client):
        # Create project first
        proj = client.post("/api/projects", json={"name": "Test Project"})
        pid = proj.json()["id"]

        resp = client.post(
            "/api/context",
            json={
                "title": "Project context",
                "content": "Details here",
                "scope": "project",
                "project_id": pid,
            },
        )
        assert resp.status_code == 201
        assert resp.json()["scope"] == "project"
        assert resp.json()["project_id"] == pid

    def test_get_item(self, client):
        create = client.post(
            "/api/context",
            json={
                "title": "Get me",
                "content": "Body",
            },
        )
        item_id = create.json()["id"]
        resp = client.get(f"/api/context/{item_id}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Get me"

    def test_get_not_found(self, client):
        resp = client.get("/api/context/nonexistent")
        assert resp.status_code == 404

    def test_filter_by_scope(self, client):
        client.post("/api/context", json={"title": "G", "content": "g", "scope": "global"})
        proj = client.post("/api/projects", json={"name": "P"})
        pid = proj.json()["id"]
        client.post(
            "/api/context",
            json={"title": "P", "content": "p", "scope": "project", "project_id": pid},
        )

        global_items = client.get("/api/context?scope=global").json()
        assert len(global_items) == 1
        assert global_items[0]["title"] == "G"

    def test_search(self, client):
        client.post("/api/context", json={"title": "Auth", "content": "Use JWT"})
        client.post("/api/context", json={"title": "DB", "content": "Use Postgres"})

        results = client.get("/api/context?search=JWT").json()
        assert len(results) == 1
        assert results[0]["title"] == "Auth"

    def test_update(self, client):
        create = client.post("/api/context", json={"title": "Old", "content": "old"})
        item_id = create.json()["id"]

        resp = client.patch(f"/api/context/{item_id}", json={"title": "New", "content": "new"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "New"
        assert resp.json()["content"] == "new"

    def test_update_not_found(self, client):
        resp = client.patch("/api/context/nonexistent", json={"title": "X"})
        assert resp.status_code == 404

    def test_delete(self, client):
        create = client.post("/api/context", json={"title": "Del", "content": "me"})
        item_id = create.json()["id"]

        resp = client.delete(f"/api/context/{item_id}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        assert client.get(f"/api/context/{item_id}").status_code == 404

    def test_delete_not_found(self, client):
        resp = client.delete("/api/context/nonexistent")
        assert resp.status_code == 404

    def test_create_defaults_to_no_category(self, client):
        resp = client.post(
            "/api/context",
            json={
                "title": "No cat",
                "content": "Should have null category",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["category"] is None

    def test_create_with_instruction_category(self, client):
        resp = client.post(
            "/api/context",
            json={
                "title": "Must follow",
                "content": "Use 2-space indent",
                "category": "instruction",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["category"] == "instruction"

    def test_create_with_reference_category(self, client):
        resp = client.post(
            "/api/context",
            json={
                "title": "API docs",
                "content": "See https://...",
                "category": "reference",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["category"] == "reference"

    def test_filter_by_category(self, client):
        client.post(
            "/api/context",
            json={
                "title": "Rule",
                "content": "mandatory",
                "category": "instruction",
            },
        )
        client.post(
            "/api/context",
            json={
                "title": "Info",
                "content": "background",
                "category": "reference",
            },
        )
        client.post(
            "/api/context",
            json={
                "title": "Note",
                "content": "general",
            },
        )

        instructions = client.get("/api/context?category=instruction").json()
        assert len(instructions) == 1
        assert instructions[0]["title"] == "Rule"

        references = client.get("/api/context?category=reference").json()
        assert len(references) == 1
        assert references[0]["title"] == "Info"
