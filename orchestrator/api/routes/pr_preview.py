"""GitHub PR preview endpoint — fetches PR metadata via `gh api`."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import Counter

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory cache: key = "owner/repo/number", value = (timestamp, response_dict)
_pr_cache: dict[str, tuple[float, dict]] = {}
_PR_CACHE_TTL = 60.0  # seconds

_PR_URL_RE = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")

_GH_TIMEOUT = 15  # seconds per subprocess call


async def _run_gh(*args: str) -> dict | list:
    """Run `gh api <args>` and return parsed JSON."""
    proc = await asyncio.create_subprocess_exec(
        "gh",
        "api",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_GH_TIMEOUT)
    if proc.returncode != 0:
        err = stderr.decode().strip()
        if "404" in err or "Not Found" in err:
            raise HTTPException(404, "PR not found on GitHub")
        if "rate limit" in err.lower():
            raise HTTPException(429, "GitHub API rate limit exceeded")
        raise HTTPException(502, f"gh api error: {err}")
    return json.loads(stdout)


def _parse_pr_url(url: str) -> tuple[str, str, str]:
    """Extract (owner, repo, number) from a GitHub PR URL."""
    m = _PR_URL_RE.search(url)
    if not m:
        raise HTTPException(400, "Not a valid GitHub PR URL")
    return m.group(1), m.group(2), m.group(3)


def _build_reviews(
    reviews_raw: list[dict],
    review_comments: list[dict],
    issue_comments: list[dict],
) -> list[dict]:
    """Build review list with latest state and per-user comment counts."""
    # Count comments per user (inline review comments + PR-level comments)
    comment_counts: Counter[str] = Counter()
    latest_comment_url: dict[str, str] = {}
    for c in review_comments:
        user = c.get("user", {}).get("login", "")
        if user:
            comment_counts[user] += 1
            if c.get("html_url"):
                latest_comment_url[user] = c["html_url"]
    for c in issue_comments:
        user = c.get("user", {}).get("login", "")
        if user:
            comment_counts[user] += 1
            if c.get("html_url"):
                latest_comment_url[user] = c["html_url"]

    # Dedupe reviews: keep latest per reviewer, skip pending
    latest: dict[str, dict] = {}
    for r in reviews_raw:
        user = r.get("user", {}).get("login", "")
        state = r.get("state", "").lower()
        if not user or state == "pending":
            continue
        latest[user] = {
            "reviewer": user,
            "state": state,
            "submitted_at": r.get("submitted_at"),
            "comments": comment_counts.get(user, 0),
            "html_url": r.get("html_url"),
        }

    # Add users who only left comments but no formal review
    for user, count in comment_counts.items():
        if user not in latest:
            latest[user] = {
                "reviewer": user,
                "state": "commented",
                "submitted_at": None,
                "comments": count,
                "html_url": latest_comment_url.get(user),
            }

    # Attach comment counts to reviewers already present
    for user, entry in latest.items():
        if user in comment_counts and entry["comments"] == 0:
            entry["comments"] = comment_counts[user]

    return list(latest.values())


def _map_checks(check_runs: list[dict]) -> list[dict]:
    """Map GitHub check runs to simplified format."""
    results = []
    for c in check_runs:
        results.append(
            {
                "name": c.get("name", ""),
                "status": c.get("status", ""),
                "conclusion": c.get("conclusion"),
            }
        )
    return results


def _map_files(files_raw: list[dict]) -> list[dict]:
    """Map GitHub PR files to simplified format."""
    return [
        {
            "filename": f.get("filename", ""),
            "status": f.get("status", ""),
            "additions": f.get("additions", 0),
            "deletions": f.get("deletions", 0),
        }
        for f in files_raw
    ]


def _build_response(
    pr: dict,
    reviews_raw: list[dict],
    checks_raw: list[dict],
    review_comments: list[dict],
    issue_comments: list[dict],
    files_raw: list[dict],
) -> dict:
    """Build the PR preview response from raw GitHub API data."""
    state = pr.get("state", "open")
    if pr.get("merged"):
        state = "merged"

    return {
        "title": pr.get("title", ""),
        "state": state,
        "draft": pr.get("draft", False),
        "number": pr.get("number", 0),
        "repo": pr.get("base", {}).get("repo", {}).get("full_name", ""),
        "author": pr.get("user", {}).get("login", ""),
        "created_at": pr.get("created_at", ""),
        "updated_at": pr.get("updated_at", ""),
        "merged_at": pr.get("merged_at"),
        "merged_by": (pr.get("merged_by") or {}).get("login"),
        "additions": pr.get("additions", 0),
        "deletions": pr.get("deletions", 0),
        "changed_files": pr.get("changed_files", 0),
        "commits": pr.get("commits", 0),
        "reviews": _build_reviews(reviews_raw, review_comments, issue_comments),
        "checks": _map_checks(checks_raw),
        "files": _map_files(files_raw),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@router.get("/pr-preview")
async def get_pr_preview(url: str = Query(..., description="GitHub PR URL")):
    """Fetch a GitHub PR preview with metadata, reviews, and CI checks."""
    owner, repo, number = _parse_pr_url(url)
    cache_key = f"{owner}/{repo}/{number}"

    # Check cache
    if cache_key in _pr_cache:
        ts, data = _pr_cache[cache_key]
        if time.time() - ts < _PR_CACHE_TTL:
            return data

    # Fetch PR metadata, reviews, comments, and files in parallel
    pr_data, reviews_data, review_comments, issue_comments, files_data = await asyncio.gather(
        _run_gh(f"repos/{owner}/{repo}/pulls/{number}"),
        _run_gh(f"repos/{owner}/{repo}/pulls/{number}/reviews"),
        _run_gh(f"repos/{owner}/{repo}/pulls/{number}/comments"),
        _run_gh(f"repos/{owner}/{repo}/issues/{number}/comments"),
        _run_gh(f"repos/{owner}/{repo}/pulls/{number}/files"),
    )

    # Fetch check runs using head SHA (depends on pr_data)
    head_sha = pr_data.get("head", {}).get("sha", "") if isinstance(pr_data, dict) else ""
    checks_data: dict = {"check_runs": []}
    if head_sha:
        try:
            checks_data = await _run_gh(f"repos/{owner}/{repo}/commits/{head_sha}/check-runs")
        except HTTPException:
            # Non-fatal: some repos may not have checks configured
            logger.debug("Could not fetch check runs for %s", cache_key)

    response = _build_response(
        pr_data if isinstance(pr_data, dict) else {},
        reviews_data if isinstance(reviews_data, list) else [],
        checks_data.get("check_runs", []) if isinstance(checks_data, dict) else [],
        review_comments if isinstance(review_comments, list) else [],
        issue_comments if isinstance(issue_comments, list) else [],
        files_data if isinstance(files_data, list) else [],
    )

    # Cache
    _pr_cache[cache_key] = (time.time(), response)
    return response
