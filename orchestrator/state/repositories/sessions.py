"""Repository for sessions and worker_capabilities tables."""

import sqlite3
import uuid

from orchestrator.state.db import transaction, with_retry
from orchestrator.state.models import Session, WorkerCapability


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
    tmux_window: str | None = None,
    session_type: str = "worker",
) -> Session:
    id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO sessions (id, name, host, work_dir, tmux_window, session_type)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (id, name, host, work_dir, tmux_window, session_type),
    )
    conn.commit()
    return get_session(conn, id)


@with_retry
def update_session(
    conn: sqlite3.Connection,
    id: str,
    status: str | None = None,
    tmux_window: str | None = None,
    tunnel_pane: str | None = ...,
    takeover_mode: bool | None = None,
    last_activity: str | None = None,
) -> Session | None:
    sets = []
    params = []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if tmux_window is not None:
        sets.append("tmux_window = ?")
        params.append(tmux_window)
    if tunnel_pane is not ...:
        sets.append("tunnel_pane = ?")
        params.append(tunnel_pane)
    if takeover_mode is not None:
        sets.append("takeover_mode = ?")
        params.append(takeover_mode)
    if last_activity is not None:
        sets.append("last_activity = ?")
        params.append(last_activity)
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
        # Clean up all FK references before deleting the session
        conn.execute("DELETE FROM worker_capabilities WHERE session_id = ?", (id,))
        conn.execute("DELETE FROM comm_events WHERE session_id = ?", (id,))
        conn.execute("DELETE FROM session_snapshots WHERE session_id = ?", (id,))
        conn.execute("DELETE FROM project_workers WHERE session_id = ?", (id,))
        conn.execute("UPDATE tasks SET assigned_session_id = NULL WHERE assigned_session_id = ?", (id,))
        cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (id,))
    return cursor.rowcount > 0


# --- Worker Capabilities ---

def get_capabilities(conn: sqlite3.Connection, session_id: str) -> list[WorkerCapability]:
    rows = conn.execute(
        "SELECT * FROM worker_capabilities WHERE session_id = ?", (session_id,)
    ).fetchall()
    return [WorkerCapability(**dict(r)) for r in rows]


def add_capability(
    conn: sqlite3.Connection,
    session_id: str,
    capability_type: str,
    capability_value: str,
) -> WorkerCapability:
    conn.execute(
        """INSERT OR IGNORE INTO worker_capabilities
           (session_id, capability_type, capability_value)
           VALUES (?, ?, ?)""",
        (session_id, capability_type, capability_value),
    )
    conn.commit()
    return WorkerCapability(session_id, capability_type, capability_value)


def remove_capability(
    conn: sqlite3.Connection,
    session_id: str,
    capability_type: str,
    capability_value: str,
) -> bool:
    cursor = conn.execute(
        """DELETE FROM worker_capabilities
           WHERE session_id = ? AND capability_type = ? AND capability_value = ?""",
        (session_id, capability_type, capability_value),
    )
    conn.commit()
    return cursor.rowcount > 0
