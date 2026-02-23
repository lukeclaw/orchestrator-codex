"""Repository for skills table."""

import os
import re
import sqlite3
import uuid

from orchestrator.state.models import Skill


def _builtin_skill_names(target: str) -> set[str]:
    """Get built-in skill filenames (without .md) for a target."""
    from orchestrator.agents.deploy import get_brain_skills_dir, get_worker_skills_dir

    if target == "brain":
        skills_dir = get_brain_skills_dir()
    else:
        skills_dir = get_worker_skills_dir()

    if not skills_dir or not os.path.isdir(skills_dir):
        return set()

    return {
        os.path.splitext(f)[0]
        for f in os.listdir(skills_dir)
        if f.endswith(".md")
    }


def get_skill(conn: sqlite3.Connection, id: str) -> Skill | None:
    row = conn.execute("SELECT * FROM skills WHERE id = ?", (id,)).fetchone()
    if row is None:
        return None
    return Skill(**dict(row))


def list_skills(
    conn: sqlite3.Connection,
    target: str | None = None,
    search: str | None = None,
    enabled_only: bool = False,
) -> list[Skill]:
    clauses = []
    params: list = []

    if target:
        clauses.append("target = ?")
        params.append(target)
    if search:
        clauses.append("(name LIKE ? OR description LIKE ? OR content LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
    if enabled_only:
        clauses.append("enabled = 1")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM skills {where} ORDER BY updated_at DESC", params
    ).fetchall()
    return [Skill(**dict(r)) for r in rows]


def create_skill(
    conn: sqlite3.Connection,
    name: str,
    target: str,
    content: str,
    description: str | None = None,
) -> Skill:
    # Validate name format
    if not re.match(r'^[a-z][a-z0-9-]*$', name):
        raise ValueError("Name must start with a lowercase letter and contain only lowercase letters, digits, and hyphens")
    if len(name) > 50:
        raise ValueError("Name must be 50 characters or less")

    # Validate target
    if target not in ("brain", "worker"):
        raise ValueError("Target must be 'brain' or 'worker'")

    # Check for conflict with built-in skill names
    builtin_names = _builtin_skill_names(target)
    if name in builtin_names:
        raise ValueError(f"Name '{name}' conflicts with a built-in {target} skill")

    id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO skills (id, name, target, description, content)
           VALUES (?, ?, ?, ?, ?)""",
        (id, name, target, description, content),
    )
    conn.commit()
    return get_skill(conn, id)


def update_skill(
    conn: sqlite3.Connection,
    id: str,
    name: str | None = None,
    description: str | None = ...,
    content: str | None = None,
    target: str | None = None,
    enabled: bool | None = None,
) -> Skill | None:
    sets = []
    params: list = []

    if name is not None:
        if not re.match(r'^[a-z][a-z0-9-]*$', name):
            raise ValueError("Name must start with a lowercase letter and contain only lowercase letters, digits, and hyphens")
        if len(name) > 50:
            raise ValueError("Name must be 50 characters or less")
        sets.append("name = ?")
        params.append(name)

    if target is not None:
        if target not in ("brain", "worker"):
            raise ValueError("Target must be 'brain' or 'worker'")
        sets.append("target = ?")
        params.append(target)

    if description is not ...:
        sets.append("description = ?")
        params.append(description)

    if content is not None:
        sets.append("content = ?")
        params.append(content)

    if enabled is not None:
        sets.append("enabled = ?")
        params.append(1 if enabled else 0)

    if not sets:
        return get_skill(conn, id)

    # Check name conflict with built-in skills if name or target changed
    existing = get_skill(conn, id)
    if existing:
        check_name = name if name is not None else existing.name
        check_target = target if target is not None else existing.target
        builtin_names = _builtin_skill_names(check_target)
        if check_name in builtin_names:
            raise ValueError(f"Name '{check_name}' conflicts with a built-in {check_target} skill")

    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(id)
    conn.execute(
        f"UPDATE skills SET {', '.join(sets)} WHERE id = ?", params
    )
    conn.commit()
    return get_skill(conn, id)


def delete_skill(conn: sqlite3.Connection, id: str) -> bool:
    cursor = conn.execute("DELETE FROM skills WHERE id = ?", (id,))
    conn.commit()
    return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Built-in skill overrides
# ---------------------------------------------------------------------------

def is_builtin_skill_disabled(conn: sqlite3.Connection, name: str, target: str) -> bool:
    """Check if a built-in skill has been disabled via overrides table."""
    row = conn.execute(
        "SELECT enabled FROM skill_overrides WHERE name = ? AND target = ?",
        (name, target),
    ).fetchone()
    if row is None:
        return False  # No override = default enabled
    return row["enabled"] == 0


def set_builtin_skill_enabled(conn: sqlite3.Connection, name: str, target: str, enabled: bool):
    """Enable or disable a built-in skill.

    Disabling inserts a row with enabled=0.
    Re-enabling deletes the override row (back to default).
    """
    if enabled:
        conn.execute(
            "DELETE FROM skill_overrides WHERE name = ? AND target = ?",
            (name, target),
        )
    else:
        conn.execute(
            """INSERT INTO skill_overrides (name, target, enabled)
               VALUES (?, ?, 0)
               ON CONFLICT(name, target) DO UPDATE SET enabled = 0""",
            (name, target),
        )
    conn.commit()


def list_disabled_builtin_skills(
    conn: sqlite3.Connection,
    target: str | None = None,
) -> set[tuple[str, str]]:
    """Return set of (name, target) tuples for disabled built-in skills."""
    if target:
        rows = conn.execute(
            "SELECT name, target FROM skill_overrides WHERE enabled = 0 AND target = ?",
            (target,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT name, target FROM skill_overrides WHERE enabled = 0",
        ).fetchall()
    return {(r["name"], r["target"]) for r in rows}
