"""Trends API: historical throughput, worker heatmap, and worker-hours."""

import sqlite3
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from orchestrator.api.deps import get_db
from orchestrator.api.routes.prs import _search_cache as pr_search_cache
from orchestrator.state.repositories import human_activity, status_events

router = APIRouter()

VALID_RANGES = {"7d": 7, "30d": 30, "90d": 90}


@router.get("/trends")
def get_trends(
    range: str = Query("7d", alias="range"),
    conn: sqlite3.Connection = Depends(get_db),
):
    days = VALID_RANGES.get(range, 7)
    since = (datetime.now().astimezone() - timedelta(days=days)).strftime("%Y-%m-%d")
    # Heatmap groups by day-of-week so we need exactly N days (no DOW
    # collisions).  Use a full UTC timestamp of midnight local time
    # (days-1) ago so the boundary respects the user's timezone.
    local_start = (datetime.now().astimezone() - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    heatmap_since = local_start.astimezone(UTC).isoformat()

    throughput = status_events.query_throughput(conn, since)
    heatmap = status_events.query_worker_heatmap(conn, heatmap_since)
    worker_hours = status_events.query_worker_hours(conn, since)
    human_hours = human_activity.query_human_hours(conn, since)

    return {
        "range": range if range in VALID_RANGES else "7d",
        "throughput": throughput,
        "heatmap": heatmap,
        "worker_hours": worker_hours,
        "human_hours": human_hours,
    }


@router.get("/trends/detail")
def get_trend_detail(
    chart: str = Query(...),
    date: str | None = Query(None),
    day_of_week: int | None = Query(None),
    hour: int | None = Query(None),
    range: str = Query("7d", alias="range"),
    conn: sqlite3.Connection = Depends(get_db),
):
    if chart == "throughput":
        if not date:
            raise HTTPException(status_code=400, detail="date is required for throughput detail")
        items = status_events.query_throughput_detail(conn, date)
        return {"chart": chart, "date": date, "items": items}

    elif chart == "worker_hours":
        if not date:
            raise HTTPException(status_code=400, detail="date is required for worker_hours detail")
        items = status_events.query_worker_hours_detail(conn, date)
        return {"chart": chart, "date": date, "items": items}

    elif chart == "human_hours":
        if not date:
            raise HTTPException(status_code=400, detail="date is required for human_hours detail")
        items = human_activity.query_human_hours_detail(conn, date)
        return {"chart": chart, "date": date, "items": items}

    elif chart == "heatmap":
        if day_of_week is None or hour is None:
            raise HTTPException(
                status_code=400,
                detail="day_of_week and hour are required for heatmap detail",
            )
        days = VALID_RANGES.get(range, 7)
        local_start = (datetime.now().astimezone() - timedelta(days=days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        since = local_start.astimezone(UTC).isoformat()
        items = status_events.query_heatmap_detail(conn, day_of_week, hour, since)
        return {"chart": chart, "day_of_week": day_of_week, "hour": hour, "items": items}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown chart type: {chart}")


@router.get("/trends/pr-merges")
def get_pr_merge_throughput(
    range: str = Query("30d", alias="range"),
):
    """Merged PRs per day, extracted from the PR search cache.

    Reads from the same data source as the PRs page to ensure consistency
    (same GitHub account, same paginated results).
    """
    range_key = range if range in VALID_RANGES else "30d"
    days = VALID_RANGES.get(range_key, 30)
    cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")

    # Find the best matching cache entry — prefer exact days match,
    # fall back to any recent entry with enough range.
    prs: list[dict] = []
    exact_key = f"recent:{days}"
    if exact_key in pr_search_cache:
        _, cached = pr_search_cache[exact_key]
        prs = cached.get("prs", [])
    else:
        # Fall back to any recent cache entry
        for key, (_, cached) in pr_search_cache.items():
            if key.startswith("recent:"):
                prs = cached.get("prs", [])
                break

    by_day: dict[str, dict] = {}
    for pr in prs:
        merged_at = pr.get("merged_at")
        if not merged_at:
            continue
        try:
            dt = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        day = dt.astimezone().strftime("%Y-%m-%d")
        if day < cutoff:
            continue
        if day not in by_day:
            by_day[day] = {"date": day, "count": 0, "prs": []}
        by_day[day]["count"] += 1
        by_day[day]["prs"].append(
            {
                "url": pr.get("url", ""),
                "number": pr.get("number", 0),
                "title": pr.get("title", ""),
                "repo": pr.get("repo", ""),
                "merged_at": merged_at,
                "additions": pr.get("additions", 0),
                "deletions": pr.get("deletions", 0),
            }
        )

    return sorted(by_day.values(), key=lambda x: x["date"])
