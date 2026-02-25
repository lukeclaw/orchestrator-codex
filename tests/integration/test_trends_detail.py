"""Integration tests for trends detail drill-down feature.

Tests cover:
- Repository-level query functions (query_throughput_detail, query_worker_hours_detail, query_heatmap_detail)
- API endpoint (GET /api/trends/detail) with all chart types and error handling
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch


def _local_today() -> str:
    """Get today's date in local timezone as YYYY-MM-DD."""
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _local_yesterday() -> str:
    """Get yesterday's date in local timezone as YYYY-MM-DD."""
    return (datetime.now().astimezone() - timedelta(days=1)).strftime("%Y-%m-%d")

import pytest
from fastapi.testclient import TestClient

from orchestrator.api.app import create_app
from orchestrator.state.db import get_memory_connection
from orchestrator.state.migrations.runner import apply_migrations
from orchestrator.state.repositories import (
    projects,
    sessions,
    status_events,
    tasks,
)


@pytest.fixture
def client():
    conn = get_memory_connection()
    apply_migrations(conn)
    app = create_app(db=conn)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def populated_db(db):
    """DB with projects, tasks, sessions, and status events for testing drill-down."""
    # Create a project with a task_prefix
    proj = projects.create_project(db, "Test Project", task_prefix="TST")

    # Create tasks
    t1 = tasks.create_task(db, proj.id, "Build login page", priority="H")
    t2 = tasks.create_task(db, proj.id, "Setup CI pipeline", priority="M")
    # Create a subtask under t1
    sub1 = tasks.create_task(db, proj.id, "Login form validation", parent_task_id=t1.id)

    # Mark t1 and sub1 as done (to generate status events)
    tasks.update_task(db, t1.id, status="in_progress")
    tasks.update_task(db, t1.id, status="done")
    tasks.update_task(db, sub1.id, status="in_progress")
    tasks.update_task(db, sub1.id, status="done")

    # Create worker sessions
    w1 = sessions.create_session(db, "worker-alpha", "host1", session_type="worker")
    w2 = sessions.create_session(db, "worker-beta", "host2", session_type="worker")

    # Assign t2 to worker-alpha
    tasks.update_task(db, t2.id, status="in_progress", assigned_session_id=w1.id)

    # Generate worker status events for heatmap/worker-hours
    sessions.update_session(db, w1.id, status="working")
    sessions.update_session(db, w2.id, status="working")
    sessions.update_session(db, w1.id, status="idle")

    return {
        "conn": db,
        "project": proj,
        "tasks": {"t1": t1, "t2": t2, "sub1": sub1},
        "sessions": {"w1": w1, "w2": w2},
    }


# --- Repository Tests ---


class TestQueryThroughputDetail:
    def test_returns_completed_tasks_for_date(self, populated_db):
        conn = populated_db["conn"]
        today = _local_today()

        items = status_events.query_throughput_detail(conn, today)
        # t1 (task) and sub1 (subtask) both marked done
        assert len(items) == 2

        task_items = [i for i in items if not i["is_subtask"]]
        sub_items = [i for i in items if i["is_subtask"]]
        assert len(task_items) == 1
        assert len(sub_items) == 1

    def test_task_enrichment(self, populated_db):
        conn = populated_db["conn"]
        t1 = populated_db["tasks"]["t1"]
        today = _local_today()

        items = status_events.query_throughput_detail(conn, today)
        task_item = next(i for i in items if i["entity_id"] == t1.id)

        assert task_item["title"] == "Build login page"
        assert task_item["task_key"] is not None  # should be TST-<index>
        assert task_item["task_key"].startswith("TST-")
        assert task_item["status"] == "done"
        assert task_item["parent_task_id"] is None

    def test_subtask_has_parent_info(self, populated_db):
        conn = populated_db["conn"]
        sub1 = populated_db["tasks"]["sub1"]
        t1 = populated_db["tasks"]["t1"]
        today = _local_today()

        items = status_events.query_throughput_detail(conn, today)
        sub_item = next(i for i in items if i["entity_id"] == sub1.id)

        assert sub_item["is_subtask"] is True
        assert sub_item["parent_task_id"] == t1.id
        assert sub_item["parent_title"] == "Build login page"
        assert sub_item["parent_task_key"] is not None
        assert sub_item["parent_task_key"].startswith("TST-")

    def test_empty_for_different_date(self, populated_db):
        conn = populated_db["conn"]
        yesterday = _local_yesterday()
        items = status_events.query_throughput_detail(conn, yesterday)
        assert items == []

    def test_handles_deleted_task_gracefully(self, db):
        """Events referencing deleted tasks should still return results."""
        # Insert an event manually referencing a non-existent task
        db.execute(
            """INSERT INTO status_events (entity_type, entity_id, old_status, new_status, is_subtask, timestamp)
               VALUES ('task', 'deleted-task-id', 'in_progress', 'done', 0, ?)""",
            (datetime.now(UTC).isoformat(),),
        )
        db.commit()
        today = _local_today()
        items = status_events.query_throughput_detail(db, today)
        assert len(items) == 1
        assert items[0]["title"] == "Unknown"
        assert items[0]["task_key"] is None


class TestQueryWorkerHoursDetail:
    def test_returns_worker_intervals(self, populated_db):
        conn = populated_db["conn"]
        today = _local_today()

        items = status_events.query_worker_hours_detail(conn, today)
        # w1 went working->idle, w2 went working (still open)
        assert len(items) >= 1

        # Check structure
        for item in items:
            assert "session_id" in item
            assert "session_name" in item
            assert "total_hours" in item
            assert "intervals" in item
            assert isinstance(item["intervals"], list)
            assert "current_task" in item

    def test_session_name_enrichment(self, populated_db):
        conn = populated_db["conn"]
        w1 = populated_db["sessions"]["w1"]
        today = _local_today()

        items = status_events.query_worker_hours_detail(conn, today)
        w1_item = next((i for i in items if i["session_id"] == w1.id), None)
        if w1_item:
            assert w1_item["session_name"] == "worker-alpha"

    def test_current_task_populated(self, populated_db):
        conn = populated_db["conn"]
        w1 = populated_db["sessions"]["w1"]
        today = _local_today()

        items = status_events.query_worker_hours_detail(conn, today)
        w1_item = next((i for i in items if i["session_id"] == w1.id), None)
        if w1_item:
            # t2 is assigned to w1 and in_progress
            assert w1_item["current_task"] is not None
            assert w1_item["current_task"]["title"] == "Setup CI pipeline"

    def test_empty_for_different_date(self, populated_db):
        conn = populated_db["conn"]
        far_past = "2020-01-01"
        items = status_events.query_worker_hours_detail(conn, far_past)
        assert items == []

    def test_intervals_have_start_end(self, populated_db):
        conn = populated_db["conn"]
        today = _local_today()

        items = status_events.query_worker_hours_detail(conn, today)
        for item in items:
            for interval in item["intervals"]:
                assert "start" in interval
                assert "end" in interval
                # Verify parseable ISO timestamps
                start = datetime.fromisoformat(interval["start"])
                end = datetime.fromisoformat(interval["end"])
                assert end >= start

    def test_sorted_by_hours_descending(self, populated_db):
        conn = populated_db["conn"]
        today = _local_today()

        items = status_events.query_worker_hours_detail(conn, today)
        if len(items) >= 2:
            for i in range(len(items) - 1):
                assert items[i]["total_hours"] >= items[i + 1]["total_hours"]


class TestQueryHeatmapDetail:
    def test_returns_events_for_cell(self, populated_db):
        conn = populated_db["conn"]
        now = datetime.now(UTC)
        dow = int(now.strftime("%w"))  # 0=Sun
        hour = now.hour
        since = (now - timedelta(days=7)).strftime("%Y-%m-%d")

        items = status_events.query_heatmap_detail(conn, dow, hour, since)
        # w1 and w2 both transitioned to "working" at this hour
        assert len(items) >= 1

    def test_event_structure(self, populated_db):
        conn = populated_db["conn"]
        now = datetime.now(UTC)
        dow = int(now.strftime("%w"))
        hour = now.hour
        since = (now - timedelta(days=7)).strftime("%Y-%m-%d")

        items = status_events.query_heatmap_detail(conn, dow, hour, since)
        for item in items:
            assert "date" in item
            assert "session_id" in item
            assert "session_name" in item
            assert "timestamp" in item

    def test_session_name_enrichment(self, populated_db):
        conn = populated_db["conn"]
        now = datetime.now(UTC)
        dow = int(now.strftime("%w"))
        hour = now.hour
        since = (now - timedelta(days=7)).strftime("%Y-%m-%d")

        items = status_events.query_heatmap_detail(conn, dow, hour, since)
        names = {i["session_name"] for i in items}
        # At least one of our workers should appear
        assert names & {"worker-alpha", "worker-beta"}

    def test_empty_for_wrong_hour(self, populated_db):
        conn = populated_db["conn"]
        now = datetime.now(UTC)
        dow = int(now.strftime("%w"))
        # Pick an hour that differs from now
        wrong_hour = (now.hour + 12) % 24
        since = (now - timedelta(days=7)).strftime("%Y-%m-%d")

        items = status_events.query_heatmap_detail(conn, dow, wrong_hour, since)
        assert items == []

    def test_ordered_by_timestamp_desc(self, populated_db):
        conn = populated_db["conn"]
        now = datetime.now(UTC)
        dow = int(now.strftime("%w"))
        hour = now.hour
        since = (now - timedelta(days=7)).strftime("%Y-%m-%d")

        items = status_events.query_heatmap_detail(conn, dow, hour, since)
        if len(items) >= 2:
            for i in range(len(items) - 1):
                assert items[i]["timestamp"] >= items[i + 1]["timestamp"]


# --- API Endpoint Tests ---


class TestTrendsDetailAPI:
    def _setup_data(self, client):
        """Helper to create project, tasks, sessions, and generate status events via API."""
        proj = client.post("/api/projects", json={"name": "API Test"}).json()

        task = client.post("/api/tasks", json={
            "project_id": proj["id"], "title": "API Task"
        }).json()
        client.patch(f"/api/tasks/{task['id']}", json={"status": "in_progress"})
        client.patch(f"/api/tasks/{task['id']}", json={"status": "done"})

        with patch("orchestrator.api.routes.sessions.ensure_window", return_value="orchestrator:w1"):
            worker = client.post("/api/sessions", json={
                "name": "api-worker", "host": "localhost"
            }).json()
        client.patch(f"/api/sessions/{worker['id']}", json={"status": "working"})

        return {"project": proj, "task": task, "worker": worker}

    def test_throughput_detail(self, client):
        self._setup_data(client)
        today = _local_today()
        resp = client.get(f"/api/trends/detail?chart=throughput&date={today}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["chart"] == "throughput"
        assert data["date"] == today
        assert isinstance(data["items"], list)
        assert len(data["items"]) >= 1

    def test_worker_hours_detail(self, client):
        self._setup_data(client)
        today = _local_today()
        resp = client.get(f"/api/trends/detail?chart=worker_hours&date={today}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["chart"] == "worker_hours"
        assert isinstance(data["items"], list)

    def test_heatmap_detail(self, client):
        self._setup_data(client)
        now = datetime.now(UTC)
        dow = int(now.strftime("%w"))
        hour = now.hour
        resp = client.get(
            f"/api/trends/detail?chart=heatmap&day_of_week={dow}&hour={hour}&range=7d"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["chart"] == "heatmap"
        assert data["day_of_week"] == dow
        assert data["hour"] == hour
        assert isinstance(data["items"], list)

    def test_missing_chart_param(self, client):
        resp = client.get("/api/trends/detail")
        assert resp.status_code == 422  # FastAPI validation error

    def test_unknown_chart_type(self, client):
        resp = client.get("/api/trends/detail?chart=unknown")
        assert resp.status_code == 400

    def test_throughput_missing_date(self, client):
        resp = client.get("/api/trends/detail?chart=throughput")
        assert resp.status_code == 400
        assert "date is required" in resp.json()["detail"]

    def test_worker_hours_missing_date(self, client):
        resp = client.get("/api/trends/detail?chart=worker_hours")
        assert resp.status_code == 400
        assert "date is required" in resp.json()["detail"]

    def test_heatmap_missing_day_of_week(self, client):
        resp = client.get("/api/trends/detail?chart=heatmap&hour=10")
        assert resp.status_code == 400
        assert "day_of_week and hour are required" in resp.json()["detail"]

    def test_heatmap_missing_hour(self, client):
        resp = client.get("/api/trends/detail?chart=heatmap&day_of_week=1")
        assert resp.status_code == 400

    def test_throughput_empty_date(self, client):
        resp = client.get("/api/trends/detail?chart=throughput&date=2020-01-01")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_heatmap_uses_range_param(self, client):
        """Range parameter controls the since_date for heatmap queries."""
        self._setup_data(client)
        now = datetime.now(UTC)
        dow = int(now.strftime("%w"))
        hour = now.hour

        # With 7d range, should find today's events
        resp_7d = client.get(
            f"/api/trends/detail?chart=heatmap&day_of_week={dow}&hour={hour}&range=7d"
        )
        # With 90d range, should also find them
        resp_90d = client.get(
            f"/api/trends/detail?chart=heatmap&day_of_week={dow}&hour={hour}&range=90d"
        )
        assert resp_7d.status_code == 200
        assert resp_90d.status_code == 200
        # 90d range should return >= 7d range results
        assert len(resp_90d.json()["items"]) >= len(resp_7d.json()["items"])
