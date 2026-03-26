"""Tests for settings endpoint defaults merging."""

import pytest
from fastapi.testclient import TestClient

from orchestrator.api.app import create_app
from orchestrator.config_defaults import SETTING_DEFAULTS
from orchestrator.providers import DEFAULT_PROVIDER_ID
from orchestrator.state.db import get_memory_connection
from orchestrator.state.migrations.runner import apply_migrations


@pytest.fixture
def client():
    conn = get_memory_connection()
    apply_migrations(conn)
    app = create_app(db=conn, test_mode=True)
    with TestClient(app) as c:
        yield c


class TestSettingsDefaults:
    def test_get_returns_defaults_for_empty_db(self, client):
        """GET /settings should return default values for keys not in DB."""
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        entries = resp.json()
        keys = {e["key"] for e in entries}
        for key in SETTING_DEFAULTS:
            assert key in keys, f"Default key {key!r} missing from response"
            entry = next(e for e in entries if e["key"] == key)
            assert entry["value"] == SETTING_DEFAULTS[key]
            assert entry["updated_at"] == ""

    def test_provider_defaults_are_present_for_empty_db(self, client):
        """Worker and brain provider defaults should be exposed by GET /settings."""
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        entries = resp.json()
        worker = next(e for e in entries if e["key"] == "worker.default_provider")
        brain = next(e for e in entries if e["key"] == "brain.default_provider")
        assert worker["value"] == DEFAULT_PROVIDER_ID
        assert brain["value"] == DEFAULT_PROVIDER_ID
        assert worker["category"] == "worker"
        assert brain["category"] == "brain"

    def test_db_override_takes_precedence(self, client):
        """Once a user sets a value, that overrides the default."""
        client.put("/api/settings", json={"settings": {"ui.preserve_filters": True}})
        resp = client.get("/api/settings")
        entries = resp.json()
        entry = next(e for e in entries if e["key"] == "ui.preserve_filters")
        assert entry["value"] is True
        assert entry["updated_at"] != ""

    def test_category_filter_includes_matching_defaults(self, client):
        """Category filter should include defaults whose category matches."""
        resp = client.get("/api/settings?category=ui")
        entries = resp.json()
        keys = {e["key"] for e in entries}
        assert "ui.preserve_filters" in keys
        assert "claude.update_before_start" not in keys

    def test_category_filter_excludes_non_matching_defaults(self, client):
        """Category filter should exclude defaults from other categories."""
        resp = client.get("/api/settings?category=claude")
        entries = resp.json()
        keys = {e["key"] for e in entries}
        assert "claude.update_before_start" in keys
        assert "ui.preserve_filters" not in keys

    def test_category_filter_includes_worker_provider_default(self, client):
        """Category filter should include the default worker provider key."""
        resp = client.get("/api/settings?category=worker")
        entries = resp.json()
        keys = {e["key"] for e in entries}
        assert "worker.default_provider" in keys
        assert "brain.default_provider" not in keys

    def test_category_filter_includes_brain_provider_default(self, client):
        """Category filter should include the default brain provider key."""
        resp = client.get("/api/settings?category=brain")
        entries = resp.json()
        keys = {e["key"] for e in entries}
        assert "brain.default_provider" in keys
        assert "worker.default_provider" not in keys

    def test_put_then_get_returns_user_value(self, client):
        """Full round-trip: PUT stores value, GET returns it instead of default."""
        # Before: default is False
        resp = client.get("/api/settings")
        entry = next(e for e in resp.json() if e["key"] == "ui.preserve_filters")
        assert entry["value"] is False

        # Toggle on
        client.put("/api/settings", json={"settings": {"ui.preserve_filters": True}})

        # After: user value is True
        resp = client.get("/api/settings")
        entry = next(e for e in resp.json() if e["key"] == "ui.preserve_filters")
        assert entry["value"] is True

    def test_no_duplicate_keys(self, client):
        """If a default key is also in the DB, it should appear only once."""
        client.put(
            "/api/settings",
            json={"settings": {"claude.update_before_start": True}},
        )
        resp = client.get("/api/settings")
        entries = resp.json()
        keys = [e["key"] for e in entries]
        assert keys.count("claude.update_before_start") == 1

    def test_get_provider_registry(self, client):
        """GET /settings/providers should expose the provider registry."""
        resp = client.get("/api/settings/providers")
        assert resp.status_code == 200
        payload = resp.json()
        providers = payload["providers"]
        ids = [provider["id"] for provider in providers]
        assert ids == ["claude", "codex"]
        assert payload["defaults"] == {"worker": DEFAULT_PROVIDER_ID, "brain": DEFAULT_PROVIDER_ID}

        claude = providers[0]
        codex = providers[1]
        assert claude["label"] == "Claude"
        assert codex["label"] == "Codex"
        assert claude["capabilities"]["worker_sessions"]["supported"] is True
        assert codex["capabilities"]["remote_sessions"]["supported"] is False
        assert codex["capabilities"]["remote_sessions"]["disabled_reason"]
