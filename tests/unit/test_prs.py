"""Unit tests for the GET /api/prs endpoint (GraphQL-backed)."""

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


def _make_graphql_node(
    number=42,
    repo="org/repo",
    title="Fix something",
    state="OPEN",
    draft=False,
    merged_at=None,
    rollup_state=None,
    review_decision=None,
    review_requests=None,
    auto_merge=False,
    mergeable=None,
):
    """Build a GraphQL PullRequest node for mocking."""
    rollup = {"state": rollup_state} if rollup_state else None
    rr_nodes = []
    if review_requests:
        for rr in review_requests:
            if isinstance(rr, dict):
                rr_nodes.append({"requestedReviewer": rr})
            else:
                rr_nodes.append({"requestedReviewer": {"login": rr}})
    return {
        "url": f"https://github.com/{repo}/pull/{number}",
        "number": number,
        "title": title,
        "state": state,
        "isDraft": draft,
        "author": {"login": "alice"},
        "createdAt": "2026-03-10T10:00:00Z",
        "updatedAt": "2026-03-13T14:00:00Z",
        "closedAt": ("2026-03-12T12:00:00Z" if state in ("CLOSED", "MERGED") else None),
        "mergedAt": merged_at,
        "mergedBy": {"login": "bob"} if merged_at else None,
        "additions": 10,
        "deletions": 5,
        "changedFiles": 2,
        "repository": {"nameWithOwner": repo},
        "mergeable": mergeable,
        "autoMergeRequest": {"enabledAt": "2026-03-10T10:00:00Z"} if auto_merge else None,
        "reviewDecision": review_decision,
        "reviewRequests": {"nodes": rr_nodes},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": rollup}}]},
    }


def _wrap_graphql(*nodes):
    """Wrap nodes in the GraphQL search response structure."""
    return {"data": {"search": {"nodes": list(nodes)}}}


SEARCH_RESPONSE = _wrap_graphql(
    _make_graphql_node(number=42, title="Fix auth"),
    _make_graphql_node(number=43, title="Add tests", draft=True),
)


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

    # Verify search query contains is:open
    call_args = mock_gh.call_args[0]
    q_arg = [a for a in call_args if a.startswith("q=")][0]
    assert "is:open" in q_arg
    assert "author:@me" in q_arg


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_recent_tab_search(mock_gh, client):
    """Recent tab returns closed PRs with date filter."""
    mock_gh.return_value = _wrap_graphql(
        _make_graphql_node(
            number=40,
            state="MERGED",
            merged_at="2026-03-11T12:00:00Z",
        ),
    )

    resp = client.get("/api/prs?tab=recent&days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["prs"]) == 1
    assert data["prs"][0]["merged_at"] == "2026-03-11T12:00:00Z"

    call_args = mock_gh.call_args[0]
    q_arg = [a for a in call_args if a.startswith("q=")][0]
    assert "is:closed" in q_arg
    assert "closed:>" in q_arg


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_response_shape(mock_gh, client):
    """Verify all fields in the response."""
    mock_gh.return_value = _wrap_graphql(
        _make_graphql_node(rollup_state="SUCCESS", review_decision="APPROVED")
    )

    resp = client.get("/api/prs?tab=active")
    data = resp.json()
    pr = data["prs"][0]
    assert pr["url"] == "https://github.com/org/repo/pull/42"
    assert pr["repo"] == "org/repo"
    assert pr["number"] == 42
    assert pr["author"] == "alice"
    assert pr["additions"] == 10
    assert pr["deletions"] == 5
    assert pr["changed_files"] == 2
    assert pr["review_decision"] == "approved"
    assert pr["review_requests"] == []
    assert pr["auto_merge"] is False
    assert pr["ci_state"] == "success"
    assert pr["attention_level"] == 2
    assert pr["merged_by"] is None
    assert pr["linked_task"] is None
    assert pr["linked_worker"] is None

    # No details key in response
    assert "details" not in data


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_ci_state_success(mock_gh, client):
    """SUCCESS rollup gives ci_state='success'."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node(rollup_state="SUCCESS"))

    resp = client.get("/api/prs?tab=active")
    pr = resp.json()["prs"][0]
    assert pr["ci_state"] == "success"


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_draft_detection(mock_gh, client):
    """Draft PRs are correctly detected from isDraft field."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node(draft=True))

    resp = client.get("/api/prs?tab=active")
    assert resp.json()["prs"][0]["draft"] is True


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_task_cross_referencing(mock_gh, client, conn):
    """PRs linked to tasks get task info populated."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node(number=42))

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
    mock_gh.return_value = _wrap_graphql(_make_graphql_node())

    resp = client.get("/api/prs?tab=active")
    assert resp.json()["prs"][0]["linked_task"] is None


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_multiple_tasks_pick_most_recent(mock_gh, client, conn):
    """When multiple tasks link the same PR, pick the most recently updated."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node(number=42))

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
    mock_gh.return_value = _wrap_graphql(_make_graphql_node(number=42))

    conn.execute(
        "INSERT INTO projects (id, name, task_prefix) VALUES (?, ?, ?)",
        ("proj-1", "Test", "TST"),
    )
    conn.execute(
        """INSERT INTO sessions
           (id, name, host, status, session_type, created_at)
           VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        ("worker-1", "my-worker", "localhost", "working", "worker"),
    )
    conn.execute(
        _INSERT_TASK_WITH_SESSION.format(ts="CURRENT_TIMESTAMP"),
        (
            "parent-1",
            "proj-1",
            "Parent task",
            "in_progress",
            1,
            "worker-1",
            "[]",
        ),
    )
    pr_link = json.dumps([{"url": "https://github.com/org/repo/pull/42"}])
    conn.execute(
        _INSERT_TASK_WITH_PARENT.format(ts="CURRENT_TIMESTAMP"),
        (
            "sub-1",
            "proj-1",
            "Subtask",
            "in_progress",
            1,
            "parent-1",
            pr_link,
        ),
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
    mock_gh.return_value = _wrap_graphql(_make_graphql_node())

    resp1 = client.get("/api/prs?tab=active")
    assert resp1.status_code == 200
    assert mock_gh.call_count == 1

    resp2 = client.get("/api/prs?tab=active")
    assert resp2.status_code == 200
    assert mock_gh.call_count == 1  # Not called again

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

    mock_gh.return_value = _wrap_graphql(_make_graphql_node())

    client.get("/api/prs?tab=active")
    assert mock_gh.call_count == 1

    # Expire cache by manipulating timestamp
    prs._search_cache["active"] = (0, prs._search_cache["active"][1])

    mock_gh.side_effect = HTTPException(429, "Rate limited")
    resp = client.get("/api/prs?tab=active")
    assert resp.status_code == 200
    assert len(resp.json()["prs"]) == 1


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_merged_pr_state(mock_gh, client):
    """Merged PRs have state=closed and merged_at set."""
    mock_gh.return_value = _wrap_graphql(
        _make_graphql_node(
            state="MERGED",
            merged_at="2026-03-11T12:00:00Z",
        )
    )

    resp = client.get("/api/prs?tab=recent")
    data = resp.json()
    assert data["prs"][0]["state"] == "closed"
    assert data["prs"][0]["merged_at"] == "2026-03-11T12:00:00Z"
    assert data["prs"][0]["merged_by"] == "bob"


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_rollup_failure_state(mock_gh, client):
    """FAILURE rollup state produces ci_state='failure'."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node(rollup_state="FAILURE"))

    resp = client.get("/api/prs?tab=active")
    pr = resp.json()["prs"][0]
    assert pr["ci_state"] == "failure"
    assert pr["attention_level"] == 1


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_rollup_pending_state(mock_gh, client):
    """PENDING rollup state produces ci_state='pending'."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node(rollup_state="PENDING"))

    resp = client.get("/api/prs?tab=active")
    pr = resp.json()["prs"][0]
    assert pr["ci_state"] == "pending"


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_no_rollup_gives_null_ci_state(mock_gh, client):
    """No statusCheckRollup gives ci_state=null."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node())

    resp = client.get("/api/prs?tab=active")
    pr = resp.json()["prs"][0]
    assert pr["ci_state"] is None


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_error_rollup_treated_as_failure(mock_gh, client):
    """ERROR rollup state is treated as CI failure."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node(rollup_state="ERROR"))

    resp = client.get("/api/prs?tab=active")
    pr = resp.json()["prs"][0]
    assert pr["ci_state"] == "failure"


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_expected_rollup_treated_as_pending(mock_gh, client):
    """EXPECTED rollup state is treated as pending."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node(rollup_state="EXPECTED"))

    resp = client.get("/api/prs?tab=active")
    pr = resp.json()["prs"][0]
    assert pr["ci_state"] == "pending"


# --- Attention model tests ---


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_attention_needs_action_ci_failure(mock_gh, client):
    """CI failure -> attention level 1."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node(rollup_state="FAILURE"))

    resp = client.get("/api/prs?tab=active")
    assert resp.json()["prs"][0]["attention_level"] == 1


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_attention_needs_action_changes_requested(mock_gh, client):
    """Changes requested -> attention level 1."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node(review_decision="CHANGES_REQUESTED"))

    resp = client.get("/api/prs?tab=active")
    assert resp.json()["prs"][0]["attention_level"] == 1


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_attention_ready_to_ship(mock_gh, client):
    """Approved + CI success -> attention level 2."""
    mock_gh.return_value = _wrap_graphql(
        _make_graphql_node(review_decision="APPROVED", rollup_state="SUCCESS")
    )

    resp = client.get("/api/prs?tab=active")
    assert resp.json()["prs"][0]["attention_level"] == 2


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_attention_in_review_default(mock_gh, client):
    """Open non-draft PR without specific signals -> attention level 3."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node())

    resp = client.get("/api/prs?tab=active")
    assert resp.json()["prs"][0]["attention_level"] == 3


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_attention_draft(mock_gh, client):
    """Draft PR -> attention level 4."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node(draft=True))

    resp = client.get("/api/prs?tab=active")
    assert resp.json()["prs"][0]["attention_level"] == 4


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_attention_approved_ci_failing(mock_gh, client):
    """Approved + CI failing -> level 1 (CI failure wins)."""
    mock_gh.return_value = _wrap_graphql(
        _make_graphql_node(review_decision="APPROVED", rollup_state="FAILURE")
    )

    resp = client.get("/api/prs?tab=active")
    assert resp.json()["prs"][0]["attention_level"] == 1


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_attention_approved_no_ci(mock_gh, client):
    """Approved + no CI -> level 3 (can't confirm ready)."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node(review_decision="APPROVED"))

    resp = client.get("/api/prs?tab=active")
    assert resp.json()["prs"][0]["attention_level"] == 3


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_review_requests_parsed(mock_gh, client):
    """Review requests list is extracted from GraphQL."""
    mock_gh.return_value = _wrap_graphql(
        _make_graphql_node(review_requests=["reviewer1", "reviewer2"])
    )

    resp = client.get("/api/prs?tab=active")
    pr = resp.json()["prs"][0]
    assert pr["review_requests"] == ["reviewer1", "reviewer2"]


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_auto_merge_detected(mock_gh, client):
    """autoMergeRequest present -> auto_merge=True."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node(auto_merge=True))

    resp = client.get("/api/prs?tab=active")
    assert resp.json()["prs"][0]["auto_merge"] is True


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_review_decision_mapping(mock_gh, client):
    """APPROVED -> 'approved' (lowercase)."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node(review_decision="APPROVED"))

    resp = client.get("/api/prs?tab=active")
    assert resp.json()["prs"][0]["review_decision"] == "approved"


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_team_reviewer_parsed(mock_gh, client):
    """Team reviewer uses name not login."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node(review_requests=[{"name": "my-team"}]))

    resp = client.get("/api/prs?tab=active")
    assert resp.json()["prs"][0]["review_requests"] == ["my-team"]


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_mergeable_conflicting(mock_gh, client):
    """CONFLICTING mergeable state is parsed as 'conflicting'."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node(mergeable="CONFLICTING"))

    resp = client.get("/api/prs?tab=active")
    pr = resp.json()["prs"][0]
    assert pr["mergeable"] == "conflicting"


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_mergeable_unknown_is_null(mock_gh, client):
    """UNKNOWN mergeable state is treated as null."""
    mock_gh.return_value = _wrap_graphql(_make_graphql_node(mergeable="UNKNOWN"))

    resp = client.get("/api/prs?tab=active")
    assert resp.json()["prs"][0]["mergeable"] is None


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_attention_needs_action_conflict(mock_gh, client):
    """Merge conflict -> attention level 1 even with CI passing."""
    mock_gh.return_value = _wrap_graphql(
        _make_graphql_node(rollup_state="SUCCESS", mergeable="CONFLICTING")
    )

    resp = client.get("/api/prs?tab=active")
    pr = resp.json()["prs"][0]
    assert pr["attention_level"] == 1


@patch.object(prs, "_run_gh", new_callable=AsyncMock)
def test_conflict_blocks_ready_to_ship(mock_gh, client):
    """Approved + CI passing + conflict -> level 1, not level 2."""
    mock_gh.return_value = _wrap_graphql(
        _make_graphql_node(
            review_decision="APPROVED",
            rollup_state="SUCCESS",
            mergeable="CONFLICTING",
        )
    )

    resp = client.get("/api/prs?tab=active")
    assert resp.json()["prs"][0]["attention_level"] == 1
