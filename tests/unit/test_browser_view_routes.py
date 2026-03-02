"""Unit tests for browser view API endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from orchestrator.api.app import create_app
from orchestrator.browser.cdp_proxy import _active_views
from orchestrator.state.db import get_connection
from orchestrator.state.migrations.runner import apply_migrations

pytestmark = pytest.mark.allow_subprocess


@pytest.fixture
def db():
    """Create an in-memory database for testing."""
    conn = get_connection(":memory:")
    apply_migrations(conn)
    yield conn
    conn.close()


@pytest.fixture
def client(db):
    """Create a test client with the in-memory database."""
    app = create_app(db=db)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def rdev_session(db):
    """Create an rdev session for testing."""
    from orchestrator.state.repositories import sessions as repo

    session = repo.create_session(db, "rdev-worker", "user/rdev-vm", "/tmp/work")
    return session


@pytest.fixture
def local_session(db):
    """Create a local session for testing."""
    from orchestrator.state.repositories import sessions as repo

    session = repo.create_session(db, "local-worker", "localhost", "/tmp/work")
    return session


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear in-memory registry before each test."""
    _active_views.clear()
    yield
    _active_views.clear()


def _make_fake_view(session_id: str, host: str = "user/rdev-vm"):
    """Create a fake BrowserViewSession for testing."""
    from orchestrator.browser.cdp_proxy import BrowserViewSession

    mock_ws = MagicMock()
    mock_ws.close = AsyncMock()

    return BrowserViewSession(
        session_id=session_id,
        host=host,
        cdp_ws=mock_ws,
        tunnel_local_port=9222,
        page_url="https://sso.example.com/login",
        page_title="Sign In",
        viewport_width=1280,
        viewport_height=960,
        quality=60,
    )


class TestStartEndpoint:
    """Tests for POST /api/sessions/{id}/browser-view."""

    @patch("orchestrator.api.routes.browser_view.start_browser_view")
    def test_starts_browser_view(self, mock_start, client, rdev_session):
        fake_view = _make_fake_view(rdev_session.id)
        mock_start.return_value = fake_view

        response = client.post(
            f"/api/sessions/{rdev_session.id}/browser-view",
            json={"cdp_port": 9222},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["page_url"] == "https://sso.example.com/login"
        assert data["page_title"] == "Sign In"
        assert data["viewport"]["width"] == 1280
        assert data["viewport"]["height"] == 960

    def test_400_local_session(self, client, local_session):
        response = client.post(
            f"/api/sessions/{local_session.id}/browser-view",
            json={"cdp_port": 9222},
        )
        assert response.status_code == 400

    def test_409_already_active(self, client, rdev_session):
        # Pre-register a fake view
        _active_views[rdev_session.id] = _make_fake_view(rdev_session.id)

        response = client.post(
            f"/api/sessions/{rdev_session.id}/browser-view",
            json={"cdp_port": 9222},
        )
        assert response.status_code == 409

    @patch("orchestrator.api.routes.browser_view.start_browser_view")
    def test_502_no_browser(self, mock_start, client, rdev_session):
        mock_start.side_effect = RuntimeError("No browser found on CDP port 9222")

        response = client.post(
            f"/api/sessions/{rdev_session.id}/browser-view",
            json={"cdp_port": 9222},
        )
        assert response.status_code == 502

    def test_404_session_not_found(self, client):
        response = client.post(
            "/api/sessions/nonexistent-id/browser-view",
            json={"cdp_port": 9222},
        )
        assert response.status_code == 404

    @patch("orchestrator.api.routes.browser_view.start_browser_view")
    def test_custom_quality_and_dimensions(self, mock_start, client, rdev_session):
        fake_view = _make_fake_view(rdev_session.id)
        fake_view.quality = 80
        fake_view.viewport_width = 1920
        fake_view.viewport_height = 1080
        mock_start.return_value = fake_view

        response = client.post(
            f"/api/sessions/{rdev_session.id}/browser-view",
            json={"cdp_port": 9222, "quality": 80, "max_width": 1920, "max_height": 1080},
        )

        assert response.status_code == 200
        mock_start.assert_called_once_with(
            session_id=rdev_session.id,
            host="user/rdev-vm",
            cdp_port=9222,
            quality=80,
            max_width=1920,
            max_height=1080,
        )


class TestStopEndpoint:
    """Tests for DELETE /api/sessions/{id}/browser-view."""

    @patch("orchestrator.api.routes.browser_view.stop_browser_view")
    def test_stops_browser_view(self, mock_stop, client, rdev_session):
        _active_views[rdev_session.id] = _make_fake_view(rdev_session.id)
        mock_stop.return_value = True

        response = client.delete(f"/api/sessions/{rdev_session.id}/browser-view")

        assert response.status_code == 200
        assert response.json()["ok"] is True

    @patch("orchestrator.api.routes.browser_view.stop_browser_view")
    def test_404_no_active_view(self, mock_stop, client, rdev_session):
        mock_stop.return_value = False

        response = client.delete(f"/api/sessions/{rdev_session.id}/browser-view")
        assert response.status_code == 404


class TestStatusEndpoint:
    """Tests for GET /api/sessions/{id}/browser-view."""

    def test_returns_active_status(self, client, rdev_session):
        _active_views[rdev_session.id] = _make_fake_view(rdev_session.id)

        response = client.get(f"/api/sessions/{rdev_session.id}/browser-view")

        assert response.status_code == 200
        data = response.json()
        assert data["active"] is True
        assert data["page_url"] == "https://sso.example.com/login"
        assert data["page_title"] == "Sign In"
        assert data["quality"] == 60

    def test_returns_inactive(self, client, rdev_session):
        response = client.get(f"/api/sessions/{rdev_session.id}/browser-view")

        assert response.status_code == 200
        assert response.json()["active"] is False

    def test_404_session_not_found(self, client):
        response = client.get("/api/sessions/nonexistent-id/browser-view")
        assert response.status_code == 404
