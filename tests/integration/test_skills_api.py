"""Integration tests for skills API endpoints."""

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


# Mock built-in skill filesystem access throughout
@pytest.fixture(autouse=True)
def mock_builtin_skills():
    with (
        patch(
            "orchestrator.api.routes.skills._list_builtin_skills",
            return_value=[
                {
                    "id": "builtin:brain:create",
                    "name": "create",
                    "target": "brain",
                    "type": "built_in",
                    "description": "Create new tasks",
                    "content": None,
                    "line_count": 50,
                    "enabled": True,
                    "created_at": "2026-01-01T00:00:00",
                    "updated_at": "2026-01-01T00:00:00",
                }
            ],
        ),
        patch(
            "orchestrator.state.repositories.skills._builtin_skill_names",
            return_value={"create", "check-worker"},
        ),
        patch(
            "orchestrator.core.events.publish",
        ),
    ):
        yield


class TestSkillsList:
    def test_list_includes_builtin(self, client):
        resp = client.get("/api/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert any(s["type"] == "built_in" for s in data)

    def test_list_includes_custom(self, client):
        client.post(
            "/api/skills",
            json={
                "name": "my-skill",
                "target": "worker",
                "content": "body",
            },
        )
        resp = client.get("/api/skills")
        data = resp.json()
        custom = [s for s in data if s["type"] == "custom"]
        assert len(custom) == 1
        assert custom[0]["name"] == "my-skill"

    def test_list_custom_excludes_content(self, client):
        client.post(
            "/api/skills",
            json={
                "name": "my-skill",
                "target": "worker",
                "content": "secret body",
            },
        )
        resp = client.get("/api/skills")
        custom = [s for s in resp.json() if s["type"] == "custom"]
        assert custom[0]["content"] is None

    def test_list_filter_target(self, client):
        """When target=worker, only worker skills returned."""
        # The mock builtin returns a brain skill; create a worker custom skill
        client.post(
            "/api/skills",
            json={
                "name": "worker-only",
                "target": "worker",
                "content": "x",
            },
        )
        # Override the builtin mock to respect target filtering
        with patch(
            "orchestrator.api.routes.skills._list_builtin_skills",
            return_value=[],
        ):
            resp = client.get("/api/skills?target=worker")
            data = resp.json()
            assert all(s["target"] == "worker" for s in data)


class TestSkillsCreate:
    def test_create(self, client):
        resp = client.post(
            "/api/skills",
            json={
                "name": "deploy-check",
                "target": "worker",
                "content": "# Deploy\nRun pre-deploy checks",
                "description": "Pre-deploy verification",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "deploy-check"
        assert data["target"] == "worker"
        assert data["type"] == "custom"
        assert data["content"] == "# Deploy\nRun pre-deploy checks"
        assert data["description"] == "Pre-deploy verification"
        assert data["id"]

    def test_create_minimal(self, client):
        resp = client.post(
            "/api/skills",
            json={
                "name": "simple",
                "content": "",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["target"] == "worker"  # default

    def test_create_invalid_name(self, client):
        resp = client.post(
            "/api/skills",
            json={
                "name": "Bad Name",
                "target": "worker",
                "content": "x",
            },
        )
        assert resp.status_code == 400

    def test_create_builtin_conflict(self, client):
        resp = client.post(
            "/api/skills",
            json={
                "name": "create",
                "target": "brain",
                "content": "x",
            },
        )
        assert resp.status_code == 400
        assert "conflicts" in resp.json()["detail"]

    def test_create_line_count(self, client):
        resp = client.post(
            "/api/skills",
            json={
                "name": "multi-line",
                "target": "worker",
                "content": "line1\nline2\nline3",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["line_count"] == 3


class TestSkillsGet:
    def test_get_custom(self, client):
        create = client.post(
            "/api/skills",
            json={
                "name": "get-me",
                "target": "worker",
                "content": "full body here",
            },
        )
        skill_id = create.json()["id"]

        resp = client.get(f"/api/skills/{skill_id}")
        assert resp.status_code == 200
        assert resp.json()["content"] == "full body here"

    def test_get_not_found(self, client):
        resp = client.get("/api/skills/nonexistent")
        assert resp.status_code == 404


class TestSkillsUpdate:
    def test_update(self, client):
        create = client.post(
            "/api/skills",
            json={
                "name": "old-name",
                "target": "worker",
                "content": "old body",
            },
        )
        skill_id = create.json()["id"]

        resp = client.patch(
            f"/api/skills/{skill_id}",
            json={
                "name": "new-name",
                "content": "new body",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "new-name"
        assert resp.json()["content"] == "new body"

    def test_update_partial(self, client):
        create = client.post(
            "/api/skills",
            json={
                "name": "my-skill",
                "target": "worker",
                "content": "body",
                "description": "original desc",
            },
        )
        skill_id = create.json()["id"]

        resp = client.patch(
            f"/api/skills/{skill_id}",
            json={
                "description": "updated desc",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "my-skill"  # unchanged
        assert resp.json()["description"] == "updated desc"

    def test_update_not_found(self, client):
        resp = client.patch("/api/skills/nonexistent", json={"name": "x"})
        assert resp.status_code == 404

    def test_update_invalid_name(self, client):
        create = client.post(
            "/api/skills",
            json={
                "name": "good-name",
                "target": "worker",
                "content": "x",
            },
        )
        skill_id = create.json()["id"]

        resp = client.patch(f"/api/skills/{skill_id}", json={"name": "Bad Name"})
        assert resp.status_code == 400


class TestSkillsDelete:
    def test_delete(self, client):
        create = client.post(
            "/api/skills",
            json={
                "name": "del-me",
                "target": "worker",
                "content": "x",
            },
        )
        skill_id = create.json()["id"]

        resp = client.delete(f"/api/skills/{skill_id}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Confirm deleted
        assert client.get(f"/api/skills/{skill_id}").status_code == 404

    def test_delete_not_found(self, client):
        resp = client.delete("/api/skills/nonexistent")
        assert resp.status_code == 404


class TestSkillsToggle:
    def test_list_includes_enabled_field(self, client):
        resp = client.get("/api/skills")
        assert resp.status_code == 200
        data = resp.json()
        for skill in data:
            assert "enabled" in skill

    def test_custom_skill_default_enabled(self, client):
        resp = client.post(
            "/api/skills",
            json={
                "name": "enabled-by-default",
                "target": "worker",
                "content": "body",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["enabled"] is True

    def test_disable_custom_skill(self, client):
        create = client.post(
            "/api/skills",
            json={
                "name": "toggle-me",
                "target": "worker",
                "content": "body",
            },
        )
        skill_id = create.json()["id"]

        resp = client.patch(f"/api/skills/{skill_id}", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        # Verify it persists
        get_resp = client.get(f"/api/skills/{skill_id}")
        assert get_resp.json()["enabled"] is False

    def test_toggle_builtin_skill(self, client):
        """Test the builtin skill toggle endpoint."""
        with (
            patch(
                "orchestrator.api.routes.skills.get_brain_skills_dir",
                return_value="/tmp/test-skills",
            ),
            patch("os.path.exists", return_value=True),
        ):
            resp = client.patch("/api/skills/builtin/brain/create", json={"enabled": False})
            assert resp.status_code == 200
            assert resp.json()["ok"] is True
            assert resp.json()["enabled"] is False
