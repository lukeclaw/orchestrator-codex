"""Unit tests for the PR preview endpoint."""

from unittest.mock import AsyncMock, patch

import pytest
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


# --- Sample GitHub API responses ---

SAMPLE_PR = {
    "title": "Fix auth token refresh",
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
    "additions": 127,
    "deletions": 34,
    "changed_files": 5,
    "commits": 3,
}

SAMPLE_PR_MERGED = {
    **SAMPLE_PR,
    "state": "closed",
    "merged": True,
    "merged_at": "2026-03-06T12:00:00Z",
    "merged_by": {"login": "bob"},
}

SAMPLE_REVIEWS = [
    {
        "user": {"login": "bob"},
        "state": "COMMENTED",
        "submitted_at": "2026-03-02T10:00:00Z",
        "html_url": "https://github.com/org/repo/pull/42#pullrequestreview-100",
    },
    {
        "user": {"login": "bob"},
        "state": "APPROVED",
        "submitted_at": "2026-03-04T10:00:00Z",
        "html_url": "https://github.com/org/repo/pull/42#pullrequestreview-200",
    },
    {
        "user": {"login": "carol"},
        "state": "CHANGES_REQUESTED",
        "submitted_at": "2026-03-03T10:00:00Z",
        "html_url": "https://github.com/org/repo/pull/42#pullrequestreview-300",
    },
]

SAMPLE_REVIEW_COMMENTS = [
    {
        "id": 1001,
        "user": {"login": "bob"},
        "body": "Looks good",
        "path": "src/auth/token.ts",
        "html_url": "https://github.com/org/repo/pull/42#discussion_r1001",
    },
    {
        "id": 1002,
        "user": {"login": "bob"},
        "body": "One nit",
        "path": "src/auth/refresh.ts",
        "html_url": "https://github.com/org/repo/pull/42#discussion_r1002",
    },
    {
        "id": 1003,
        "user": {"login": "carol"},
        "body": "Please fix this",
        "path": "src/auth/token.ts",
        "html_url": "https://github.com/org/repo/pull/42#discussion_r1003",
    },
]

SAMPLE_ISSUE_COMMENTS = [
    {
        "user": {"login": "bob"},
        "body": "Thanks for the fix",
        "html_url": "https://github.com/org/repo/pull/42#issuecomment-5001",
    },
    {
        "user": {"login": "dave"},
        "body": "I tested this and it works",
        "html_url": "https://github.com/org/repo/pull/42#issuecomment-5002",
    },
]

SAMPLE_CHECKS = {
    "check_runs": [
        {"name": "build", "status": "completed", "conclusion": "success"},
        {"name": "lint", "status": "completed", "conclusion": "success"},
        {"name": "test-integration", "status": "completed", "conclusion": "failure"},
    ]
}

SAMPLE_FILES = [
    {"filename": "src/auth/token.ts", "status": "modified", "additions": 45, "deletions": 12},
    {"filename": "src/auth/refresh.ts", "status": "added", "additions": 82, "deletions": 0},
    {"filename": "tests/auth.test.ts", "status": "modified", "additions": 0, "deletions": 22},
]

SAMPLE_REQUESTED_REVIEWERS = {"users": [], "teams": []}


def _mock_gh_side_effect(
    pr_data=None,
    reviews_data=None,
    checks_data=None,
    review_comments=None,
    issue_comments=None,
    files_data=None,
    requested_reviewers=None,
):
    """Create a side effect that returns different data based on the gh api path."""
    pr = pr_data or SAMPLE_PR
    reviews = reviews_data if reviews_data is not None else SAMPLE_REVIEWS
    checks = checks_data or SAMPLE_CHECKS
    rc = review_comments if review_comments is not None else SAMPLE_REVIEW_COMMENTS
    ic = issue_comments if issue_comments is not None else SAMPLE_ISSUE_COMMENTS
    files = files_data if files_data is not None else SAMPLE_FILES
    rr = requested_reviewers or SAMPLE_REQUESTED_REVIEWERS

    async def side_effect(*args, **kwargs):
        path = args[0] if args else ""
        if "/reviews" in path:
            return reviews
        elif "/check-runs" in path:
            return checks
        elif "/files" in path:
            return files
        elif "/requested_reviewers" in path:
            return rr
        elif "/issues/" in path and "/comments" in path:
            return ic
        elif "/pulls/" in path and "/comments" in path:
            return rc
        elif "/pulls/" in path:
            return pr
        return {}

    return side_effect


class TestPrPreviewUrlParsing:
    def test_valid_github_pr_url(self, client):
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")
        assert resp.status_code == 200

    def test_invalid_url_returns_400(self, client):
        resp = client.get("/api/pr-preview?url=https://example.com/not-a-pr")
        assert resp.status_code == 400
        assert "Not a valid GitHub PR URL" in resp.json()["detail"]

    def test_missing_url_returns_422(self, client):
        resp = client.get("/api/pr-preview")
        assert resp.status_code == 422

    def test_url_with_extra_path(self, client):
        """PR URLs with extra path segments (e.g. /files) should still work."""
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42/files")
        assert resp.status_code == 200
        assert resp.json()["number"] == 42


class TestPrPreviewResponse:
    def test_open_pr_response(self, client):
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        data = resp.json()
        assert data["title"] == "Fix auth token refresh"
        assert data["state"] == "open"
        assert data["draft"] is False
        assert data["number"] == 42
        assert data["repo"] == "org/repo"
        assert data["author"] == "alice"
        assert data["additions"] == 127
        assert data["deletions"] == 34
        assert data["changed_files"] == 5
        assert data["commits"] == 3
        assert data["merged_at"] is None
        assert data["merged_by"] is None
        assert "fetched_at" in data

    def test_merged_pr_state(self, client):
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect(pr_data=SAMPLE_PR_MERGED)
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        data = resp.json()
        assert data["state"] == "merged"
        assert data["merged_at"] == "2026-03-06T12:00:00Z"
        assert data["merged_by"] == "bob"


class TestReviewsWithComments:
    def test_review_includes_comment_count(self, client):
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        reviews = resp.json()["reviews"]
        # bob: 2 review comments + 1 issue comment = 3 total
        bob = next(r for r in reviews if r["reviewer"] == "bob")
        assert bob["state"] == "approved"
        assert bob["comments"] == 3
        # html_url from latest formal review
        assert bob["html_url"] == "https://github.com/org/repo/pull/42#pullrequestreview-200"

        # carol: 1 review comment
        carol = next(r for r in reviews if r["reviewer"] == "carol")
        assert carol["state"] == "changes_requested"
        assert carol["comments"] == 1
        assert carol["html_url"] == "https://github.com/org/repo/pull/42#pullrequestreview-300"

    def test_comment_only_users_appear_in_reviews(self, client):
        """Users who only left comments (no formal review) should still appear."""
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        reviews = resp.json()["reviews"]
        # dave only left an issue comment, no formal review
        dave = next((r for r in reviews if r["reviewer"] == "dave"), None)
        assert dave is not None
        assert dave["state"] == "commented"
        assert dave["comments"] == 1
        # html_url from latest comment (issue comment)
        assert dave["html_url"] == "https://github.com/org/repo/pull/42#issuecomment-5002"

    def test_no_comments_shows_zero(self, client):
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect(review_comments=[], issue_comments=[])
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        reviews = resp.json()["reviews"]
        for r in reviews:
            assert r["comments"] == 0

    def test_pending_reviews_excluded(self, client):
        reviews_with_pending = [
            {"user": {"login": "dave"}, "state": "PENDING", "submitted_at": None},
            {"user": {"login": "eve"}, "state": "APPROVED", "submitted_at": "2026-03-04T10:00:00Z"},
        ]
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect(
                reviews_data=reviews_with_pending,
                review_comments=[],
                issue_comments=[],
            )
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        reviews = resp.json()["reviews"]
        reviewers = [r["reviewer"] for r in reviews]
        assert "dave" not in reviewers
        assert "eve" in reviewers


class TestFiles:
    def test_files_included_in_response(self, client):
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        files = resp.json()["files"]
        assert len(files) == 3
        assert files[0]["filename"] == "src/auth/token.ts"
        assert files[0]["status"] == "modified"
        assert files[0]["additions"] == 45
        assert files[0]["deletions"] == 12

    def test_added_file(self, client):
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        files = resp.json()["files"]
        added = next(f for f in files if f["status"] == "added")
        assert added["filename"] == "src/auth/refresh.ts"
        assert added["additions"] == 82
        assert added["deletions"] == 0

    def test_empty_files(self, client):
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect(files_data=[])
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        assert resp.json()["files"] == []


class TestChecks:
    def test_check_runs_mapped(self, client):
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        checks = resp.json()["checks"]
        assert len(checks) == 3
        names = [c["name"] for c in checks]
        assert "build" in names
        assert "test-integration" in names

        failed = [c for c in checks if c["conclusion"] == "failure"]
        assert len(failed) == 1
        assert failed[0]["name"] == "test-integration"

    def test_missing_head_sha_skips_checks(self, client):
        pr_no_sha = {**SAMPLE_PR, "head": {}}
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect(pr_data=pr_no_sha)
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        assert resp.json()["checks"] == []


class TestCache:
    def test_second_call_uses_cache(self, client):
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")
            client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        # _run_gh: 6 parallel + 1 sequential (checks) = 7
        # Second call uses cache: 0 calls
        assert mock_gh.call_count == 7

    def test_cache_expires(self, client):
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        # Expire the cache (open PR uses _PR_CACHE_TTL_OPEN)
        for key in pr_preview._pr_cache:
            ts, data = pr_preview._pr_cache[key]
            pr_preview._pr_cache[key] = (ts - pr_preview._PR_CACHE_TTL_OPEN - 1, data)

        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        # Should have fetched again (7 calls)
        assert mock_gh.call_count == 7

    def test_different_prs_cached_separately(self, client):
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")
            client.get("/api/pr-preview?url=https://github.com/org/repo/pull/99")

        # 7 calls per unique PR
        assert mock_gh.call_count == 14

    def test_merged_pr_uses_longer_ttl(self, client):
        """Merged PRs should stay cached for _PR_CACHE_TTL_CLOSED (10min)."""
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect(pr_data=SAMPLE_PR_MERGED)
            client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        # Advance time past _PR_CACHE_TTL_OPEN but within _PR_CACHE_TTL_CLOSED
        for key in pr_preview._pr_cache:
            ts, data = pr_preview._pr_cache[key]
            pr_preview._pr_cache[key] = (ts - pr_preview._PR_CACHE_TTL_OPEN - 1, data)

        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect(pr_data=SAMPLE_PR_MERGED)
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        # Should still be cached — 0 calls
        assert mock_gh.call_count == 0
        assert resp.json()["state"] == "merged"

    def test_merged_pr_cache_eventually_expires(self, client):
        """Merged PR cache should expire after _PR_CACHE_TTL_CLOSED."""
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect(pr_data=SAMPLE_PR_MERGED)
            client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        # Advance time past _PR_CACHE_TTL_CLOSED
        for key in pr_preview._pr_cache:
            ts, data = pr_preview._pr_cache[key]
            pr_preview._pr_cache[key] = (ts - pr_preview._PR_CACHE_TTL_CLOSED - 1, data)

        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect(pr_data=SAMPLE_PR_MERGED)
            client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        # Should refetch — merged PR skips requested_reviewers + check_runs = 5 calls
        assert mock_gh.call_count == 5

    def test_cache_lru_eviction(self, client):
        """Oldest entries should be evicted when cache exceeds _MAX_CACHE_SIZE."""
        original_max = pr_preview._MAX_CACHE_SIZE
        pr_preview._MAX_CACHE_SIZE = 2
        try:
            with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
                mock_gh.side_effect = _mock_gh_side_effect()
                client.get("/api/pr-preview?url=https://github.com/org/repo/pull/1")
                client.get("/api/pr-preview?url=https://github.com/org/repo/pull/2")
                client.get("/api/pr-preview?url=https://github.com/org/repo/pull/3")

            assert len(pr_preview._pr_cache) == 2
            # Oldest (PR #1) should be evicted
            assert "org/repo/1" not in pr_preview._pr_cache
            assert "org/repo/2" in pr_preview._pr_cache
            assert "org/repo/3" in pr_preview._pr_cache
        finally:
            pr_preview._MAX_CACHE_SIZE = original_max


class TestErrorHandling:
    def test_gh_404_returns_404(self, client):
        async def fail_404(*args, **kwargs):
            from fastapi import HTTPException

            raise HTTPException(404, "PR not found on GitHub")

        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = fail_404
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/999")

        assert resp.status_code == 404

    def test_gh_rate_limit_returns_429(self, client):
        async def fail_rate_limit(*args, **kwargs):
            from fastapi import HTTPException

            raise HTTPException(429, "GitHub API rate limit exceeded")

        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = fail_rate_limit
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        assert resp.status_code == 429

    def test_gh_auth_error_returns_401(self, client):
        async def fail_auth(*args, **kwargs):
            from fastapi import HTTPException

            raise HTTPException(
                401, "GitHub CLI not authenticated. Run `gh auth login` in a terminal to fix this."
            )

        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = fail_auth
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        assert resp.status_code == 401
        assert "gh auth login" in resp.json()["detail"]

    def test_checks_failure_nonfatal(self, client):
        """If check-runs fetch fails, the response should still return with empty checks."""

        async def selective_fail(*args, **kwargs):
            path = args[0] if args else ""
            if "/check-runs" in path:
                from fastapi import HTTPException

                raise HTTPException(502, "gh api error")
            if "/reviews" in path:
                return SAMPLE_REVIEWS
            if "/files" in path:
                return SAMPLE_FILES
            if "/requested_reviewers" in path:
                return SAMPLE_REQUESTED_REVIEWERS
            if "/issues/" in path and "/comments" in path:
                return SAMPLE_ISSUE_COMMENTS
            if "/pulls/" in path and "/comments" in path:
                return SAMPLE_REVIEW_COMMENTS
            return SAMPLE_PR

        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = selective_fail
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        assert resp.status_code == 200
        assert resp.json()["checks"] == []


class TestEndpointSkipping:
    def test_merged_pr_skips_endpoints_on_refetch(self, client):
        """After caching a merged PR, re-fetching should skip requested_reviewers + check_runs."""
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect(pr_data=SAMPLE_PR_MERGED)
            client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")
            first_count = mock_gh.call_count

        # First fetch: 7 calls (all endpoints, but check-runs is also skipped for merged
        # since state was unknown — actually it's 7 on cold fetch)
        assert first_count == 7

        # Expire the cache
        for key in pr_preview._pr_cache:
            ts, data = pr_preview._pr_cache[key]
            pr_preview._pr_cache[key] = (ts - pr_preview._PR_CACHE_TTL_CLOSED - 1, data)

        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect(pr_data=SAMPLE_PR_MERGED)
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        # Second fetch: 5 calls (skipped requested_reviewers + check_runs)
        assert mock_gh.call_count == 5
        assert resp.json()["state"] == "merged"
        assert resp.json()["requested_reviewers"] == []
        assert resp.json()["checks"] == []

    def test_state_change_refetches_skipped(self, client):
        """If a merged PR is reopened, skipped endpoints should be recovered."""
        # First fetch as merged
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect(pr_data=SAMPLE_PR_MERGED)
            client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        # Expire the cache
        for key in pr_preview._pr_cache:
            ts, data = pr_preview._pr_cache[key]
            pr_preview._pr_cache[key] = (ts - pr_preview._PR_CACHE_TTL_CLOSED - 1, data)

        # Now the PR is open again
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()  # defaults to SAMPLE_PR (open)
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        # Should have fetched 5 initially (skipped 2), then recovered 2 via safety net = 7
        assert mock_gh.call_count == 7
        assert resp.json()["state"] == "open"

    def test_gh_cache_kwarg_passed(self, client):
        """Verify that _run_gh is called with cache kwarg."""
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        # All calls should pass cache=_GH_HTTP_CACHE
        for call in mock_gh.call_args_list:
            assert call.kwargs.get("cache") == pr_preview._GH_HTTP_CACHE


class TestRefreshBypassesCache:
    def test_refresh_bypasses_inmemory_cache(self, client):
        """refresh=true should skip the in-memory cache and re-fetch from gh."""
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")
            first_count = mock_gh.call_count

        # Normal second call should hit cache (0 new calls)
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")
        assert mock_gh.call_count == 0

        # refresh=true should bypass cache and re-fetch
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            resp = client.get(
                "/api/pr-preview?url=https://github.com/org/repo/pull/42&refresh=true"
            )
        assert resp.status_code == 200
        assert mock_gh.call_count == first_count  # same number of calls as a fresh fetch

    def test_refresh_updates_cache(self, client):
        """After refresh, the cache should contain the new data."""
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        # Refresh with updated title
        updated_pr = {**SAMPLE_PR, "title": "Updated title"}
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect(pr_data=updated_pr)
            resp = client.get(
                "/api/pr-preview?url=https://github.com/org/repo/pull/42&refresh=true"
            )
        assert resp.json()["title"] == "Updated title"

        # Subsequent normal call should return the updated cached data
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            resp = client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")
        assert mock_gh.call_count == 0  # served from cache
        assert resp.json()["title"] == "Updated title"

    def test_refresh_false_uses_cache(self, client):
        """refresh=false (default) should use the cache normally."""
        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42")

        with patch.object(pr_preview, "_run_gh", new_callable=AsyncMock) as mock_gh:
            mock_gh.side_effect = _mock_gh_side_effect()
            client.get("/api/pr-preview?url=https://github.com/org/repo/pull/42&refresh=false")
        assert mock_gh.call_count == 0


class TestHelperFunctions:
    def test_parse_pr_url_standard(self):
        owner, repo, number = pr_preview._parse_pr_url("https://github.com/myorg/myrepo/pull/123")
        assert owner == "myorg"
        assert repo == "myrepo"
        assert number == "123"

    def test_parse_pr_url_with_fragment(self):
        owner, repo, number = pr_preview._parse_pr_url(
            "https://github.com/myorg/myrepo/pull/123#discussion_r12345"
        )
        assert number == "123"

    def test_parse_pr_url_invalid(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            pr_preview._parse_pr_url("https://example.com")
        assert exc_info.value.status_code == 400

    def test_build_reviews_with_comments(self):
        result = pr_preview._build_reviews(
            SAMPLE_REVIEWS, SAMPLE_REVIEW_COMMENTS, SAMPLE_ISSUE_COMMENTS, pr_author="alice"
        )
        bob = next(r for r in result if r["reviewer"] == "bob")
        assert bob["state"] == "approved"
        assert bob["comments"] == 3  # 2 review + 1 issue
        assert bob["html_url"] == "https://github.com/org/repo/pull/42#pullrequestreview-200"

        carol = next(r for r in result if r["reviewer"] == "carol")
        assert carol["comments"] == 1
        assert carol["html_url"] == "https://github.com/org/repo/pull/42#pullrequestreview-300"

        dave = next(r for r in result if r["reviewer"] == "dave")
        assert dave["state"] == "commented"
        assert dave["comments"] == 1
        assert dave["html_url"] == "https://github.com/org/repo/pull/42#issuecomment-5002"

    def test_build_reviews_excludes_pending(self):
        reviews = [
            {"user": {"login": "a"}, "state": "PENDING", "submitted_at": None},
        ]
        result = pr_preview._build_reviews(reviews, [], [])
        assert len(result) == 0

    def test_map_checks(self):
        raw = [
            {"name": "build", "status": "completed", "conclusion": "success"},
            {"name": "test", "status": "in_progress", "conclusion": None},
        ]
        result = pr_preview._map_checks(raw)
        assert len(result) == 2
        assert result[0] == {"name": "build", "status": "completed", "conclusion": "success"}
        assert result[1] == {"name": "test", "status": "in_progress", "conclusion": None}

    def test_map_files(self):
        result = pr_preview._map_files(SAMPLE_FILES)
        assert len(result) == 3
        assert result[0]["filename"] == "src/auth/token.ts"
        assert result[1]["status"] == "added"
        assert result[2]["deletions"] == 22


class TestGhBrowserSuppression:
    """Verify that gh CLI subprocesses cannot open a browser (SSO re-auth)."""

    @pytest.mark.asyncio
    async def test_run_gh_passes_env_with_browser_suppressed(self):
        """_run_gh must pass _GH_ENV (GH_BROWSER='', BROWSER='') to subprocess."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b'{"ok": true}', b"")
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await pr_preview._run_gh("repos/org/repo/pulls/1")

        _, kwargs = mock_exec.call_args
        env = kwargs.get("env", {})
        assert env.get("GH_BROWSER") == "true", "GH_BROWSER must be 'true' to suppress browser"
        assert env.get("BROWSER") == "true", "BROWSER must be 'true' to suppress browser"

    @pytest.mark.asyncio
    async def test_run_gh_detects_saml_sso_error(self):
        """_run_gh must catch SAML/SSO errors and return 401."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (
            b"",
            b"Resource protected by organization SAML enforcement. "
            b"You must grant your OAuth token access to this organization.",
        )
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(pr_preview.HTTPException) as exc_info:
                await pr_preview._run_gh("repos/org/repo/pulls/1")
        # "saml" keyword should trigger 401, not 502
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_run_gh_detects_sso_keyword_error(self):
        """_run_gh must catch errors mentioning SSO and return 401."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (
            b"",
            b"SSO authorization required for organization linkedin",
        )
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(pr_preview.HTTPException) as exc_info:
                await pr_preview._run_gh("repos/org/repo/pulls/1")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_run_gh_detects_bad_credentials(self):
        """_run_gh must catch 'Bad credentials' errors and return 401."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (
            b"",
            b"gh: Bad credentials (HTTP 401)",
        )
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(pr_preview.HTTPException) as exc_info:
                await pr_preview._run_gh("repos/org/repo/pulls/1")
        assert exc_info.value.status_code == 401

    def test_gh_env_has_browser_vars_set(self):
        """The module-level _GH_ENV dict must suppress browser opening."""
        assert pr_preview._GH_ENV["GH_BROWSER"] == "true"
        assert pr_preview._GH_ENV["BROWSER"] == "true"

    def test_auto_merge_passes_env(self, client):
        """gh pr merge subprocess must use _GH_ENV."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            client.post("/api/pr-auto-merge?url=https://github.com/org/repo/pull/42&enable=true")

        _, kwargs = mock_exec.call_args
        env = kwargs.get("env", {})
        assert env.get("GH_BROWSER") == "true"
        assert env.get("BROWSER") == "true"

    def test_pr_ready_passes_env(self, client):
        """gh pr ready subprocess must use _GH_ENV."""
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            client.post("/api/pr-ready?url=https://github.com/org/repo/pull/42")

        _, kwargs = mock_exec.call_args
        env = kwargs.get("env", {})
        assert env.get("GH_BROWSER") == "true"
        assert env.get("BROWSER") == "true"
