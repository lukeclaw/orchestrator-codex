"""Repository for status_events table — append-only event log for trends."""

import sqlite3
from datetime import datetime, timezone, timedelta

from orchestrator.utils import utc_now_iso


def insert_event(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    old_status: str | None,
    new_status: str,
    is_subtask: bool = False,
    session_type: str | None = None,
) -> None:
    """Insert a status change event. Does NOT commit — caller handles that."""
    conn.execute(
        """INSERT INTO status_events (entity_type, entity_id, old_status, new_status, is_subtask, session_type, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (entity_type, entity_id, old_status, new_status, int(is_subtask), session_type, utc_now_iso()),
    )


def query_throughput(conn: sqlite3.Connection, since_date: str) -> list[dict]:
    """Completed tasks/subtasks per day since the given date.

    Returns [{date, tasks, subtasks}] sorted by date ascending.
    """
    rows = conn.execute(
        """SELECT
               date(timestamp) as day,
               SUM(CASE WHEN is_subtask = 0 THEN 1 ELSE 0 END) as tasks,
               SUM(CASE WHEN is_subtask = 1 THEN 1 ELSE 0 END) as subtasks
           FROM status_events
           WHERE entity_type = 'task'
             AND new_status = 'done'
             AND timestamp >= ?
           GROUP BY day
           ORDER BY day""",
        (since_date,),
    ).fetchall()
    return [{"date": r["day"], "tasks": r["tasks"], "subtasks": r["subtasks"]} for r in rows]


def query_worker_heatmap(conn: sqlite3.Connection, since_date: str) -> list[dict]:
    """Worker activity heatmap: count of 'working' transitions by day-of-week and hour.

    Returns [{day_of_week (0=Sun), hour, count}].
    """
    rows = conn.execute(
        """SELECT
               CAST(strftime('%%w', timestamp) AS INTEGER) as day_of_week,
               CAST(strftime('%%H', timestamp) AS INTEGER) as hour,
               COUNT(*) as count
           FROM status_events
           WHERE entity_type = 'session'
             AND new_status = 'working'
             AND session_type = 'worker'
             AND timestamp >= ?
           GROUP BY day_of_week, hour""",
        (since_date,),
    ).fetchall()
    return [{"day_of_week": r["day_of_week"], "hour": r["hour"], "count": r["count"]} for r in rows]


def query_worker_hours(conn: sqlite3.Connection, since_date: str) -> list[dict]:
    """Worker-hours per day: total hours workers spent in 'working' status.

    Computes intervals from working→non-working transitions.
    Open intervals (still working) are clamped to now.
    Cross-midnight intervals are split at midnight boundaries.

    Returns [{date, hours}] sorted by date ascending.
    """
    rows = conn.execute(
        """SELECT entity_id, new_status, timestamp
           FROM status_events
           WHERE entity_type = 'session'
             AND session_type = 'worker'
             AND timestamp >= ?
           ORDER BY entity_id, timestamp""",
        (since_date,),
    ).fetchall()

    # Group events by worker
    workers: dict[str, list[tuple[str, str]]] = {}
    for r in rows:
        wid = r["entity_id"]
        if wid not in workers:
            workers[wid] = []
        workers[wid].append((r["new_status"], r["timestamp"]))

    now = datetime.now(timezone.utc)
    hours_by_date: dict[str, float] = {}

    for events in workers.values():
        work_start: datetime | None = None

        for status, ts_str in events:
            ts = _parse_ts(ts_str)
            if status == "working":
                work_start = ts
            elif work_start is not None:
                # End of working interval
                _add_interval(hours_by_date, work_start, ts)
                work_start = None

        # Clamp open interval to now
        if work_start is not None:
            _add_interval(hours_by_date, work_start, now)

    result = [{"date": d, "hours": round(h, 2)} for d, h in sorted(hours_by_date.items())]
    return result


def _parse_ts(ts_str: str) -> datetime:
    """Parse an ISO timestamp, handling timezone info."""
    try:
        return datetime.fromisoformat(ts_str)
    except ValueError:
        # Fallback for timestamps without timezone
        return datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)


def _add_interval(hours_by_date: dict[str, float], start: datetime, end: datetime) -> None:
    """Add a working interval to hours_by_date, splitting at midnight boundaries."""
    if start >= end:
        return

    current = start
    while current < end:
        # End of current day (midnight)
        next_midnight = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        interval_end = min(end, next_midnight)
        day_key = current.strftime("%Y-%m-%d")
        hours = (interval_end - current).total_seconds() / 3600
        hours_by_date[day_key] = hours_by_date.get(day_key, 0) + hours
        current = interval_end


def cleanup_old_events(conn: sqlite3.Connection, retention_days: int = 180) -> None:
    """Delete events older than retention_days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    conn.execute("DELETE FROM status_events WHERE timestamp < ?", (cutoff,))
    conn.commit()
