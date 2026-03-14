"""Unit tests for the GET /api/prs endpoint."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from orchestrator.api.app import create_app
from orchestrator.api.routes import pr_preview, prs
from orchestrator.state.db import get_memory_connection
from orchestrator.state.migrations.runner import apply_migrations

_INSERT_TASK = (
    "INSERT INTO tasks"
    " (id, project_id, title, status, task_index,"
    " links, created_at, updated_at)"
    " VALUES (?, ?, ?, ?, ?, ?, {ts}, {ts})"
)
_INSERT_TASK_WITH_SESSION = (
    "INSERT INTO tasks"
    " (id, project_id, title, status, task_index,"
    " assigned_session_id, links, created_at, updated_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, {ts}, {ts})"
)
_INSERT_TASK_WITH_PARENT = (
    "INSERT INTO tasks"
    " (id, project_id, title, status, task_index,"
    " parent_task_id, links, created_at, updated_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, {ts}, {ts})"
)


@pytest.fixture(autouse=True)
def clear_caches():
    """Clear caches before each test."""
    prs._search_cache.clear()
    pr_preview._pr_cache.clear()
    yield
    prs._search_cache.clear()
    pr_preview._pr_cache.clear()


@pytest.fixture
def conn():
    c = get_memory_connection()
    apply_migrations(c)
    return c


@pytest.fixture
def client(conn):
    app = create_app(db=conn, test_mode=True)
    with TestClient(app) as c:
        yield c


def _make_search_item(
    number=42,
    repo="org/repo",
    title="Fix something",
    state="open",
    draft=False,
    merged_at=None,
):
    return {
        "html_url": f"https://github.com/{repo}/pull/{number}",
        "number": number,
        "title": title,
        "state": state,
        "user": {"login": "alice"},
        "created_at": "2026-03-10T10:00:00Z",
        "updated_at": "2026-03-13T14:00:00Z",
        "closed_at": "2026-03-12T12:00:00Z" if state == "closed" else None,
        "pull_request": {
            "html_url": f"https://github.com/{repo}/pull/{number}",
            "draft": draft,
            "merged_at": merged_at,
        },
    }


SEARCH_RESPONSE = {
    "total_count": 2,
    "items": [
        _make_search_item(number=42, title="Fix auth"),
        _make_search_item(number=43, title="Add tests", draft=True),
    ],
}


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_active_tab_search(mock_gh, client):
    """Active tab returns open PRs."""
    mock_gh.return_value = SEARCH_RESPONSE

    resp = client.get("/api/prs?tab=active")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["prs"]) == 2
    assert data["prs"][0]["number"] == 42
    assert data["prs"][0]["title"] == "Fix auth"
    assert data["prs"][0]["state"] == "open"
    assert data["prs"][0]["draft"] is False
    assert data["prs"][1]["draft"] is True

    # Verify search query
    call_args = mock_gh.call_args[0][0]
    assert "is:open" in call_args
    assert "author:@me" in call_args


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_recent_tab_search(mock_gh, client):
    """Recent tab returns closed PRs with date filter."""
    mock_gh.return_value = {
        "items": [
            _make_search_item(number=40, state="closed", merged_at="2026-03-11T12:00:00Z"),
        ]
    }

    resp = client.get("/api/prs?tab=recent&days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["prs"]) == 1
    assert data["prs"][0]["merged_at"] == "2026-03-11T12:00:00Z"

    call_args = mock_gh.call_args[0][0]
    assert "is:closed" in call_args
    assert "closed:>" in call_args


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_response_shape(mock_gh, client):
    """Verify all fields in the response."""
    mock_gh.return_value = {
        "items": [_make_search_item()],
    }

    resp = client.get("/api/prs?tab=active")
    pr = resp.json()["prs"][0]
    assert pr["url"] == "https://github.com/org/repo/pull/42"
    assert pr["repo"] == "org/repo"
    assert pr["number"] == 42
    assert pr["author"] == "alice"
    assert pr["linked_task"] is None
    assert pr["linked_worker"] is None


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_draft_detection(mock_gh, client):
    """Draft PRs are correctly detected from search API's pull_request.draft field."""
    mock_gh.return_value = {
        "items": [_make_search_item(draft=True)],
    }

    resp = client.get("/api/prs?tab=active")
    assert resp.json()["prs"][0]["draft"] is True


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_task_cross_referencing(mock_gh, client, conn):
    """PRs linked to tasks get task info populated."""
    mock_gh.return_value = {
        "items": [_make_search_item(number=42)],
    }

    # Create a project and task with a link to the PR
    conn.execute(
        "INSERT INTO projects (id, name, task_prefix) VALUES (?, ?, ?)",
        ("proj-1", "Test Project", "TST"),
    )
    pr_link = json.dumps([{"url": "https://github.com/org/repo/pull/42"}])
    conn.execute(
        _INSERT_TASK.format(ts="CURRENT_TIMESTAMP"),
        ("task-1", "proj-1", "Fix auth task", "in_progress", 1, pr_link),
    )
    conn.commit()

    resp = client.get("/api/prs?tab=active")
    pr = resp.json()["prs"][0]
    assert pr["linked_task"] is not None
    assert pr["linked_task"]["id"] == "task-1"
    assert pr["linked_task"]["task_key"] == "TST-1"
    assert pr["linked_task"]["title"] == "Fix auth task"


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_unlinked_pr(mock_gh, client):
    """PRs without task links have null linked_task."""
    mock_gh.return_value = {
        "items": [_make_search_item()],
    }

    resp = client.get("/api/prs?tab=active")
    assert resp.json()["prs"][0]["linked_task"] is None


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_multiple_tasks_pick_most_recent(mock_gh, client, conn):
    """When multiple tasks link the same PR, pick the most recently updated."""
    mock_gh.return_value = {
        "items": [_make_search_item(number=42)],
    }

    conn.execute(
        "INSERT INTO projects (id, name, task_prefix) VALUES (?, ?, ?)",
        ("proj-1", "Test", "TST"),
    )
    pr_link = json.dumps([{"url": "https://github.com/org/repo/pull/42"}])
    conn.execute(
        _INSERT_TASK.format(ts="'2026-03-10 10:00:00'"),
        ("task-old", "proj-1", "Old task", "done", 1, pr_link),
    )
    conn.execute(
        _INSERT_TASK.format(ts="'2026-03-12 10:00:00'"),
        ("task-new", "proj-1", "New task", "in_progress", 2, pr_link),
    )
    conn.commit()

    resp = client.get("/api/prs?tab=active")
    pr = resp.json()["prs"][0]
    assert pr["linked_task"]["id"] == "task-new"


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_subtask_resolves_parent_worker(mock_gh, client, conn):
    """Subtask with no assigned worker falls back to parent task's worker."""
    mock_gh.return_value = {
        "items": [_make_search_item(number=42)],
    }

    conn.execute(
        "INSERT INTO projects (id, name, task_prefix) VALUES (?, ?, ?)",
        ("proj-1", "Test", "TST"),
    )
    # Create a worker session
    conn.execute(
        """INSERT INTO sessions (id, name, host, status, session_type, created_at)
           VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        ("worker-1", "my-worker", "localhost", "working", "worker"),
    )
    # Parent task with worker
    conn.execute(
        _INSERT_TASK_WITH_SESSION.format(ts="CURRENT_TIMESTAMP"),
        ("parent-1", "proj-1", "Parent task", "in_progress", 1, "worker-1", "[]"),
    )
    # Subtask linked to PR, no assigned worker
    pr_link = json.dumps([{"url": "https://github.com/org/repo/pull/42"}])
    conn.execute(
        _INSERT_TASK_WITH_PARENT.format(ts="CURRENT_TIMESTAMP"),
        ("sub-1", "proj-1", "Subtask", "in_progress", 1, "parent-1", pr_link),
    )
    conn.commit()

    resp = client.get("/api/prs?tab=active")
    pr = resp.json()["prs"][0]
    assert pr["linked_worker"] is not None
    assert pr["linked_worker"]["id"] == "worker-1"
    assert pr["linked_worker"]["name"] == "my-worker"


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_cache_behavior(mock_gh, client):
    """Cached results are returned without calling gh api again."""
    mock_gh.return_value = {"items": [_make_search_item()]}

    # First call
    resp1 = client.get("/api/prs?tab=active")
    assert resp1.status_code == 200
    assert mock_gh.call_count == 1

    # Second call should use cache
    resp2 = client.get("/api/prs?tab=active")
    assert resp2.status_code == 200
    assert mock_gh.call_count == 1  # Not called again

    # With refresh=true, should bypass cache
    resp3 = client.get("/api/prs?tab=active&refresh=true")
    assert resp3.status_code == 200
    assert mock_gh.call_count == 2


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_invalid_tab(mock_gh, client):
    """Invalid tab returns 400."""
    resp = client.get("/api/prs?tab=invalid")
    assert resp.status_code == 400


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_401_error(mock_gh, client):
    """Auth errors propagate as 401."""
    from fastapi import HTTPException

    mock_gh.side_effect = HTTPException(401, "Not authenticated")

    resp = client.get("/api/prs?tab=active")
    assert resp.status_code == 401


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_429_returns_stale_cache(mock_gh, client):
    """Rate limit returns stale cache if available."""
    from fastapi import HTTPException

    mock_gh.return_value = {"items": [_make_search_item()]}

    # First call populates cache
    client.get("/api/prs?tab=active")
    assert mock_gh.call_count == 1

    # Expire cache by manipulating timestamp
    prs._search_cache["active"] = (0, prs._search_cache["active"][1])

    # Second call with rate limit should return stale cache
    mock_gh.side_effect = HTTPException(429, "Rate limited")
    resp = client.get("/api/prs?tab=active")
    assert resp.status_code == 200
    assert len(resp.json()["prs"]) == 1
