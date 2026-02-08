"""Repository for activities table — append-only event log."""

import sqlite3
import uuid

from orchestrator.state.models import Activity


def log_activity(
    conn: sqlite3.Connection,
    event_type: str,
    project_id: str | None = None,
    task_id: str | None = None,
    session_id: str | None = None,
    event_data: str | None = None,
    actor: str = "system",
) -> Activity:
    id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO activities
           (id, project_id, task_id, session_id, event_type, event_data, actor)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (id, project_id, task_id, session_id, event_type, event_data, actor),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM activities WHERE id = ?", (id,)).fetchone()
    return Activity(**dict(row))


def list_activities(
    conn: sqlite3.Connection,
    project_id: str | None = None,
    session_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
) -> list[Activity]:
    clauses = []
    params: list = []
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM activities{where} ORDER BY created_at DESC LIMIT ?", params
    ).fetchall()
    return [Activity(**dict(r)) for r in rows]


def get_activity(conn: sqlite3.Connection, id: str) -> Activity | None:
    row = conn.execute("SELECT * FROM activities WHERE id = ?", (id,)).fetchone()
    if row is None:
        return None
    return Activity(**dict(row))
