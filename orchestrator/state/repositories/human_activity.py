"""Repository for human_activity_events table — tracks human user active time."""

import sqlite3
from datetime import UTC, datetime, timedelta

from orchestrator.utils import utc_now_iso


def start_interval(conn: sqlite3.Connection) -> int:
    """Start a new active interval. Returns the row id."""
    cursor = conn.execute(
        "INSERT INTO human_activity_events (start_time) VALUES (?)",
        (utc_now_iso(),),
    )
    return cursor.lastrowid


def close_interval(conn: sqlite3.Connection, interval_id: int, end_time: str) -> None:
    """Close an active interval by setting its end_time."""
    conn.execute(
        "UPDATE human_activity_events SET end_time = ? WHERE id = ?",
        (end_time, interval_id),
    )


def get_open_interval(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Find the open interval (end_time IS NULL). Should be 0 or 1 rows."""
    return conn.execute(
        "SELECT id, start_time FROM human_activity_events WHERE end_time IS NULL LIMIT 1"
    ).fetchone()


def close_stale_intervals(conn: sqlite3.Connection, idle_timeout_seconds: int = 300) -> None:
    """Startup recovery: close any open intervals with end_time = start_time + idle_timeout."""
    rows = conn.execute(
        "SELECT id, start_time FROM human_activity_events WHERE end_time IS NULL"
    ).fetchall()
    for row in rows:
        start = _parse_ts(row["start_time"])
        end_time = (start + timedelta(seconds=idle_timeout_seconds)).isoformat()
        conn.execute(
            "UPDATE human_activity_events SET end_time = ? WHERE id = ?",
            (end_time, row["id"]),
        )
    if rows:
        conn.commit()


def query_human_hours(conn: sqlite3.Connection, since_date: str) -> list[dict]:
    """Human-hours per day since the given date.

    Clamps open intervals to now, splits cross-midnight intervals.
    Returns [{date, hours}] sorted by date ascending.
    """
    rows = conn.execute(
        """SELECT start_time, end_time
           FROM human_activity_events
           WHERE start_time >= ?
           ORDER BY start_time""",
        (since_date,),
    ).fetchall()

    now = datetime.now(UTC)
    hours_by_date: dict[str, float] = {}

    for r in rows:
        start = _parse_ts(r["start_time"])
        end = _parse_ts(r["end_time"]) if r["end_time"] else now
        _add_interval(hours_by_date, start, end)

    return [{"date": d, "hours": round(h, 2)} for d, h in sorted(hours_by_date.items())]


def query_human_hours_detail(conn: sqlite3.Connection, date: str) -> list[dict]:
    """Per-interval detail for a specific local date.

    Returns [{start, end, hours}] clamped to target date boundaries.
    """
    target_start = datetime.strptime(date, "%Y-%m-%d").astimezone()
    target_end = target_start + timedelta(days=1)

    # Fetch with margin for cross-midnight and timezone-offset intervals
    fetch_since = (target_start - timedelta(days=1)).astimezone(UTC).isoformat()
    fetch_until = (target_end + timedelta(hours=1)).astimezone(UTC).isoformat()

    rows = conn.execute(
        """SELECT start_time, end_time
           FROM human_activity_events
           WHERE start_time >= ? AND start_time < ?
           ORDER BY start_time""",
        (fetch_since, fetch_until),
    ).fetchall()

    now = datetime.now(UTC)
    items = []

    for r in rows:
        start = _parse_ts(r["start_time"])
        end = _parse_ts(r["end_time"]) if r["end_time"] else now

        # Clamp to target date boundaries
        clamped_start = max(start, target_start)
        clamped_end = min(end, target_end)
        if clamped_start >= clamped_end:
            continue

        hours = (clamped_end - clamped_start).total_seconds() / 3600
        items.append(
            {
                "start": clamped_start.isoformat(),
                "end": clamped_end.isoformat(),
                "hours": round(hours, 2),
            }
        )

    return items


def cleanup_old_events(conn: sqlite3.Connection, retention_days: int = 180) -> None:
    """Delete events older than retention_days."""
    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
    conn.execute("DELETE FROM human_activity_events WHERE start_time < ?", (cutoff,))
    conn.commit()


def _parse_ts(ts_str: str) -> datetime:
    """Parse an ISO timestamp, handling timezone info."""
    try:
        return datetime.fromisoformat(ts_str)
    except ValueError:
        return datetime.fromisoformat(ts_str).replace(tzinfo=UTC)


def _add_interval(hours_by_date: dict[str, float], start: datetime, end: datetime) -> None:
    """Add an interval to hours_by_date, splitting at local midnight boundaries."""
    if start >= end:
        return

    start = start.astimezone()
    end = end.astimezone()

    current = start
    while current < end:
        next_midnight = (current + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        interval_end = min(end, next_midnight)
        day_key = current.strftime("%Y-%m-%d")
        hours = (interval_end - current).total_seconds() / 3600
        hours_by_date[day_key] = hours_by_date.get(day_key, 0) + hours
        current = interval_end
