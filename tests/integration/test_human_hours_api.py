"""Integration tests for human-hours tracking API endpoints."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from orchestrator.api.app import create_app
from orchestrator.state.db import get_memory_connection
from orchestrator.state.migrations.runner import apply_migrations


def _local_today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


@pytest.fixture
def client():
    conn = get_memory_connection()
    apply_migrations(conn)
    app = create_app(db=conn, test_mode=True)
    with TestClient(app) as c:
        yield c, conn


class TestTrendsIncludesHumanHours:
    def test_trends_response_has_human_hours_key(self, client):
        c, _ = client
        resp = c.get("/api/trends?range=7d")
        assert resp.status_code == 200
        data = resp.json()
        assert "human_hours" in data
        assert isinstance(data["human_hours"], list)

    def test_trends_human_hours_empty_when_no_data(self, client):
        c, _ = client
        resp = c.get("/api/trends?range=7d")
        data = resp.json()
        assert data["human_hours"] == []

    def test_trends_human_hours_with_data(self, client):
        c, conn = client
        # Insert a closed interval for today
        now = datetime.now(UTC)
        start = (now - timedelta(hours=2)).isoformat()
        end = now.isoformat()
        conn.execute(
            "INSERT INTO human_activity_events (start_time, end_time) VALUES (?, ?)",
            (start, end),
        )
        conn.commit()

        resp = c.get("/api/trends?range=7d")
        data = resp.json()
        assert len(data["human_hours"]) >= 1
        total = sum(h["hours"] for h in data["human_hours"])
        assert total > 1.9


class TestHumanHoursDetail:
    def test_returns_intervals_for_date(self, client):
        c, conn = client
        today = datetime.now().astimezone()
        start = today.replace(hour=9, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=3)
        conn.execute(
            "INSERT INTO human_activity_events (start_time, end_time) VALUES (?, ?)",
            (start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat()),
        )
        conn.commit()

        date_str = _local_today()
        resp = c.get(f"/api/trends/detail?chart=human_hours&date={date_str}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["chart"] == "human_hours"
        assert data["date"] == date_str
        assert isinstance(data["items"], list)
        assert len(data["items"]) >= 1
        assert "start" in data["items"][0]
        assert "end" in data["items"][0]
        assert "hours" in data["items"][0]

    def test_missing_date_returns_400(self, client):
        c, _ = client
        resp = c.get("/api/trends/detail?chart=human_hours")
        assert resp.status_code == 400
        assert "date is required" in resp.json()["detail"]

    def test_empty_date_returns_empty(self, client):
        c, _ = client
        resp = c.get("/api/trends/detail?chart=human_hours&date=2020-01-01")
        assert resp.status_code == 200
        assert resp.json()["items"] == []
