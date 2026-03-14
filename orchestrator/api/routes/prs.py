"""PR search endpoint — discovers user's PRs via GitHub GraphQL API."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from orchestrator.api.deps import get_db
from orchestrator.api.routes.pr_preview import _run_gh
from orchestrator.api.routes.tasks import _get_task_key
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.state.repositories import tasks as tasks_repo

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory cache: key = "active" or "recent:7", value = (timestamp, response_dict)
_search_cache: dict[str, tuple[float, dict]] = {}
_SEARCH_CACHE_TTL = 600.0  # 10 minutes

# Single GraphQL query fetches the PR list AND status details in one API call.
_GRAPHQL_QUERY = """\
query($q: String!) {
  search(query: $q, type: ISSUE, first: 100) {
    nodes {
      ... on PullRequest {
        url
        number
        title
        state
        isDraft
        author { login }
        createdAt
        updatedAt
        closedAt
        mergedAt
        mergedBy { login }
        additions
        deletions
        changedFiles
        repository { nameWithOwner }
        mergeable
        autoMergeRequest { enabledAt }
        reviewDecision
        reviewRequests(first: 10) {
          nodes {
            requestedReviewer {
              ... on User { login }
              ... on Team { name }
            }
          }
        }
        commits(last: 1) {
          nodes {
            commit {
              statusCheckRollup {
                state
              }
            }
          }
        }
      }
    }
  }
}"""


def _compute_attention_level(
    *,
    draft: bool,
    state: str,
    ci_state: str | None,
    review_decision: str | None,
    mergeable: str | None,
) -> int:
    """Compute attention level (1-4) for a PR."""
    if draft:
        return 4
    if state != "open":
        return 3  # closed/merged — no urgency model
    if (
        ci_state == "failure"
        or review_decision == "changes_requested"
        or mergeable == "conflicting"
    ):
        return 1  # needs action
    if review_decision == "approved" and ci_state == "success":
        return 2  # ready to ship
    return 3  # in review (default)


def _derive_ci_state(node: dict) -> str | None:
    """Derive ci_state from statusCheckRollup.

    Only returns definitive states (success/failure).  PENDING is not
    surfaced because statusCheckRollup aggregates ALL checks including
    owner-approval gates — we cannot distinguish "real CI running" from
    "only the gate is pending" without fetching individual check
    contexts (which causes GitHub 504 timeouts at scale).  The expanded
    preview card has full per-check detail when needed.
    """
    commits_nodes = (node.get("commits") or {}).get("nodes", [])
    if not commits_nodes:
        return None
    commit = (commits_nodes[0] or {}).get("commit") or {}
    rollup = commit.get("statusCheckRollup") or {}
    rollup_state = (rollup.get("state") or "").upper()
    if rollup_state == "SUCCESS":
        return "success"
    if rollup_state in ("FAILURE", "ERROR"):
        return "failure"
    # PENDING/EXPECTED — can't tell if real CI or just a gate; omit.
    return None


def _parse_graphql_prs(nodes: list[dict], fetched_at: str) -> list[dict]:
    """Parse GraphQL PR nodes into a flat list of PR dicts."""
    prs: list[dict] = []

    for node in nodes:
        if not isinstance(node, dict) or "url" not in node:
            continue

        url = node["url"]
        repo = (node.get("repository") or {}).get("nameWithOwner", "")
        gql_state = (node.get("state") or "OPEN").upper()
        draft = node.get("isDraft", False)
        author = (node.get("author") or {}).get("login", "")

        if gql_state == "MERGED":
            state = "closed"
            merged_at = node.get("mergedAt")
        elif gql_state == "CLOSED":
            state = "closed"
            merged_at = None
        else:
            state = "open"
            merged_at = None

        # Review decision (lowercase)
        raw_decision = node.get("reviewDecision")
        review_decision = raw_decision.lower() if raw_decision else None

        # Review requests — extract logins/names
        review_requests: list[str] = []
        for rr_node in (node.get("reviewRequests") or {}).get("nodes", []):
            reviewer = (rr_node or {}).get("requestedReviewer") or {}
            name = reviewer.get("login") or reviewer.get("name")
            if name:
                review_requests.append(name)

        ci_state = _derive_ci_state(node)
        auto_merge = bool(node.get("autoMergeRequest"))
        merged_by = (node.get("mergedBy") or {}).get("login")
        raw_mergeable = (node.get("mergeable") or "").upper()
        mergeable = raw_mergeable.lower() if raw_mergeable in ("MERGEABLE", "CONFLICTING") else None
        attention_level = _compute_attention_level(
            draft=draft,
            state=state,
            ci_state=ci_state,
            review_decision=review_decision,
            mergeable=mergeable,
        )

        prs.append(
            {
                "url": url,
                "repo": repo,
                "number": node.get("number", 0),
                "title": node.get("title", ""),
                "state": state,
                "draft": draft,
                "author": author,
                "created_at": node.get("createdAt", ""),
                "updated_at": node.get("updatedAt", ""),
                "closed_at": node.get("closedAt"),
                "merged_at": merged_at,
                "additions": node.get("additions", 0),
                "deletions": node.get("deletions", 0),
                "changed_files": node.get("changedFiles", 0),
                "review_decision": review_decision,
                "review_requests": review_requests,
                "auto_merge": auto_merge,
                "ci_state": ci_state,
                "mergeable": mergeable,
                "attention_level": attention_level,
                "merged_by": merged_by,
                "linked_task": None,
                "linked_worker": None,
            }
        )

    return prs


@router.get("/prs")
async def search_prs(
    tab: str = Query("active", description="active or recent"),
    days: int = Query(7, description="Days back for recent tab"),
    refresh: bool = Query(False, description="Bypass cache"),
    db=Depends(get_db),
):
    """Search for PRs authored by the current GitHub user via GraphQL."""
    if tab not in ("active", "recent"):
        raise HTTPException(400, "tab must be 'active' or 'recent'")

    cache_key = tab if tab == "active" else f"recent:{days}"

    # Check cache
    if not refresh and cache_key in _search_cache:
        ts, cached = _search_cache[cache_key]
        if time.time() - ts < _SEARCH_CACHE_TTL:
            return cached

    # Build search query string
    if tab == "active":
        search_q = "type:pr author:@me is:open sort:updated-desc"
    else:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        search_q = f"type:pr author:@me is:closed closed:>{cutoff} sort:updated-desc"

    try:
        result = await _run_gh("graphql", "-f", f"query={_GRAPHQL_QUERY}", "-f", f"q={search_q}")
    except HTTPException as e:
        if e.status_code == 429 and cache_key in _search_cache:
            _, cached = _search_cache[cache_key]
            return cached
        raise

    # Handle GraphQL errors
    if isinstance(result, dict) and result.get("errors"):
        logger.warning("GraphQL errors: %s", result["errors"])

    nodes = (result.get("data") or {}).get("search", {}).get("nodes", [])
    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    prs = _parse_graphql_prs(nodes if isinstance(nodes, list) else [], fetched_at)

    # Cross-reference with tasks
    if prs:
        pr_urls = {pr["url"] for pr in prs}
        all_tasks = tasks_repo.list_tasks(db)
        url_to_task = {}
        for t in all_tasks:
            for link in t.links_list:
                link_url = link.get("url", "")
                if link_url in pr_urls:
                    existing = url_to_task.get(link_url)
                    if not existing or t.updated_at > existing.updated_at:
                        url_to_task[link_url] = t

        for pr in prs:
            task = url_to_task.get(pr["url"])
            if task:
                task_key = _get_task_key(task, db)
                pr["linked_task"] = {
                    "id": task.id,
                    "task_key": task_key,
                    "title": task.title,
                }

                # Resolve worker
                session_id = task.assigned_session_id
                if not session_id and task.parent_task_id:
                    parent = tasks_repo.get_task(db, task.parent_task_id)
                    if parent:
                        session_id = parent.assigned_session_id
                if session_id:
                    session = sessions_repo.get_session(db, session_id)
                    if session:
                        pr["linked_worker"] = {
                            "id": session.id,
                            "name": session.name,
                        }

    # Cache the full response
    response = {"prs": prs}
    _search_cache[cache_key] = (time.time(), response)
    return response
