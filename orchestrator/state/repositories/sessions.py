"""Repository for sessions table."""

import logging
import sqlite3
import uuid

from orchestrator.providers import DEFAULT_PROVIDER_ID
from orchestrator.session.state_machine import SessionStatus
from orchestrator.state.db import transaction, with_retry
from orchestrator.state.models import Session
from orchestrator.utils import utc_now_iso

logger = logging.getLogger(__name__)

# Valid status strings (derived from the state machine enum)
_VALID_STATUSES = {s.value for s in SessionStatus}


def get_session(conn: sqlite3.Connection, id: str) -> Session | None:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (id,)).fetchone()
    if row is None:
        return None
    return Session(**dict(row))


def get_session_by_name(conn: sqlite3.Connection, name: str) -> Session | None:
    row = conn.execute("SELECT * FROM sessions WHERE name = ?", (name,)).fetchone()
    if row is None:
        return None
    return Session(**dict(row))


def list_sessions(
    conn: sqlite3.Connection,
    status: str | None = None,
    session_type: str | None = None,
) -> list[Session]:
    """List sessions with optional filters.

    Args:
        status: Filter by session status (idle, working, etc.)
        session_type: Filter by session type (worker, brain, system)
    """
    conditions = []
    params = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if session_type:
        conditions.append("session_type = ?")
        params.append(session_type)

    query = "SELECT * FROM sessions"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY name"

    rows = conn.execute(query, params).fetchall()
    return [Session(**dict(r)) for r in rows]


@with_retry
def create_session(
    conn: sqlite3.Connection,
    name: str,
    host: str,
    work_dir: str | None = None,
    session_type: str = "worker",
    provider: str = DEFAULT_PROVIDER_ID,
) -> Session:
    id = str(uuid.uuid4())
    now = utc_now_iso()
    conn.execute(
        """INSERT INTO sessions
           (id, name, host, work_dir, session_type, provider,
            last_status_changed_at, claude_session_id, auto_reconnect)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        (id, name, host, work_dir, session_type, provider or DEFAULT_PROVIDER_ID, now, id),
    )
    conn.commit()
    return get_session(conn, id)


@with_retry
def update_session(
    conn: sqlite3.Connection,
    id: str,
    status: str | None = None,
    tunnel_pid: int | None = ...,
    takeover_mode: bool | None = None,
    last_viewed_at: str | None = None,
    auto_reconnect: bool | None = None,
    claude_session_id: str | None = None,
    work_dir: str | None = ...,
    rws_pty_id: str | None = ...,
) -> Session | None:
    sets = []
    params = []
    # Auto-update last_status_changed_at whenever status changes
    if status is not None:
        if status not in _VALID_STATUSES:
            logger.error("Rejected invalid status %r for session %s", status, id)
            raise ValueError(
                f"Invalid session status: {status!r}. Valid: {sorted(_VALID_STATUSES)}"
            )
        sets.append("status = ?")
        params.append(status)
        sets.append("last_status_changed_at = ?")
        params.append(utc_now_iso())
    if tunnel_pid is not ...:
        sets.append("tunnel_pid = ?")
        params.append(tunnel_pid)
    if takeover_mode is not None:
        sets.append("takeover_mode = ?")
        params.append(takeover_mode)
    if last_viewed_at is not None:
        sets.append("last_viewed_at = ?")
        params.append(last_viewed_at)
    if auto_reconnect is not None:
        sets.append("auto_reconnect = ?")
        params.append(auto_reconnect)
    if claude_session_id is not None:
        sets.append("claude_session_id = ?")
        params.append(claude_session_id)
    if work_dir is not ...:
        sets.append("work_dir = ?")
        params.append(work_dir)
    if rws_pty_id is not ...:
        sets.append("rws_pty_id = ?")
        params.append(rws_pty_id)
    if not sets:
        return get_session(conn, id)

    # Read old status before UPDATE for event tracking
    old_status = None
    session_type = None
    session_name = None
    task_id = None
    task_title = None
    if status is not None:
        row = conn.execute(
            "SELECT status, session_type, name FROM sessions WHERE id = ?", (id,)
        ).fetchone()
        if row:
            old_status = row["status"]
            session_type = row["session_type"]
            session_name = row["name"]

            # Look up currently assigned task for worker sessions
            if session_type == "worker":
                from orchestrator.state.repositories import tasks as tasks_repo

                assigned = tasks_repo.list_tasks(conn, assigned_session_id=id, parent_task_id=...)
                for t in assigned:
                    if t.status == "in_progress":
                        task_id, task_title = t.id, t.title
                        break
                else:
                    for t in assigned:
                        if t.status == "todo":
                            task_id, task_title = t.id, t.title
                            break

    params.append(id)
    conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", params)

    # Insert status event if status actually changed
    if status is not None and old_status is not None and old_status != status:
        try:
            from orchestrator.state.repositories.status_events import insert_event

            insert_event(
                conn,
                "session",
                id,
                old_status,
                status,
                session_type=session_type,
                session_name=session_name,
                task_id=task_id,
                task_title=task_title,
            )
        except Exception:
            logger.debug("Failed to insert status event for session %s", id, exc_info=True)

    conn.commit()
    return get_session(conn, id)


@with_retry
def delete_session(conn: sqlite3.Connection, id: str) -> bool:
    """Delete a session and all related records.

    Uses a single transaction to avoid partial deletes and reduce lock time.
    Records a closing status event before deletion so that trend queries
    don't treat the last 'working' event as an open interval extending to now.
    """
    with transaction(conn):
        # Insert a closing status event so worker-hours charts don't overcount.
        row = conn.execute(
            "SELECT status, session_type, name FROM sessions WHERE id = ?", (id,)
        ).fetchone()
        if row and row["status"] in ("working", "connecting"):
            try:
                from orchestrator.state.repositories.status_events import insert_event

                insert_event(
                    conn,
                    "session",
                    id,
                    row["status"],
                    "idle",
                    session_type=row["session_type"],
                    session_name=row["name"],
                )
            except Exception:
                logger.debug("Failed to insert closing event for session %s", id, exc_info=True)

        conn.execute(
            "UPDATE tasks SET assigned_session_id = NULL WHERE assigned_session_id = ?", (id,)
        )
        cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (id,))
    return cursor.rowcount > 0
