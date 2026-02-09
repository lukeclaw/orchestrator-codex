"""Repository for tasks, task_dependencies, and task_requirements tables."""

import sqlite3
import uuid

from orchestrator.state.models import Task, TaskDependency, TaskRequirement

# Explicit column list to avoid loading deprecated columns
TASK_COLUMNS = "id, project_id, title, description, status, priority, assigned_session_id, created_at, updated_at, parent_task_id, notes, links, task_index"


def get_task(conn: sqlite3.Connection, id: str) -> Task | None:
    row = conn.execute(f"SELECT {TASK_COLUMNS} FROM tasks WHERE id = ?", (id,)).fetchone()
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
        f"SELECT {TASK_COLUMNS} FROM tasks{where} ORDER BY priority DESC, created_at", params
    ).fetchall()
    return [Task(**dict(r)) for r in rows]


def _get_next_task_index(conn: sqlite3.Connection, project_id: str, parent_task_id: str | None) -> int:
    """Get the next task_index for a project or parent task."""
    if parent_task_id:
        # For subtasks, count existing subtasks under the parent
        row = conn.execute(
            "SELECT COALESCE(MAX(task_index), 0) + 1 as next_idx FROM tasks WHERE parent_task_id = ?",
            (parent_task_id,)
        ).fetchone()
    else:
        # For top-level tasks, count existing top-level tasks in the project
        row = conn.execute(
            "SELECT COALESCE(MAX(task_index), 0) + 1 as next_idx FROM tasks WHERE project_id = ? AND parent_task_id IS NULL",
            (project_id,)
        ).fetchone()
    return row["next_idx"] if row else 1


def create_task(
    conn: sqlite3.Connection,
    project_id: str,
    title: str,
    description: str | None = None,
    priority: str = "M",  # H (High), M (Medium), L (Low)
    parent_task_id: str | None = None,
) -> Task:
    id = str(uuid.uuid4())
    # Auto-generate task_index
    task_index = _get_next_task_index(conn, project_id, parent_task_id)
    conn.execute(
        """INSERT INTO tasks (id, project_id, title, description, priority, parent_task_id, task_index, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
        (id, project_id, title, description, priority, parent_task_id, task_index),
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
    notes: str | None = ...,
    links: str | None = ...,
) -> Task | None:
    sets = []
    params = []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
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
    if notes is not ...:
        sets.append("notes = ?")
        params.append(notes)
    if links is not ...:
        sets.append("links = ?")
        params.append(links)
    if not sets:
        return get_task(conn, id)
    # Always update updated_at timestamp
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(id)
    conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    return get_task(conn, id)


def delete_task(conn: sqlite3.Connection, id: str) -> bool:
    """Delete a task and all its subtasks recursively.
    
    Also cleans up:
    - All subtasks (cascading delete)
    - Task dependencies involving this task or subtasks
    - Task requirements for this task or subtasks
    """
    # First, recursively delete all subtasks
    subtasks = list_tasks(conn, parent_task_id=id)
    for subtask in subtasks:
        delete_task(conn, subtask.id)
    
    # Clean up dependencies and requirements for this task
    conn.execute("DELETE FROM task_dependencies WHERE task_id = ? OR depends_on_task_id = ?", (id, id))
    conn.execute("DELETE FROM task_requirements WHERE task_id = ?", (id,))
    
    # Delete the task itself
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
