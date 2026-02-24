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
    """Completed tasks/subtasks for a specific date with enriched task data.

    Returns [{entity_id, is_subtask, timestamp, title, task_key, status,
              parent_task_id, parent_title, parent_task_key}].
    """
    rows = conn.execute(
        """SELECT entity_id, is_subtask, timestamp
           FROM status_events
           WHERE entity_type = 'task'
             AND new_status = 'done'
             AND date(timestamp) = ?
           ORDER BY timestamp""",
        (date,),
    ).fetchall()

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
        items.append({
            "entity_id": r["entity_id"],
            "is_subtask": bool(r["is_subtask"]),
            "timestamp": r["timestamp"],
            "title": title,
            "task_key": task_key,
            "status": status,
            "parent_task_id": parent_task_id,
            "parent_title": parent_title,
            "parent_task_key": parent_task_key,
        })
    return items


def query_worker_hours_detail(conn: sqlite3.Connection, date: str) -> list[dict]:
    """Per-worker hour breakdown for a specific date.

    Returns [{session_id, session_name, total_hours,
              intervals: [{start, end}], current_task: {id, title}|null}].
    """
    # Fetch events from prev day through target date to capture cross-midnight intervals
    prev_date = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT entity_id, new_status, timestamp
           FROM status_events
           WHERE entity_type = 'session'
             AND session_type = 'worker'
             AND timestamp >= ?
             AND timestamp < date(?, '+1 day')
           ORDER BY entity_id, timestamp""",
        (prev_date, date),
    ).fetchall()

    # Group events by worker
    workers: dict[str, list[tuple[str, str]]] = {}
    for r in rows:
        wid = r["entity_id"]
        if wid not in workers:
            workers[wid] = []
        workers[wid].append((r["new_status"], r["timestamp"]))

    now = datetime.now(UTC)
    target_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
    target_end = target_start + timedelta(days=1)

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
                    intervals.append({
                        "start": clamped_start.isoformat(),
                        "end": clamped_end.isoformat(),
                    })
                work_start = None

        # Clamp open interval to now (or target_end)
        if work_start is not None:
            clamped_start = max(work_start, target_start)
            clamped_end = min(now, target_end)
            if clamped_start < clamped_end:
                intervals.append({
                    "start": clamped_start.isoformat(),
                    "end": clamped_end.isoformat(),
                })

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

        items.append({
            "session_id": wid,
            "session_name": session_name,
            "total_hours": round(total_hours, 2),
            "intervals": intervals,
            "current_task": current_task,
        })

    # Sort by total hours descending
    items.sort(key=lambda x: x["total_hours"], reverse=True)
    return items


def query_heatmap_detail(conn: sqlite3.Connection, day_of_week: int, hour: int, since_date: str) -> list[dict]:
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
        items.append({
            "date": ts.strftime("%Y-%m-%d"),
            "session_id": r["entity_id"],
            "session_name": session_name,
            "timestamp": r["timestamp"],
        })
    return items


def cleanup_old_events(conn: sqlite3.Connection, retention_days: int = 180) -> None:
    """Delete events older than retention_days."""
    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
    conn.execute("DELETE FROM status_events WHERE timestamp < ?", (cutoff,))
    conn.commit()
