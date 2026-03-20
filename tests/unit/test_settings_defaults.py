"""Tests for settings endpoint defaults merging."""

import pytest
from fastapi.testclient import TestClient

from orchestrator.api.app import create_app
from orchestrator.config_defaults import SETTING_DEFAULTS
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
