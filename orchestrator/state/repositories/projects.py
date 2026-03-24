"""Repository for projects table."""

import sqlite3
import uuid

from orchestrator.state.models import Project, generate_task_prefix


def get_project(conn: sqlite3.Connection, id: str) -> Project | None:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (id,)).fetchone()
    if row is None:
        return None
    return Project(**dict(row))


def list_projects(conn: sqlite3.Connection, status: str | None = None) -> list[Project]:
    if status:
        rows = conn.execute(
            "SELECT * FROM projects WHERE status = ? ORDER BY starred DESC, name",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM projects ORDER BY starred DESC, name").fetchall()
    return [Project(**dict(r)) for r in rows]


def create_project(
    conn: sqlite3.Connection,
    name: str,
    description: str | None = None,
    target_date: str | None = None,
    task_prefix: str | None = None,
) -> Project:
    id = str(uuid.uuid4())
    # Auto-generate task_prefix if not provided
    if task_prefix is None:
        task_prefix = generate_task_prefix(name)
    conn.execute(
        """INSERT INTO projects (id, name, description, target_date, task_prefix)
           VALUES (?, ?, ?, ?, ?)""",
        (id, name, description, target_date, task_prefix),
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
    task_prefix: str | None = None,
    starred: bool | None = None,
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
    if task_prefix is not None:
        sets.append("task_prefix = ?")
        params.append(task_prefix)
    if starred is not None:
        sets.append("starred = ?")
        params.append(starred)
    if not sets:
        return get_project(conn, id)
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(id)
    conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    return get_project(conn, id)


def delete_project(conn: sqlite3.Connection, id: str) -> bool:
    cursor = conn.execute("DELETE FROM projects WHERE id = ?", (id,))
    conn.commit()
    return cursor.rowcount > 0
