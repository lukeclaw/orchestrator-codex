"""Unit tests for POST /api/pr-preview/batch endpoint."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from orchestrator.api.app import create_app
from orchestrator.api.routes import pr_preview
from orchestrator.state.db import get_memory_connection
from orchestrator.state.migrations.runner import apply_migrations


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the PR cache before each test."""
    pr_preview._pr_cache.clear()
    yield
    pr_preview._pr_cache.clear()


@pytest.fixture
def client():
    conn = get_memory_connection()
    apply_migrations(conn)
    app = create_app(db=conn, test_mode=True)
    with TestClient(app) as c:
        yield c


SAMPLE_PR = {
    "title": "Fix auth",
    "state": "open",
    "draft": False,
    "number": 42,
    "user": {"login": "alice"},
    "base": {"repo": {"full_name": "org/repo"}},
    "head": {"sha": "abc123"},
    "created_at": "2026-03-01T10:00:00Z",
    "updated_at": "2026-03-05T14:00:00Z",
    "merged": False,
    "merged_at": None,
    "merged_by": None,
    "additions": 10,
    "deletions": 5,
    "changed_files": 2,
    "commits": 1,
    "auto_merge": None,
}


def _gh_router(path, **kwargs):
    """Route gh api calls to appropriate mock responses."""
    if "pulls/42" in path and "/reviews" in path:
        return []
    if "pulls/42" in path and "/comments" in path:
        return []
    if "issues/42/comments" in path:
        return []
    if "pulls/42/files" in path:
        return []
    if "pulls/42/requested_reviewers" in path:
        return {"users": []}
    if "check-runs" in path:
        return {"check_runs": []}
    if "pulls/42" in path:
        return SAMPLE_PR
    if "pulls/99" in path:
        return {**SAMPLE_PR, "number": 99}
    if "issues/99/comments" in path:
        return []
    if "99/reviews" in path:
        return []
    if "99/comments" in path:
        return []
    if "99/files" in path:
        return []
    if "99/requested_reviewers" in path:
        return {"users": []}
    raise HTTPException(404, "Not found")


@patch.object(pr_preview, "_run_gh", new_callable=AsyncMock)
def test_batch_basic(mock_gh, client):
    """Batch endpoint returns results for each URL."""
    mock_gh.side_effect = _gh_router

    resp = client.post(
        "/api/pr-preview/batch",
        json={"urls": ["https://github.com/org/repo/pull/42"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "https://github.com/org/repo/pull/42" in data["results"]
    result = data["results"]["https://github.com/org/repo/pull/42"]
    assert result is not None
    assert result["title"] == "Fix auth"
    assert result["number"] == 42


@patch.object(pr_preview, "_run_gh", new_callable=AsyncMock)
def test_batch_deduplication(mock_gh, client):
    """Duplicate URLs are deduplicated."""
    mock_gh.side_effect = _gh_router

    url = "https://github.com/org/repo/pull/42"
    resp = client.post(
        "/api/pr-preview/batch",
        json={"urls": [url, url, url]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 1
    assert url in data["results"]


def test_batch_max_limit(client):
    """More than 50 URLs returns 400."""
    urls = [f"https://github.com/org/repo/pull/{i}" for i in range(51)]
    resp = client.post("/api/pr-preview/batch", json={"urls": urls})
    assert resp.status_code == 400


@patch.object(pr_preview, "_run_gh", new_callable=AsyncMock)
def test_batch_cache_interaction(mock_gh, client):
    """Cached URLs are returned from cache, not re-fetched."""
    mock_gh.side_effect = _gh_router

    # Pre-populate cache
    url = "https://github.com/org/repo/pull/42"
    import time

    pr_preview._pr_cache["org/repo/42"] = (
        time.time(),
        {"title": "Cached PR", "state": "open", "number": 42, "fetched_at": ""},
    )

    resp = client.post("/api/pr-preview/batch", json={"urls": [url]})
    assert resp.status_code == 200
    result = resp.json()["results"][url]
    assert result["title"] == "Cached PR"
    # _run_gh should not have been called since it was cached
    mock_gh.assert_not_called()


@patch.object(pr_preview, "_run_gh", new_callable=AsyncMock)
def test_batch_invalid_url(mock_gh, client):
    """Invalid URLs return null in results."""
    resp = client.post(
        "/api/pr-preview/batch",
        json={"urls": ["https://example.com/not-a-pr"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"]["https://example.com/not-a-pr"] is None


@patch.object(pr_preview, "_run_gh", new_callable=AsyncMock)
def test_batch_error_isolation(mock_gh, client):
    """A 404 for one PR returns null, others still succeed."""
    call_count = 0

    async def side_effect(path, **kwargs):
        nonlocal call_count
        is_main_pull = (
            "pulls/99" in path
            and "/reviews" not in path
            and "/comments" not in path
            and "/files" not in path
            and "requested_reviewers" not in path
        )
        if is_main_pull:
            raise HTTPException(404, "Not found")
        return _gh_router(path, **kwargs)

    mock_gh.side_effect = side_effect

    resp = client.post(
        "/api/pr-preview/batch",
        json={
            "urls": [
                "https://github.com/org/repo/pull/42",
                "https://github.com/org/other/pull/99",
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"]["https://github.com/org/repo/pull/42"] is not None
    assert data["results"]["https://github.com/org/other/pull/99"] is None


@patch.object(pr_preview, "_run_gh", new_callable=AsyncMock)
def test_batch_auth_error_short_circuits(mock_gh, client):
    """A 401 error short-circuits remaining fetches and includes error field."""
    call_count = {"value": 0}

    async def side_effect(path, **kwargs):
        is_main_pull = (
            "pulls/42" in path
            and "/reviews" not in path
            and "/comments" not in path
            and "/files" not in path
            and "requested_reviewers" not in path
        )
        if is_main_pull:
            call_count["value"] += 1
            raise HTTPException(401, "Not authenticated")
        return _gh_router(path, **kwargs)

    mock_gh.side_effect = side_effect

    resp = client.post(
        "/api/pr-preview/batch",
        json={"urls": ["https://github.com/org/repo/pull/42"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"]["https://github.com/org/repo/pull/42"] is None
    assert "error" in data


@patch.object(pr_preview, "_run_gh", new_callable=AsyncMock)
def test_batch_rate_limit_short_circuits(mock_gh, client):
    """A 429 error short-circuits and includes error field."""

    async def side_effect(path, **kwargs):
        is_main_pull = (
            "pulls/42" in path
            and "/reviews" not in path
            and "/comments" not in path
            and "/files" not in path
            and "requested_reviewers" not in path
        )
        if is_main_pull:
            raise HTTPException(429, "Rate limited")
        return _gh_router(path, **kwargs)

    mock_gh.side_effect = side_effect

    resp = client.post(
        "/api/pr-preview/batch",
        json={"urls": ["https://github.com/org/repo/pull/42"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
    assert "Rate limited" in data["error"]
