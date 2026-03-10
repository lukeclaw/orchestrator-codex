"""Repository for status_events table — append-only event log for trends."""

import sqlite3
from datetime import UTC, datetime, timedelta

from orchestrator.state.repositories import projects as projects_repo
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.state.repositories import tasks as tasks_repo
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
        """INSERT INTO status_events
           (entity_type, entity_id, old_status, new_status, is_subtask, session_type, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            entity_type,
            entity_id,
            old_status,
            new_status,
            int(is_subtask),
            session_type,
            utc_now_iso(),
        ),
    )


def query_throughput(conn: sqlite3.Connection, since_date: str) -> list[dict]:
    """Completed tasks/subtasks per day since the given date.

    Groups by local-timezone date so chart labels match the user's calendar.
    Returns [{date, tasks, subtasks}] sorted by date ascending.
    """
    rows = conn.execute(
        """SELECT is_subtask, timestamp
           FROM status_events
           WHERE entity_type = 'task'
             AND new_status = 'done'
             AND timestamp >= ?
           ORDER BY timestamp""",
        (since_date,),
    ).fetchall()

    by_day: dict[str, dict] = {}
    for r in rows:
        ts = _parse_ts(r["timestamp"])
        day = ts.astimezone().strftime("%Y-%m-%d")  # Local date
        if day not in by_day:
            by_day[day] = {"date": day, "tasks": 0, "subtasks": 0}
        if r["is_subtask"]:
            by_day[day]["subtasks"] += 1
        else:
            by_day[day]["tasks"] += 1

    return sorted(by_day.values(), key=lambda x: x["date"])


def query_worker_heatmap(conn: sqlite3.Connection, since_date: str) -> list[dict]:
    """Worker activity heatmap: count of 'working' transitions by day-of-week and hour.

    Returns [{day_of_week (0=Sun), hour, count}].
    """
    rows = conn.execute(
        """SELECT
               CAST(strftime('%w', timestamp) AS INTEGER) as day_of_week,
               CAST(strftime('%H', timestamp) AS INTEGER) as hour,
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

    now = datetime.now(UTC)
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
        return datetime.fromisoformat(ts_str).replace(tzinfo=UTC)


def _add_interval(hours_by_date: dict[str, float], start: datetime, end: datetime) -> None:
    """Add a working interval to hours_by_date, splitting at local midnight boundaries."""
    if start >= end:
        return

    # Convert to local timezone so hours are attributed to the user's local date
    start = start.astimezone()
    end = end.astimezone()

    current = start
    while current < end:
        # End of current day (local midnight)
        next_midnight = (current + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        interval_end = min(end, next_midnight)
        day_key = current.strftime("%Y-%m-%d")
        hours = (interval_end - current).total_seconds() / 3600
        hours_by_date[day_key] = hours_by_date.get(day_key, 0) + hours
        current = interval_end


def _get_task_key(task, conn) -> str | None:
    """Generate human-readable task key like UTI-1 or UTI-1-1 for subtasks."""
    if task.task_index is None:
        return None
    project = projects_repo.get_project(conn, task.project_id)
    if not project or not project.task_prefix:
        return None
    if task.parent_task_id:
        parent = tasks_repo.get_task(conn, task.parent_task_id)
        if parent and parent.task_index is not None:
            return f"{project.task_prefix}-{parent.task_index}-{task.task_index}"
    return f"{project.task_prefix}-{task.task_index}"


def query_throughput_detail(conn: sqlite3.Connection, date: str) -> list[dict]:
    """Completed tasks/subtasks for a specific date (local timezone) with enriched task data.

    Returns [{entity_id, is_subtask, timestamp, title, task_key, status,
              parent_task_id, parent_title, parent_task_key}].
    """
    # Fetch a wider range and filter by local date in Python
    rows = conn.execute(
        """SELECT entity_id, is_subtask, timestamp
           FROM status_events
           WHERE entity_type = 'task'
             AND new_status = 'done'
             AND timestamp >= date(?, '-1 day')
             AND timestamp < date(?, '+2 days')
           ORDER BY timestamp""",
        (date, date),
    ).fetchall()
    # Filter to events whose local date matches the requested date
    rows = [r for r in rows if _parse_ts(r["timestamp"]).astimezone().strftime("%Y-%m-%d") == date]

    items = []
    for r in rows:
        task = tasks_repo.get_task(conn, r["entity_id"])
        title = task.title if task else "Unknown"
        status = task.status if task else "done"
        task_key = _get_task_key(task, conn) if task else None
        parent_task_id = task.parent_task_id if task else None
        parent_title = None
        parent_task_key = None
        if parent_task_id:
            parent = tasks_repo.get_task(conn, parent_task_id)
            if parent:
                parent_title = parent.title
                parent_task_key = _get_task_key(parent, conn)
        items.append(
            {
                "entity_id": r["entity_id"],
                "is_subtask": bool(r["is_subtask"]),
                "timestamp": r["timestamp"],
                "title": title,
                "task_key": task_key,
                "status": status,
                "parent_task_id": parent_task_id,
                "parent_title": parent_title,
                "parent_task_key": parent_task_key,
            }
        )
    return items


def query_worker_hours_detail(conn: sqlite3.Connection, date: str) -> list[dict]:
    """Per-worker hour breakdown for a specific date (local timezone).

    Returns [{session_id, session_name, total_hours,
              intervals: [{start, end}], current_task: {id, title}|null}].
    """
    # Interpret date as local timezone midnight boundaries
    target_start = datetime.strptime(date, "%Y-%m-%d").astimezone()
    target_end = target_start + timedelta(days=1)

    # Fetch events with margin to capture cross-midnight and timezone-offset intervals.
    # Convert to UTC for SQL string comparison (DB timestamps are UTC ISO strings).
    fetch_since = (target_start - timedelta(days=1)).astimezone(UTC).isoformat()
    fetch_until = (target_end + timedelta(hours=1)).astimezone(UTC).isoformat()
    rows = conn.execute(
        """SELECT entity_id, new_status, timestamp
           FROM status_events
           WHERE entity_type = 'session'
             AND session_type = 'worker'
             AND timestamp >= ?
             AND timestamp < ?
           ORDER BY entity_id, timestamp""",
        (fetch_since, fetch_until),
    ).fetchall()

    # Group events by worker
    workers: dict[str, list[tuple[str, str]]] = {}
    for r in rows:
        wid = r["entity_id"]
        if wid not in workers:
            workers[wid] = []
        workers[wid].append((r["new_status"], r["timestamp"]))

    now = datetime.now(UTC)

    items = []
    for wid, events in workers.items():
        work_start: datetime | None = None
        intervals: list[dict] = []

        for status, ts_str in events:
            ts = _parse_ts(ts_str)
            if status == "working":
                work_start = ts
            elif work_start is not None:
                # Clamp interval to target date boundaries
                clamped_start = max(work_start, target_start)
                clamped_end = min(ts, target_end)
                if clamped_start < clamped_end:
                    intervals.append(
                        {
                            "start": clamped_start.isoformat(),
                            "end": clamped_end.isoformat(),
                        }
                    )
                work_start = None

        # Clamp open interval to now (or target_end)
        if work_start is not None:
            clamped_start = max(work_start, target_start)
            clamped_end = min(now, target_end)
            if clamped_start < clamped_end:
                intervals.append(
                    {
                        "start": clamped_start.isoformat(),
                        "end": clamped_end.isoformat(),
                    }
                )

        if not intervals:
            continue

        total_hours = sum(
            (_parse_ts(iv["end"]) - _parse_ts(iv["start"])).total_seconds() / 3600
            for iv in intervals
        )

        session = sessions_repo.get_session(conn, wid)
        session_name = session.name if session else wid

        # V1 task correlation: look up current assignment
        current_task = None
        assigned_tasks = tasks_repo.list_tasks(conn, assigned_session_id=wid, parent_task_id=...)
        for t in assigned_tasks:
            if t.status in ("in_progress", "todo"):
                current_task = {"id": t.id, "title": t.title}
                break

        items.append(
            {
                "session_id": wid,
                "session_name": session_name,
                "total_hours": round(total_hours, 2),
                "intervals": intervals,
                "current_task": current_task,
            }
        )

    # Sort by total hours descending
    items.sort(key=lambda x: x["total_hours"], reverse=True)
    return items


def query_heatmap_detail(
    conn: sqlite3.Connection, day_of_week: int, hour: int, since_date: str
) -> list[dict]:
    """Detail events for a specific heatmap cell (day_of_week + hour).

    Returns [{date, session_id, session_name, timestamp}] ordered by timestamp DESC.
    """
    rows = conn.execute(
        """SELECT entity_id, timestamp
           FROM status_events
           WHERE entity_type = 'session'
             AND new_status = 'working'
             AND session_type = 'worker'
             AND timestamp >= ?
             AND CAST(strftime('%w', timestamp) AS INTEGER) = ?
             AND CAST(strftime('%H', timestamp) AS INTEGER) = ?
           ORDER BY timestamp DESC""",
        (since_date, day_of_week, hour),
    ).fetchall()

    items = []
    for r in rows:
        session = sessions_repo.get_session(conn, r["entity_id"])
        session_name = session.name if session else r["entity_id"]
        ts = _parse_ts(r["timestamp"])
        items.append(
            {
                "date": ts.astimezone().strftime("%Y-%m-%d"),  # Local date
                "session_id": r["entity_id"],
                "session_name": session_name,
                "timestamp": r["timestamp"],
            }
        )
    return items


def cleanup_old_events(conn: sqlite3.Connection, retention_days: int = 180) -> None:
    """Delete events older than retention_days."""
    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
    conn.execute("DELETE FROM status_events WHERE timestamp < ?", (cutoff,))
    conn.commit()
