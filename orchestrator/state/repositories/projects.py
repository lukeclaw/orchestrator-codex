"""Repository for projects and project_workers tables."""

import sqlite3
import uuid

from orchestrator.state.models import Project, ProjectWorker


def get_project(conn: sqlite3.Connection, id: str) -> Project | None:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (id,)).fetchone()
    if row is None:
        return None
    return Project(**dict(row))


def list_projects(conn: sqlite3.Connection, status: str | None = None) -> list[Project]:
    if status:
        rows = conn.execute(
            "SELECT * FROM projects WHERE status = ? ORDER BY name", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
    return [Project(**dict(r)) for r in rows]


def create_project(
    conn: sqlite3.Connection,
    name: str,
    description: str | None = None,
    target_date: str | None = None,
) -> Project:
    id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO projects (id, name, description, target_date)
           VALUES (?, ?, ?, ?)""",
        (id, name, description, target_date),
    )
    conn.commit()
    return get_project(conn, id)


def update_project(
    conn: sqlite3.Connection,
    id: str,
    name: str | None = None,
    description: str | None = None,
    status: str | None = None,
    target_date: str | None = ...,
) -> Project | None:
    sets = []
    params = []
    if name is not None:
        sets.append("name = ?")
        params.append(name)
    if description is not None:
        sets.append("description = ?")
        params.append(description)
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if target_date is not ...:
        sets.append("target_date = ?")
        params.append(target_date)
    if not sets:
        return get_project(conn, id)
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(id)
    conn.execute(
        f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", params
    )
    conn.commit()
    return get_project(conn, id)


def delete_project(conn: sqlite3.Connection, id: str) -> bool:
    conn.execute("DELETE FROM project_workers WHERE project_id = ?", (id,))
    cursor = conn.execute("DELETE FROM projects WHERE id = ?", (id,))
    conn.commit()
    return cursor.rowcount > 0


# --- Project Workers ---

def assign_worker(conn: sqlite3.Connection, project_id: str, session_id: str) -> ProjectWorker:
    conn.execute(
        """INSERT OR IGNORE INTO project_workers (project_id, session_id)
           VALUES (?, ?)""",
        (project_id, session_id),
    )
    conn.commit()
    return ProjectWorker(project_id, session_id)


def unassign_worker(conn: sqlite3.Connection, project_id: str, session_id: str) -> bool:
    cursor = conn.execute(
        "DELETE FROM project_workers WHERE project_id = ? AND session_id = ?",
        (project_id, session_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def list_project_workers(conn: sqlite3.Connection, project_id: str) -> list[ProjectWorker]:
    rows = conn.execute(
        "SELECT * FROM project_workers WHERE project_id = ?", (project_id,)
    ).fetchall()
    return [ProjectWorker(**dict(r)) for r in rows]
