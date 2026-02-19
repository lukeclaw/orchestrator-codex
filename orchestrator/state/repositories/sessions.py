"""Repository for sessions table."""

import sqlite3
import uuid

from orchestrator.state.db import transaction, with_retry
from orchestrator.utils import utc_now_iso
from orchestrator.state.models import Session


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
) -> Session:
    id = str(uuid.uuid4())
    now = utc_now_iso()
    conn.execute(
        """INSERT INTO sessions (id, name, host, work_dir, session_type, last_status_changed_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (id, name, host, work_dir, session_type, now),
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
) -> Session | None:
    sets = []
    params = []
    # Auto-update last_status_changed_at whenever status changes
    if status is not None:
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
    if not sets:
        return get_session(conn, id)
    params.append(id)
    conn.execute(
        f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", params
    )
    conn.commit()
    return get_session(conn, id)


@with_retry
def delete_session(conn: sqlite3.Connection, id: str) -> bool:
    """Delete a session and all related records.
    
    Uses a single transaction to avoid partial deletes and reduce lock time.
    """
    with transaction(conn):
        conn.execute("UPDATE tasks SET assigned_session_id = NULL WHERE assigned_session_id = ?", (id,))
        cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (id,))
    return cursor.rowcount > 0
