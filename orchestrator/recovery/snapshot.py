"""Periodically save session context snapshots to DB."""

from __future__ import annotations

import json
import sqlite3
import uuid

from orchestrator.state.db import with_retry
from orchestrator.state.models import SessionSnapshot
from orchestrator.state.repositories import sessions, tasks


@with_retry
def create_snapshot(conn: sqlite3.Connection, session_id: str) -> SessionSnapshot:
    """Create a snapshot of the current session context."""
    session = sessions.get_session(conn, session_id)
    if session is None:
        raise ValueError(f"Session not found: {session_id}")

    # Current task summary - look up by assigned_session_id
    task_summary = None
    assigned_tasks = tasks.list_tasks(conn, assigned_session_id=session_id)
    if assigned_tasks:
        task = assigned_tasks[0]
        task_summary = f"{task.title}: {task.description or 'No description'} (status: {task.status})"

    # Key decisions
    from orchestrator.state.repositories.decisions import list_decisions
    recent_decisions = list_decisions(conn, session_id=session_id)[:5]
    key_decisions = json.dumps([
        {"question": d.question, "response": d.response, "status": d.status}
        for d in recent_decisions
    ])

    # Last known state
    last_state = f"Session {session.name}: status={session.status}, host={session.host}"
    if session.work_dir:
        last_state += f", path={session.work_dir}"

    snapshot_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO session_snapshots
           (id, session_id, task_summary, key_decisions, file_paths, last_known_state)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (snapshot_id, session_id, task_summary, key_decisions, None, last_state),
    )
    conn.commit()

    row = conn.execute("SELECT * FROM session_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    return SessionSnapshot(**dict(row))


def get_latest_snapshot(conn: sqlite3.Connection, session_id: str) -> SessionSnapshot | None:
    """Get the most recent snapshot for a session."""
    row = conn.execute(
        """SELECT * FROM session_snapshots
           WHERE session_id = ?
           ORDER BY created_at DESC LIMIT 1""",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return SessionSnapshot(**dict(row))
