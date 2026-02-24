"""Unit tests for tunnel API endpoints.

Tests the FastAPI routes for tunnel management.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from orchestrator.api.app import create_app
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
    session = repo.create_session(db, "test-worker", "user/rdev-vm", "/tmp/work")
    return session


@pytest.fixture
def local_session(db):
    """Create a local session for testing."""
    from orchestrator.state.repositories import sessions as repo
    session = repo.create_session(db, "local-worker", "localhost", "/tmp/work")
    return session


class TestCreateTunnelEndpoint:
    """Tests for POST /api/sessions/{id}/tunnel."""

    def test_creates_tunnel_for_rdev_session(self, client, rdev_session):
        """Should create tunnel for rdev worker."""
        with patch("orchestrator.session.tunnel.create_tunnel") as mock_create:
            mock_create.return_value = (True, {
                "local_port": 4200,
                "remote_port": 4200,
                "pid": 12345,
                "host": "user/rdev-vm",
            })

            response = client.post(
                f"/api/sessions/{rdev_session.id}/tunnel",
                json={"port": 4200}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["ok"] is True
            assert data["local_port"] == 4200
            assert data["pid"] == 12345

    def test_rejects_tunnel_for_local_session(self, client, local_session):
        """Should reject tunnel request for non-rdev worker."""
        response = client.post(
            f"/api/sessions/{local_session.id}/tunnel",
            json={"port": 4200}
        )

        assert response.status_code == 400
        assert "only supported for remote" in response.json()["detail"].lower()

    def test_returns_404_for_unknown_session(self, client):
        """Should return 404 for non-existent session."""
        response = client.post(
            "/api/sessions/unknown-id/tunnel",
            json={"port": 4200}
        )

        assert response.status_code == 404

    def test_returns_409_for_port_conflict(self, client, rdev_session):
        """Should return 409 when port is already tunneled to different host."""
        with patch("orchestrator.session.tunnel.create_tunnel") as mock_create:
            mock_create.return_value = (False, {
                "error": "Port 4200 already tunneled to other/host"
            })

            response = client.post(
                f"/api/sessions/{rdev_session.id}/tunnel",
                json={"port": 4200}
            )

            assert response.status_code == 409

    def test_returns_500_for_ssh_failure(self, client, rdev_session):
        """Should return 500 when SSH tunnel fails to start."""
        with patch("orchestrator.session.tunnel.create_tunnel") as mock_create:
            mock_create.return_value = (False, {
                "error": "SSH tunnel failed to start"
            })

            response = client.post(
                f"/api/sessions/{rdev_session.id}/tunnel",
                json={"port": 4200}
            )

            assert response.status_code == 500


class TestCloseTunnelEndpoint:
    """Tests for DELETE /api/sessions/{id}/tunnel/{port}."""

    def test_closes_tunnel(self, client, rdev_session):
        """Should close existing tunnel."""
        with patch("orchestrator.session.tunnel.close_tunnel") as mock_close:
            mock_close.return_value = (True, "Tunnel closed")

            response = client.delete(
                f"/api/sessions/{rdev_session.id}/tunnel/4200"
            )

            assert response.status_code == 200
            data = response.json()
            assert data["ok"] is True

            # Verify host was passed for ownership check
            mock_close.assert_called_once_with(4200, host="user/rdev-vm")

    def test_returns_404_for_unknown_session(self, client):
        """Should return 404 for non-existent session."""
        response = client.delete("/api/sessions/unknown-id/tunnel/4200")

        assert response.status_code == 404


class TestListSessionTunnelsEndpoint:
    """Tests for GET /api/sessions/{id}/tunnels."""

    def test_lists_tunnels_for_rdev_session(self, client, rdev_session):
        """Should list tunnels for rdev worker."""
        with patch("orchestrator.session.tunnel.get_tunnels_for_host") as mock_get:
            mock_get.return_value = {
                4200: {"remote_port": 4200, "pid": 12345, "host": "user/rdev-vm"},
                3000: {"remote_port": 3000, "pid": 12346, "host": "user/rdev-vm"},
            }

            response = client.get(f"/api/sessions/{rdev_session.id}/tunnels")

            assert response.status_code == 200
            data = response.json()
            assert "4200" in data["tunnels"]
            assert "3000" in data["tunnels"]

    def test_returns_empty_for_local_session(self, client, local_session):
        """Should return empty tunnels for non-rdev worker."""
        response = client.get(f"/api/sessions/{local_session.id}/tunnels")

        assert response.status_code == 200
        assert response.json()["tunnels"] == {}

    def test_returns_404_for_unknown_session(self, client):
        """Should return 404 for non-existent session."""
        response = client.get("/api/sessions/unknown-id/tunnels")

        assert response.status_code == 404


class TestListAllTunnelsEndpoint:
    """Tests for GET /api/tunnels."""

    def test_lists_all_tunnels(self, client):
        """Should list all active tunnels."""
        with patch("orchestrator.session.tunnel.discover_active_tunnels") as mock_discover:
            mock_discover.return_value = {
                4200: {"remote_port": 4200, "pid": 12345, "host": "user/rdev-vm"},
                3000: {"remote_port": 3000, "pid": 12346, "host": "other/host"},
            }

            response = client.get("/api/tunnels")

            assert response.status_code == 200
            data = response.json()
            # JSON keys are strings
            assert "4200" in data["tunnels"] or 4200 in data["tunnels"]
            assert "3000" in data["tunnels"] or 3000 in data["tunnels"]

            # Should force refresh
            mock_discover.assert_called_once_with(force_refresh=True)
