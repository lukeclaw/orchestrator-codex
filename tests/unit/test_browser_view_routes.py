"""Unit tests for browser view API endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from websockets.protocol import State as WsState

from orchestrator.api.app import create_app
from orchestrator.browser.cdp_proxy import _active_views, _session_tab_targets
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
    _session_tab_targets.clear()
    yield
    _active_views.clear()
    _session_tab_targets.clear()


def _make_fake_view(session_id: str, host: str = "user/rdev-vm", ws_state=WsState.OPEN):
    """Create a fake BrowserViewSession for testing."""
    from orchestrator.browser.cdp_proxy import BrowserViewSession

    mock_ws = MagicMock()
    mock_ws.close = AsyncMock()
    mock_ws.state = ws_state

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

    @patch("orchestrator.api.routes.browser_view._auto_start_browser_local")
    @patch("orchestrator.api.routes.browser_view.start_browser_view")
    def test_local_session_auto_starts_browser(
        self, mock_start, mock_auto_start, client, local_session
    ):
        """Local sessions are supported — auto-start launches headed Chromium."""
        mock_start.side_effect = RuntimeError("No browser found on CDP port 9222")
        mock_auto_start.side_effect = RuntimeError("Chromium not found")

        response = client.post(
            f"/api/sessions/{local_session.id}/browser-view",
            json={"cdp_port": 9222},
        )
        # 502 because auto-start also failed, but importantly NOT 400
        assert response.status_code == 502

    def test_409_already_active(self, client, rdev_session):
        # Pre-register a fake view
        _active_views[rdev_session.id] = _make_fake_view(rdev_session.id)

        response = client.post(
            f"/api/sessions/{rdev_session.id}/browser-view",
            json={"cdp_port": 9222},
        )
        assert response.status_code == 409

    @patch("orchestrator.api.routes.browser_view._wait_for_rws")
    @patch("orchestrator.api.routes.browser_view.start_browser_view")
    def test_502_no_browser_auto_start_fails(self, mock_start, mock_wait_rws, client, rdev_session):
        """When no browser found and auto-start also fails, return 502."""
        mock_start.side_effect = RuntimeError("No browser found on CDP port 9222")
        mock_wait_rws.side_effect = RuntimeError("Remote worker server not ready")

        response = client.post(
            f"/api/sessions/{rdev_session.id}/browser-view",
            json={"cdp_port": 9222},
        )
        assert response.status_code == 502

    @patch("orchestrator.api.routes.browser_view._wait_for_rws")
    @patch("orchestrator.api.routes.browser_view.start_browser_view")
    def test_auto_start_browser_on_no_browser(
        self, mock_start, mock_wait_rws, client, rdev_session
    ):
        """When no browser found, auto-start via daemon and retry."""
        fake_view = _make_fake_view(rdev_session.id)
        # First call raises, second call (retry) succeeds
        mock_start.side_effect = [RuntimeError("No browser found on CDP port 9222"), fake_view]

        mock_server = MagicMock()
        mock_server.start_browser.return_value = {"status": "ok", "pid": 1234, "port": 9222}
        mock_wait_rws.return_value = mock_server

        response = client.post(
            f"/api/sessions/{rdev_session.id}/browser-view",
            json={"cdp_port": 9222},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        mock_server.start_browser.assert_called_once_with(rdev_session.id, port=9222)

    @patch("orchestrator.api.routes.browser_view.start_browser_view")
    def test_stale_view_cleaned_on_start(self, mock_start, client, rdev_session):
        """When existing view has a dead WebSocket, clean it up and proceed."""
        # Pre-register a stale view (WebSocket CLOSED)
        _active_views[rdev_session.id] = _make_fake_view(rdev_session.id, ws_state=WsState.CLOSED)

        fake_view = _make_fake_view(rdev_session.id)
        mock_start.return_value = fake_view

        response = client.post(
            f"/api/sessions/{rdev_session.id}/browser-view",
            json={"cdp_port": 9222},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        mock_start.assert_called_once()

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


class TestMinimizeEndpoint:
    """Tests for POST /api/sessions/{id}/browser-view/minimize."""

    def test_minimizes_active_view(self, client, rdev_session):
        _active_views[rdev_session.id] = _make_fake_view(rdev_session.id)

        response = client.post(f"/api/sessions/{rdev_session.id}/browser-view/minimize")

        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_404_no_active_view(self, client, rdev_session):
        response = client.post(f"/api/sessions/{rdev_session.id}/browser-view/minimize")
        assert response.status_code == 404

    def test_404_session_not_found(self, client):
        response = client.post("/api/sessions/nonexistent-id/browser-view/minimize")
        assert response.status_code == 404


class TestRestoreEndpoint:
    """Tests for POST /api/sessions/{id}/browser-view/restore."""

    def test_restores_active_view(self, client, rdev_session):
        _active_views[rdev_session.id] = _make_fake_view(rdev_session.id)

        response = client.post(f"/api/sessions/{rdev_session.id}/browser-view/restore")

        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_404_no_active_view(self, client, rdev_session):
        response = client.post(f"/api/sessions/{rdev_session.id}/browser-view/restore")
        assert response.status_code == 404

    def test_404_session_not_found(self, client):
        response = client.post("/api/sessions/nonexistent-id/browser-view/restore")
        assert response.status_code == 404


class TestBrowserStartEndpoint:
    """Tests for POST /api/sessions/{id}/browser-start."""

    @patch("orchestrator.api.routes.browser_view.get_remote_worker_server")
    def test_starts_browser_via_daemon(self, mock_rws, client, rdev_session):
        mock_server = MagicMock()
        mock_server.start_browser.return_value = {
            "status": "ok",
            "pid": 1234,
            "port": 9222,
            "already_running": False,
        }
        mock_rws.return_value = mock_server

        response = client.post(
            f"/api/sessions/{rdev_session.id}/browser-start",
            json={"port": 9222},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["pid"] == 1234
        assert data["port"] == 9222
        assert data["already_running"] is False

    @patch("orchestrator.api.routes.browser_view.get_remote_worker_server")
    def test_returns_already_running(self, mock_rws, client, rdev_session):
        mock_server = MagicMock()
        mock_server.start_browser.return_value = {
            "status": "ok",
            "pid": 5678,
            "port": 9222,
            "already_running": True,
        }
        mock_rws.return_value = mock_server

        response = client.post(
            f"/api/sessions/{rdev_session.id}/browser-start",
            json={"port": 9222},
        )

        assert response.status_code == 200
        assert response.json()["already_running"] is True

    @patch("orchestrator.api.routes.browser_view.get_remote_worker_server")
    def test_503_daemon_unavailable(self, mock_rws, client, rdev_session):
        mock_rws.side_effect = RuntimeError("Connecting to remote host\u2026")

        response = client.post(
            f"/api/sessions/{rdev_session.id}/browser-start",
            json={"port": 9222},
        )
        assert response.status_code == 503

    @patch("orchestrator.api.routes.browser_view.get_remote_worker_server")
    def test_500_browser_start_failure(self, mock_rws, client, rdev_session):
        mock_server = MagicMock()
        mock_server.start_browser.side_effect = RuntimeError("Chromium not found")
        mock_rws.return_value = mock_server

        response = client.post(
            f"/api/sessions/{rdev_session.id}/browser-start",
            json={"port": 9222},
        )
        assert response.status_code == 500

    def test_404_session_not_found(self, client):
        response = client.post(
            "/api/sessions/nonexistent-id/browser-start",
            json={"port": 9222},
        )
        assert response.status_code == 404


class TestBrowserStopEndpoint:
    """Tests for POST /api/sessions/{id}/browser-stop."""

    @patch("orchestrator.api.routes.browser_view.get_remote_worker_server")
    def test_stops_browser_via_daemon(self, mock_rws, client, rdev_session):
        mock_server = MagicMock()
        mock_server.stop_browser.return_value = None
        mock_rws.return_value = mock_server

        response = client.post(f"/api/sessions/{rdev_session.id}/browser-stop")

        assert response.status_code == 200
        assert response.json()["ok"] is True
        mock_server.stop_browser.assert_called_once_with(rdev_session.id)

    @patch("orchestrator.api.routes.browser_view.get_remote_worker_server")
    def test_503_daemon_unavailable(self, mock_rws, client, rdev_session):
        mock_rws.side_effect = RuntimeError("Connecting to remote host\u2026")

        response = client.post(f"/api/sessions/{rdev_session.id}/browser-stop")
        assert response.status_code == 503

    def test_404_session_not_found(self, client):
        response = client.post("/api/sessions/nonexistent-id/browser-stop")
        assert response.status_code == 404


class TestSharedChromeInstance:
    """Tests for shared Chrome instance (idempotent launch, no port collision)."""

    @patch("orchestrator.api.routes.browser_view.httpx")
    def test_auto_start_skips_launch_when_running(self, mock_httpx):
        """_auto_start_browser_local returns immediately if Chrome is running."""
        from orchestrator.api.routes.browser_view import _auto_start_browser_local

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_httpx.get.return_value = mock_resp

        result = _auto_start_browser_local("session-1", 9222)
        assert result == 9222
        mock_httpx.get.assert_called_once_with("http://localhost:9222/json/version", timeout=2)

    @patch("orchestrator.api.routes.browser_view._read_cdp_port_for_session")
    @patch("orchestrator.api.routes.browser_view._auto_start_browser_local")
    def test_browser_start_uses_shared_port(
        self, mock_auto_start, mock_read_port, client, local_session
    ):
        """start_browser_endpoint passes port directly without collision check."""
        mock_read_port.return_value = 9222
        mock_auto_start.return_value = 9222

        response = client.post(
            f"/api/sessions/{local_session.id}/browser-start",
            json={"port": 9222},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["port"] == 9222
        mock_auto_start.assert_called_once_with(local_session.id, 9222)


class TestSessionDeleteStopsBrowser:
    """Test that session delete stops the remote browser process."""

    @patch("orchestrator.api.routes.sessions.kill_window")
    @patch("orchestrator.terminal.remote_worker_server.get_remote_worker_server")
    def test_session_delete_stops_browser(self, mock_get_rws, mock_kill, client, rdev_session):
        """Deleting an rdev session calls rws.stop_browser()."""
        mock_server = MagicMock()
        mock_get_rws.return_value = mock_server

        response = client.delete(f"/api/sessions/{rdev_session.id}")

        assert response.status_code == 200
        mock_server.stop_browser.assert_called_once_with(rdev_session.id)

    @patch("orchestrator.api.routes.sessions.kill_window")
    @patch("orchestrator.terminal.remote_worker_server.get_remote_worker_server")
    def test_session_delete_survives_rws_failure(
        self, mock_get_rws, mock_kill, client, rdev_session
    ):
        """Session delete succeeds even if RWS daemon is unavailable."""
        mock_get_rws.side_effect = RuntimeError("Daemon not available")

        response = client.delete(f"/api/sessions/{rdev_session.id}")

        assert response.status_code == 200
