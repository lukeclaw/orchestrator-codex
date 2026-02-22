"""Trends API: historical throughput, worker heatmap, and worker-hours."""

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import status_events

router = APIRouter()

VALID_RANGES = {"7d": 7, "30d": 30, "90d": 90}


@router.get("/trends")
def get_trends(
    range: str = Query("7d", alias="range"),
    conn: sqlite3.Connection = Depends(get_db),
):
    days = VALID_RANGES.get(range, 7)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    throughput = status_events.query_throughput(conn, since)
    heatmap = status_events.query_worker_heatmap(conn, since)
    worker_hours = status_events.query_worker_hours(conn, since)

    return {
        "range": range if range in VALID_RANGES else "7d",
        "throughput": throughput,
        "heatmap": heatmap,
        "worker_hours": worker_hours,
    }
