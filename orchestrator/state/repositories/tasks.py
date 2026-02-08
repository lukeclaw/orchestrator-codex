"""Repository for tasks, task_dependencies, and task_requirements tables."""

import sqlite3
import uuid

from orchestrator.state.models import Task, TaskDependency, TaskRequirement


def get_task(conn: sqlite3.Connection, id: str) -> Task | None:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (id,)).fetchone()
    if row is None:
        return None
    return Task(**dict(row))


def list_tasks(
    conn: sqlite3.Connection,
    project_id: str | None = None,
    status: str | None = None,
    assigned_session_id: str | None = None,
    parent_task_id: str | None = ...,
) -> list[Task]:
    clauses = []
    params = []
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if assigned_session_id:
        clauses.append("assigned_session_id = ?")
        params.append(assigned_session_id)
    if parent_task_id is not ...:
        if parent_task_id is None:
            clauses.append("parent_task_id IS NULL")
        else:
            clauses.append("parent_task_id = ?")
            params.append(parent_task_id)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM tasks{where} ORDER BY priority DESC, created_at", params
    ).fetchall()
    return [Task(**dict(r)) for r in rows]


def create_task(
    conn: sqlite3.Connection,
    project_id: str,
    title: str,
    description: str | None = None,
    priority: int = 0,
    parent_task_id: str | None = None,
) -> Task:
    id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO tasks (id, project_id, title, description, priority, parent_task_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (id, project_id, title, description, priority, parent_task_id),
    )
    conn.commit()
    return get_task(conn, id)


def update_task(
    conn: sqlite3.Connection,
    id: str,
    status: str | None = None,
    assigned_session_id: str | None = ...,
    priority: int | None = None,
    title: str | None = None,
    description: str | None = None,
    blocked_by_decision_id: str | None = ...,
    notes: str | None = ...,
) -> Task | None:
    sets = []
    params = []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
        if status == "in_progress":
            sets.append("started_at = CURRENT_TIMESTAMP")
        elif status == "done":
            sets.append("completed_at = CURRENT_TIMESTAMP")
    if assigned_session_id is not ...:
        sets.append("assigned_session_id = ?")
        params.append(assigned_session_id)
    if priority is not None:
        sets.append("priority = ?")
        params.append(priority)
    if title is not None:
        sets.append("title = ?")
        params.append(title)
    if description is not None:
        sets.append("description = ?")
        params.append(description)
    if blocked_by_decision_id is not ...:
        sets.append("blocked_by_decision_id = ?")
        params.append(blocked_by_decision_id)
    if notes is not ...:
        sets.append("notes = ?")
        params.append(notes)
    if not sets:
        return get_task(conn, id)
    params.append(id)
    conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    return get_task(conn, id)


def delete_task(conn: sqlite3.Connection, id: str) -> bool:
    conn.execute("DELETE FROM task_dependencies WHERE task_id = ? OR depends_on_task_id = ?", (id, id))
    conn.execute("DELETE FROM task_requirements WHERE task_id = ?", (id,))
    cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (id,))
    conn.commit()
    return cursor.rowcount > 0


# --- Dependencies ---

def add_dependency(conn: sqlite3.Connection, task_id: str, depends_on_task_id: str) -> TaskDependency:
    conn.execute(
        "INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_task_id) VALUES (?, ?)",
        (task_id, depends_on_task_id),
    )
    conn.commit()
    return TaskDependency(task_id, depends_on_task_id)


def remove_dependency(conn: sqlite3.Connection, task_id: str, depends_on_task_id: str) -> bool:
    cursor = conn.execute(
        "DELETE FROM task_dependencies WHERE task_id = ? AND depends_on_task_id = ?",
        (task_id, depends_on_task_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def get_dependencies(conn: sqlite3.Connection, task_id: str) -> list[TaskDependency]:
    rows = conn.execute(
        "SELECT * FROM task_dependencies WHERE task_id = ?", (task_id,)
    ).fetchall()
    return [TaskDependency(**dict(r)) for r in rows]


def get_dependents(conn: sqlite3.Connection, task_id: str) -> list[TaskDependency]:
    """Get tasks that depend on the given task."""
    rows = conn.execute(
        "SELECT * FROM task_dependencies WHERE depends_on_task_id = ?", (task_id,)
    ).fetchall()
    return [TaskDependency(**dict(r)) for r in rows]


# --- Requirements ---

def add_requirement(
    conn: sqlite3.Connection,
    task_id: str,
    requirement_type: str,
    requirement_value: str,
) -> TaskRequirement:
    conn.execute(
        """INSERT OR IGNORE INTO task_requirements
           (task_id, requirement_type, requirement_value) VALUES (?, ?, ?)""",
        (task_id, requirement_type, requirement_value),
    )
    conn.commit()
    return TaskRequirement(task_id, requirement_type, requirement_value)


def get_requirements(conn: sqlite3.Connection, task_id: str) -> list[TaskRequirement]:
    rows = conn.execute(
        "SELECT * FROM task_requirements WHERE task_id = ?", (task_id,)
    ).fetchall()
    return [TaskRequirement(**dict(r)) for r in rows]
