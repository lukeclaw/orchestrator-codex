"""GitHub PR preview endpoint — fetches PR metadata via `gh api`."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import Counter, OrderedDict

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory cache: key = "owner/repo/number", value = (timestamp, response_dict)
_pr_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_PR_CACHE_TTL_OPEN = 120.0  # seconds — open PRs change frequently
_PR_CACHE_TTL_CLOSED = 600.0  # seconds — merged/closed PRs are static
_MAX_CACHE_SIZE = 200

# Semaphore to limit concurrent gh api calls (shared across batch and single-PR)
_GH_SEMAPHORE = asyncio.Semaphore(3)

_PR_URL_RE = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")

_GH_TIMEOUT = 15  # seconds per subprocess call
_GH_HTTP_CACHE = "1s"  # short TTL forces ETag revalidation (304s are free)

# Prevent `gh` CLI from opening a browser for SSO re-authorization.
# When a SAML SSO session expires, gh may try to launch the default browser
# (triggering an Okta login page).  Setting GH_BROWSER to `true` (a no-op
# command that exits 0) prevents this — gh invokes `true <url>` which silently
# discards the URL.  Empty strings do NOT work: gh treats them as unset and
# falls back to the system default (`open` on macOS).
_GH_ENV = {**os.environ, "GH_BROWSER": "true", "BROWSER": "true"}


async def _run_gh(*args: str, cache: str | None = None) -> dict | list:
    """Run `gh api <args>` and return parsed JSON."""
    cmd = ["gh", "api"]
    if cache:
        cmd.extend(["--cache", cache])
    cmd.extend(args)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_GH_ENV,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_GH_TIMEOUT)
    if proc.returncode != 0:
        err = stderr.decode().strip()
        err_lower = err.lower()
        if (
            "auth" in err_lower
            or "login" in err_lower
            or "token" in err_lower
            or "not logged" in err_lower
            or "saml" in err_lower
            or "sso" in err_lower
            or "bad credentials" in err_lower
            or "401" in err
        ):
            raise HTTPException(
                401, "GitHub CLI not authenticated. Run `gh auth login` in a terminal to fix this."
            )
        if "404" in err or "Not Found" in err:
            raise HTTPException(404, "PR not found on GitHub")
        if "rate limit" in err_lower:
            raise HTTPException(429, "GitHub API rate limit exceeded")
        raise HTTPException(502, f"gh api error: {err}")
    return json.loads(stdout)


def _parse_pr_url(url: str) -> tuple[str, str, str]:
    """Extract (owner, repo, number) from a GitHub PR URL."""
    m = _PR_URL_RE.search(url)
    if not m:
        raise HTTPException(400, "Not a valid GitHub PR URL")
    return m.group(1), m.group(2), m.group(3)


def _extract_suggestion_original(comment: dict) -> str | None:
    """Extract original lines from diff_hunk when comment body has a suggestion."""
    body = comment.get("body") or ""
    if "```suggestion" not in body:
        return None
    # Count how many lines are in the suggestion block
    m = re.search(r"```suggestion\s*\n(.*?)```", body, re.DOTALL)
    if not m:
        return None
    suggestion_lines = m.group(1).rstrip("\n").split("\n")
    num_suggestion_lines = len(suggestion_lines)

    diff_hunk = comment.get("diff_hunk") or ""
    if not diff_hunk:
        return None
    # diff_hunk ends with the lines being commented on.
    # The last N lines (matching suggestion line count) are the original code.
    hunk_lines = diff_hunk.split("\n")
    # Skip the @@ header line and only look at content lines
    content_lines = [ln for ln in hunk_lines if not ln.startswith("@@")]
    # Take the last N content lines — strip the leading +/- /space prefix
    original = content_lines[-num_suggestion_lines:]
    # Strip the diff prefix character (first char: +, -, or space)
    return "\n".join(ln[1:] if ln and ln[0] in ("+", "-", " ") else ln for ln in original)


def _build_reviews(
    reviews_raw: list[dict],
    review_comments: list[dict],
    issue_comments: list[dict],
    pr_author: str = "",
) -> list[dict]:
    """Build review list with latest state and per-user comment counts."""
    # Map review_id -> review author so inline comments can be attributed
    # to the review owner (e.g. Copilot inline comment -> copilot-reviewer[bot])
    review_id_to_author: dict[int, str] = {}
    for r in reviews_raw:
        rid = r.get("id")
        author = (r.get("user") or {}).get("login", "")
        if rid and author:
            review_id_to_author[rid] = author

    # Index review comments: top-level by id, replies grouped by parent
    top_comments: dict[int, dict] = {}  # id -> comment
    replies_by_parent: dict[int, list[dict]] = {}  # parent_id -> [replies]
    for c in review_comments:
        reply_to = c.get("in_reply_to_id")
        if reply_to:
            replies_by_parent.setdefault(reply_to, []).append(c)
        else:
            top_comments[c["id"]] = c

    # Build per-user comment threads (top-level review comments only)
    user_threads: dict[str, list[dict]] = {}
    comment_counts: Counter[str] = Counter()
    latest_comment_url: dict[str, str] = {}
    for cid, c in top_comments.items():
        # Attribute to review author if the comment belongs to a review
        pr_review_id = c.get("pull_request_review_id")
        user = review_id_to_author.get(pr_review_id, "") if pr_review_id else ""
        if not user:
            user = c.get("user", {}).get("login", "")
        if not user:
            continue
        comment_counts[user] += 1
        if c.get("html_url"):
            latest_comment_url[user] = c["html_url"]
        path = c.get("path", "")
        thread = {
            "body": c.get("body") or "",
            "file": path.rsplit("/", 1)[-1] if path else "",
            "html_url": c.get("html_url"),
            "original_lines": _extract_suggestion_original(c),
            "created_at": c.get("created_at"),
            "replies": [],
        }
        for r in replies_by_parent.get(cid, []):
            thread["replies"].append(
                {
                    "author": (r.get("user") or {}).get("login", ""),
                    "body": r.get("body") or "",
                    "created_at": r.get("created_at"),
                }
            )
        user_threads.setdefault(user, []).append(thread)

    # Also include issue-level comments (PR conversation tab)
    for c in issue_comments:
        user = c.get("user", {}).get("login", "")
        if user:
            comment_counts[user] += 1
            if c.get("html_url"):
                latest_comment_url[user] = c["html_url"]
            thread = {
                "body": c.get("body") or "",
                "file": "",
                "html_url": c.get("html_url"),
                "created_at": c.get("created_at"),
                "replies": [],
            }
            user_threads.setdefault(user, []).append(thread)

    # Dedupe reviews: keep latest per reviewer, skip pending
    latest: dict[str, dict] = {}
    for r in reviews_raw:
        user = r.get("user", {}).get("login", "")
        state = r.get("state", "").lower()
        if not user or state == "pending":
            continue
        # Include the review-level body as a thread if non-empty
        threads = list(user_threads.get(user, []))
        review_body = (r.get("body") or "").strip()
        if review_body:
            threads.insert(
                0,
                {
                    "body": review_body,
                    "file": "",
                    "html_url": r.get("html_url"),
                    "original_lines": None,
                    "created_at": r.get("submitted_at"),
                    "replies": [],
                },
            )
        latest[user] = {
            "reviewer": user,
            "state": state,
            "submitted_at": r.get("submitted_at"),
            "comments": comment_counts.get(user, 0),
            "comment_threads": threads,
            "html_url": r.get("html_url"),
        }

    # Add users who only left comments but no formal review
    for user, count in comment_counts.items():
        if user not in latest:
            threads = user_threads.get(user, [])
            # Use the earliest comment timestamp as the review timestamp
            thread_dates = [t["created_at"] for t in threads if t.get("created_at")]
            submitted_at = min(thread_dates) if thread_dates else None
            latest[user] = {
                "reviewer": user,
                "state": "commented",
                "submitted_at": submitted_at,
                "comments": count,
                "comment_threads": threads,
                "html_url": latest_comment_url.get(user),
            }

    # Attach comment counts to reviewers already present
    for user, entry in latest.items():
        if user in comment_counts and entry["comments"] == 0:
            entry["comments"] = comment_counts[user]

    # Exclude the PR author — their comments are responses, not reviews
    if pr_author:
        latest.pop(pr_author, None)

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
    requested_reviewers: list[str] | None = None,
    closed_by: str | None = None,
) -> dict:
    """Build the PR preview response from raw GitHub API data."""
    state = pr.get("state", "open")
    if pr.get("merged"):
        state = "merged"

    author = pr.get("user", {}).get("login", "")

    return {
        "title": pr.get("title", ""),
        "state": state,
        "draft": pr.get("draft", False),
        "number": pr.get("number", 0),
        "repo": pr.get("base", {}).get("repo", {}).get("full_name", ""),
        "author": author,
        "created_at": pr.get("created_at", ""),
        "updated_at": pr.get("updated_at", ""),
        "closed_at": pr.get("closed_at"),
        "closed_by": closed_by,
        "merged_at": pr.get("merged_at"),
        "merged_by": (pr.get("merged_by") or {}).get("login"),
        "additions": pr.get("additions", 0),
        "deletions": pr.get("deletions", 0),
        "changed_files": pr.get("changed_files", 0),
        "commits": pr.get("commits", 0),
        "reviews": _build_reviews(reviews_raw, review_comments, issue_comments, author),
        "requested_reviewers": requested_reviewers or [],
        "auto_merge": pr.get("auto_merge") is not None,
        "checks": _map_checks(checks_raw),
        "files": _map_files(files_raw),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _get_skips(cache_key: str) -> set[str]:
    """Determine which endpoints to skip based on previously cached state."""
    if cache_key in _pr_cache:
        _, prev = _pr_cache[cache_key]
        if prev.get("state") in ("merged", "closed"):
            return {"requested_reviewers", "check_runs"}
    return set()


async def _fetch_pr_detail(owner: str, repo: str, number: str, cache_key: str) -> dict:
    """Core fetch logic for a single PR — parallel gh api calls, build response, cache."""
    skip = _get_skips(cache_key)

    # Fetch PR metadata, reviews, comments, and files in parallel
    coros = [
        _run_gh(f"repos/{owner}/{repo}/pulls/{number}", cache=_GH_HTTP_CACHE),
        _run_gh(f"repos/{owner}/{repo}/pulls/{number}/reviews", cache=_GH_HTTP_CACHE),
        _run_gh(f"repos/{owner}/{repo}/pulls/{number}/comments", cache=_GH_HTTP_CACHE),
        _run_gh(f"repos/{owner}/{repo}/issues/{number}/comments", cache=_GH_HTTP_CACHE),
        _run_gh(f"repos/{owner}/{repo}/pulls/{number}/files", cache=_GH_HTTP_CACHE),
    ]
    if "requested_reviewers" not in skip:
        coros.append(
            _run_gh(
                f"repos/{owner}/{repo}/pulls/{number}/requested_reviewers",
                cache=_GH_HTTP_CACHE,
            )
        )

    results = await asyncio.gather(*coros)

    pr_data = results[0]
    reviews_data = results[1]
    review_comments = results[2]
    issue_comments = results[3]
    files_data = results[4]
    requested_reviewers_data = results[5] if "requested_reviewers" not in skip else {"users": []}

    pr_dict = pr_data if isinstance(pr_data, dict) else {}
    actual_state = "merged" if pr_dict.get("merged") else pr_dict.get("state", "open")

    # Safety net: if we skipped endpoints but PR is actually open, re-fetch them
    if skip and actual_state == "open":
        if "requested_reviewers" in skip:
            requested_reviewers_data = await _run_gh(
                f"repos/{owner}/{repo}/pulls/{number}/requested_reviewers",
                cache=_GH_HTTP_CACHE,
            )

    # Fetch check runs using head SHA (depends on pr_data)
    head_sha = pr_data.get("head", {}).get("sha", "") if isinstance(pr_data, dict) else ""
    checks_data: dict = {"check_runs": []}
    if head_sha and "check_runs" not in skip:
        try:
            checks_data = await _run_gh(
                f"repos/{owner}/{repo}/commits/{head_sha}/check-runs",
                cache=_GH_HTTP_CACHE,
            )
        except HTTPException:
            logger.debug("Could not fetch check runs for %s", cache_key)
    elif head_sha and "check_runs" in skip and actual_state == "open":
        try:
            checks_data = await _run_gh(
                f"repos/{owner}/{repo}/commits/{head_sha}/check-runs",
                cache=_GH_HTTP_CACHE,
            )
        except HTTPException:
            pass

    # For closed (non-merged) PRs, find who closed it from issue events
    closed_by: str | None = None
    if pr_dict.get("state") == "closed" and not pr_dict.get("merged"):
        try:
            events = await _run_gh(
                f"repos/{owner}/{repo}/issues/{number}/events", cache=_GH_HTTP_CACHE
            )
            if isinstance(events, list):
                for ev in reversed(events):
                    if ev.get("event") == "closed":
                        closed_by = (ev.get("actor") or {}).get("login")
                        break
        except HTTPException:
            logger.debug("Could not fetch events for %s", cache_key)

    # Extract requested reviewer logins
    rr = requested_reviewers_data if isinstance(requested_reviewers_data, dict) else {}
    requested_reviewers = [u.get("login", "") for u in rr.get("users", []) if u.get("login")]

    response = _build_response(
        pr_dict,
        reviews_data if isinstance(reviews_data, list) else [],
        checks_data.get("check_runs", []) if isinstance(checks_data, dict) else [],
        review_comments if isinstance(review_comments, list) else [],
        issue_comments if isinstance(issue_comments, list) else [],
        files_data if isinstance(files_data, list) else [],
        requested_reviewers=requested_reviewers,
        closed_by=closed_by,
    )

    # Cache with LRU eviction
    _pr_cache[cache_key] = (time.time(), response)
    _pr_cache.move_to_end(cache_key)
    while len(_pr_cache) > _MAX_CACHE_SIZE:
        _pr_cache.popitem(last=False)
    return response


def _check_pr_cache(cache_key: str) -> dict | None:
    """Return cached PR data if fresh, else None."""
    if cache_key in _pr_cache:
        ts, data = _pr_cache[cache_key]
        state = data.get("state", "open")
        ttl = _PR_CACHE_TTL_CLOSED if state in ("merged", "closed") else _PR_CACHE_TTL_OPEN
        if time.time() - ts < ttl:
            _pr_cache.move_to_end(cache_key)
            return data
    return None


@router.get("/pr-preview")
async def get_pr_preview(
    url: str = Query(..., description="GitHub PR URL"),
    refresh: bool = Query(False, description="Bypass in-memory cache"),
):
    """Fetch a GitHub PR preview with metadata, reviews, and CI checks."""
    owner, repo, number = _parse_pr_url(url)
    cache_key = f"{owner}/{repo}/{number}"

    if not refresh:
        cached = _check_pr_cache(cache_key)
        if cached is not None:
            return cached

    return await _fetch_pr_detail(owner, repo, number, cache_key)


class _BatchRequest(BaseModel):
    urls: list[str]


@router.post("/pr-preview/batch")
async def batch_pr_preview(body: _BatchRequest):
    """Fetch PR preview data for multiple URLs in parallel."""
    if len(body.urls) > 50:
        raise HTTPException(400, "Maximum 50 URLs per batch request")

    # Deduplicate
    unique_urls = list(dict.fromkeys(body.urls))

    results: dict[str, dict | None] = {}
    uncached: list[str] = []

    # Check cache first
    for url in unique_urls:
        try:
            owner, repo, number = _parse_pr_url(url)
            cache_key = f"{owner}/{repo}/{number}"
            cached = _check_pr_cache(cache_key)
            if cached is not None:
                results[url] = cached
            else:
                uncached.append(url)
        except HTTPException:
            results[url] = None

    # Fetch uncached PRs with concurrency control and error isolation
    error_msg: str | None = None
    stop_event = asyncio.Event()

    async def fetch_one(fetch_url: str) -> tuple[str, dict | None]:
        if stop_event.is_set():
            return fetch_url, None
        async with _GH_SEMAPHORE:
            if stop_event.is_set():
                return fetch_url, None
            try:
                owner, repo, number = _parse_pr_url(fetch_url)
                cache_key = f"{owner}/{repo}/{number}"
                data = await _fetch_pr_detail(owner, repo, number, cache_key)
                return fetch_url, data
            except HTTPException as e:
                if e.status_code in (401, 429):
                    nonlocal error_msg
                    error_msg = e.detail if isinstance(e.detail, str) else str(e.detail)
                    stop_event.set()
                return fetch_url, None
            except Exception:
                return fetch_url, None

    if uncached:
        fetch_results = await asyncio.gather(
            *[fetch_one(u) for u in uncached], return_exceptions=True
        )
        for r in fetch_results:
            if isinstance(r, Exception):
                continue
            url, data = r
            results[url] = data

    response: dict = {"results": results}
    if error_msg:
        response["error"] = error_msg
    return response


@router.post("/pr-auto-merge")
async def toggle_auto_merge(
    url: str = Query(..., description="GitHub PR URL"),
    enable: bool = Query(..., description="Enable or disable auto-merge"),
):
    """Toggle auto-merge on a GitHub PR via `gh pr merge`."""
    owner, repo, number = _parse_pr_url(url)
    cache_key = f"{owner}/{repo}/{number}"

    if enable:
        cmd = ["gh", "pr", "merge", number, "--repo", f"{owner}/{repo}", "--auto", "--squash"]
    else:
        cmd = ["gh", "pr", "merge", number, "--repo", f"{owner}/{repo}", "--disable-auto"]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_GH_ENV,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_GH_TIMEOUT)
    if proc.returncode != 0:
        err = stderr.decode().strip()
        raise HTTPException(502, f"gh pr merge error: {err}")

    # Invalidate cache so next fetch reflects the new state
    _pr_cache.pop(cache_key, None)

    return {"ok": True, "auto_merge": enable}


@router.post("/pr-ready")
async def mark_ready_for_review(
    url: str = Query(..., description="GitHub PR URL"),
):
    """Mark a draft PR as ready for review via `gh pr ready`."""
    owner, repo, number = _parse_pr_url(url)
    cache_key = f"{owner}/{repo}/{number}"

    cmd = ["gh", "pr", "ready", number, "--repo", f"{owner}/{repo}"]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_GH_ENV,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_GH_TIMEOUT)
    if proc.returncode != 0:
        err = stderr.decode().strip()
        raise HTTPException(502, f"gh pr ready error: {err}")

    _pr_cache.pop(cache_key, None)

    return {"ok": True}
