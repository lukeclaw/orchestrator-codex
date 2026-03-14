"""PR search endpoint — discovers user's PRs via GitHub search API."""

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

# In-memory cache: key = "active" or "recent:7", value = (timestamp, list[dict])
_search_cache: dict[str, tuple[float, list[dict]]] = {}
_SEARCH_CACHE_TTL = 600.0  # 10 minutes


@router.get("/prs")
async def search_prs(
    tab: str = Query("active", description="active or recent"),
    days: int = Query(7, description="Days back for recent tab"),
    refresh: bool = Query(False, description="Bypass cache"),
    db=Depends(get_db),
):
    """Search for PRs authored by the current GitHub user."""
    if tab not in ("active", "recent"):
        raise HTTPException(400, "tab must be 'active' or 'recent'")

    cache_key = tab if tab == "active" else f"recent:{days}"

    # Check cache
    if not refresh and cache_key in _search_cache:
        ts, cached = _search_cache[cache_key]
        if time.time() - ts < _SEARCH_CACHE_TTL:
            return {"prs": cached}

    # Build search query
    base = "search/issues?q=type:pr+author:@me"
    if tab == "active":
        query_path = f"{base}+is:open&sort=updated&per_page=100"
    else:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        query_path = f"{base}+is:closed+closed:>{cutoff}&sort=updated&per_page=100"

    try:
        result = await _run_gh(query_path)
    except HTTPException as e:
        if e.status_code == 429 and cache_key in _search_cache:
            # Return stale cache on rate limit
            _, cached = _search_cache[cache_key]
            return {"prs": cached}
        raise

    items = result.get("items", []) if isinstance(result, dict) else []

    # Transform items into PrSearchItem shape
    prs = []
    for item in items:
        pr_obj = item.get("pull_request", {}) or {}
        html_url = item.get("html_url", "")

        # Extract repo from URL: https://github.com/org/repo/pull/123
        repo = ""
        parts = html_url.split("/")
        if len(parts) >= 5:
            # parts = ['https:', '', 'github.com', 'org', 'repo', 'pull', '123']
            repo = f"{parts[3]}/{parts[4]}"

        prs.append(
            {
                "url": html_url,
                "repo": repo,
                "number": item.get("number", 0),
                "title": item.get("title", ""),
                "state": item.get("state", "open"),
                "draft": pr_obj.get("draft", False),
                "author": (item.get("user") or {}).get("login", ""),
                "created_at": item.get("created_at", ""),
                "updated_at": item.get("updated_at", ""),
                "closed_at": item.get("closed_at"),
                "merged_at": pr_obj.get("merged_at"),
                "linked_task": None,
                "linked_worker": None,
            }
        )

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

    # Cache result
    _search_cache[cache_key] = (time.time(), prs)

    return {"prs": prs}
