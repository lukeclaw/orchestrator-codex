"""Trends API: historical throughput, worker heatmap, and worker-hours."""

import sqlite3
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

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
    since = (datetime.now().astimezone() - timedelta(days=days)).strftime("%Y-%m-%d")

    throughput = status_events.query_throughput(conn, since)
    heatmap = status_events.query_worker_heatmap(conn, since)
    worker_hours = status_events.query_worker_hours(conn, since)

    return {
        "range": range if range in VALID_RANGES else "7d",
        "throughput": throughput,
        "heatmap": heatmap,
        "worker_hours": worker_hours,
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

    elif chart == "heatmap":
        if day_of_week is None or hour is None:
            raise HTTPException(status_code=400, detail="day_of_week and hour are required for heatmap detail")
        days = VALID_RANGES.get(range, 7)
        since = (datetime.now().astimezone() - timedelta(days=days)).strftime("%Y-%m-%d")
        items = status_events.query_heatmap_detail(conn, day_of_week, hour, since)
        return {"chart": chart, "day_of_week": day_of_week, "hour": hour, "items": items}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown chart type: {chart}")
