"""Tests for notification.created event publishing."""

import pytest
from fastapi.testclient import TestClient

from orchestrator.api.app import create_app
from orchestrator.core.events import subscribe, unsubscribe
from orchestrator.state.db import get_memory_connection
from orchestrator.state.migrations.runner import apply_migrations


@pytest.fixture
def client():
    conn = get_memory_connection()
    apply_migrations(conn)
    app = create_app(db=conn, test_mode=True)
    with TestClient(app) as c:
        yield c


class TestNotificationCreatedEvent:
    def test_create_publishes_event(self, client):
        """POST /notifications should publish a notification.created event."""
        received = []

        def handler(event):
            received.append(event)

        subscribe("notification.created", handler)
        try:
            resp = client.post("/api/notifications", json={"message": "test event"})
            assert resp.status_code == 201
            assert len(received) == 1
            assert received[0].type == "notification.created"
            assert received[0].data["message"] == "test event"
        finally:
            unsubscribe("notification.created", handler)

    def test_event_contains_full_notification_data(self, client):
        """Event data should contain all serialized notification fields."""
        received = []
        subscribe("notification.created", lambda e: received.append(e))
        try:
            resp = client.post(
                "/api/notifications",
                json={
                    "message": "PR needs review",
                    "notification_type": "warning",
                    "link_url": "https://github.com/example/pr/1",
                },
            )
            assert resp.status_code == 201
            data = received[0].data
            assert data["message"] == "PR needs review"
            assert data["notification_type"] == "warning"
            assert data["link_url"] == "https://github.com/example/pr/1"
            assert data["id"]
            assert data["created_at"]
            assert data["dismissed"] is False
        finally:
            unsubscribe("notification.created", received.clear or (lambda e: None))

    def test_event_data_matches_response(self, client):
        """Event data should match the API response body."""
        received = []
        subscribe("notification.created", lambda e: received.append(e))
        try:
            resp = client.post("/api/notifications", json={"message": "sync check"})
            assert resp.status_code == 201
            assert received[0].data == resp.json()
        finally:
            unsubscribe("notification.created", received.clear or (lambda e: None))

    def test_no_event_on_invalid_creation(self, client):
        """No event should be published when creation fails."""
        received = []
        subscribe("notification.created", lambda e: received.append(e))
        try:
            resp = client.post(
                "/api/notifications",
                json={
                    "message": "bad ref",
                    "task_id": "nonexistent-task-id",
                },
            )
            assert resp.status_code == 400
            assert len(received) == 0
        finally:
            unsubscribe("notification.created", received.clear or (lambda e: None))
