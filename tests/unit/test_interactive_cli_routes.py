"""Unit tests for interactive CLI API endpoints."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from orchestrator.api.app import create_app
from orchestrator.state.db import get_connection
from orchestrator.state.migrations.runner import apply_migrations
from orchestrator.terminal.interactive import _active_clis

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
def local_session(db):
    """Create a local session for testing."""
    from orchestrator.state.repositories import sessions as repo

    session = repo.create_session(db, "test-worker", "localhost", "/tmp/work")
    return session


@pytest.fixture
def rdev_session(db):
    """Create an rdev session for testing."""
    from orchestrator.state.repositories import sessions as repo

    session = repo.create_session(db, "rdev-worker", "user/rdev-vm", "/tmp/work")
    return session


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear in-memory registry before each test."""
    _active_clis.clear()
    yield
    _active_clis.clear()


class TestOpenEndpoint:
    """Tests for POST /api/sessions/{id}/interactive-cli."""

    @patch("orchestrator.terminal.interactive.tmux.create_window")
    @patch("orchestrator.terminal.interactive.tmux.send_keys")
    def test_opens_local_cli(self, mock_send, mock_create, client, local_session):
        response = client.post(
            f"/api/sessions/{local_session.id}/interactive-cli",
            json={"command": "bash"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["window_name"] == "test-worker-icli"

    @patch("orchestrator.terminal.interactive.tmux.send_keys")
    @patch("orchestrator.terminal.interactive.ssh.wait_for_prompt", return_value=True)
    @patch("orchestrator.terminal.interactive.ssh.remote_connect")
    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_opens_remote_cli(
        self, mock_create, mock_connect, mock_wait, mock_send, client, rdev_session
    ):
        response = client.post(
            f"/api/sessions/{rdev_session.id}/interactive-cli",
            json={"command": "sudo yum install screen"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["window_name"] == "rdev-worker-icli"

    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_409_duplicate(self, mock_create, client, local_session):
        # First open succeeds
        response = client.post(
            f"/api/sessions/{local_session.id}/interactive-cli",
            json={},
        )
        assert response.status_code == 200

        # Second open returns 409
        response = client.post(
            f"/api/sessions/{local_session.id}/interactive-cli",
            json={},
        )
        assert response.status_code == 409

    def test_404_session_not_found(self, client):
        response = client.post(
            "/api/sessions/nonexistent-id/interactive-cli",
            json={},
        )
        assert response.status_code == 404


class TestCloseEndpoint:
    """Tests for DELETE /api/sessions/{id}/interactive-cli."""

    @patch("orchestrator.terminal.interactive.tmux.kill_window")
    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_closes_active_cli(self, mock_create, mock_kill, client, local_session):
        # Open first
        client.post(
            f"/api/sessions/{local_session.id}/interactive-cli",
            json={},
        )

        response = client.delete(f"/api/sessions/{local_session.id}/interactive-cli")

        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_404_no_active_cli(self, client, local_session):
        response = client.delete(f"/api/sessions/{local_session.id}/interactive-cli")
        assert response.status_code == 404


class TestStatusEndpoint:
    """Tests for GET /api/sessions/{id}/interactive-cli."""

    @patch("orchestrator.terminal.interactive.tmux.window_exists", return_value=True)
    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_returns_active_status(self, mock_create, mock_exists, client, local_session):
        client.post(
            f"/api/sessions/{local_session.id}/interactive-cli",
            json={"command": "bash"},
        )

        response = client.get(f"/api/sessions/{local_session.id}/interactive-cli")

        assert response.status_code == 200
        data = response.json()
        assert data["active"] is True
        assert data["window_name"] == "test-worker-icli"
        assert data["initial_command"] == "bash"

    def test_returns_inactive_when_none(self, client, local_session):
        response = client.get(f"/api/sessions/{local_session.id}/interactive-cli")

        assert response.status_code == 200
        assert response.json()["active"] is False


class TestSendEndpoint:
    """Tests for POST /api/sessions/{id}/interactive-cli/send."""

    @patch("orchestrator.terminal.interactive.tmux.send_keys", return_value=True)
    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_sends_message(self, mock_create, mock_send, client, local_session):
        client.post(
            f"/api/sessions/{local_session.id}/interactive-cli",
            json={},
        )

        response = client.post(
            f"/api/sessions/{local_session.id}/interactive-cli/send",
            json={"message": "yes"},
        )

        assert response.status_code == 200
        assert response.json()["ok"] is True

    @patch("orchestrator.terminal.interactive.tmux.send_keys", return_value=True)
    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_sends_keys(self, mock_create, mock_send, client, local_session):
        client.post(
            f"/api/sessions/{local_session.id}/interactive-cli",
            json={},
        )

        response = client.post(
            f"/api/sessions/{local_session.id}/interactive-cli/send",
            json={"keys": "C-c"},
        )

        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_404_no_active_cli(self, client, local_session):
        response = client.post(
            f"/api/sessions/{local_session.id}/interactive-cli/send",
            json={"message": "test"},
        )
        assert response.status_code == 404

    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_400_no_message_or_keys(self, mock_create, client, local_session):
        client.post(
            f"/api/sessions/{local_session.id}/interactive-cli",
            json={},
        )

        response = client.post(
            f"/api/sessions/{local_session.id}/interactive-cli/send",
            json={},
        )
        assert response.status_code == 400


class TestCaptureEndpoint:
    """Tests for POST /api/sessions/{id}/interactive-cli/capture."""

    @patch(
        "orchestrator.terminal.interactive.tmux.capture_output",
        return_value="$ whoami\nroot\n",
    )
    @patch("orchestrator.terminal.interactive.tmux.create_window")
    def test_captures_output(self, mock_create, mock_capture, client, local_session):
        client.post(
            f"/api/sessions/{local_session.id}/interactive-cli",
            json={},
        )

        response = client.post(f"/api/sessions/{local_session.id}/interactive-cli/capture?lines=20")

        assert response.status_code == 200
        data = response.json()
        assert "whoami" in data["output"]
        assert data["lines"] == 20

    def test_404_no_active_cli(self, client, local_session):
        response = client.post(f"/api/sessions/{local_session.id}/interactive-cli/capture")
        assert response.status_code == 404
