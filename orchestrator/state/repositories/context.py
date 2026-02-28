"""Repository for context_items table."""

import sqlite3
import uuid

from orchestrator.state.models import ContextItem


def get_context_item(conn: sqlite3.Connection, id: str) -> ContextItem | None:
    row = conn.execute("SELECT * FROM context_items WHERE id = ?", (id,)).fetchone()
    if row is None:
        return None
    return ContextItem(**dict(row))


def list_context(
    conn: sqlite3.Connection,
    scope: str | None = None,
    project_id: str | None = None,
    category: str | None = None,
    search: str | None = None,
) -> list[ContextItem]:
    clauses = []
    params: list = []

    if scope:
        clauses.append("scope = ?")
        params.append(scope)
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if search:
        clauses.append("(title LIKE ? OR content LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM context_items {where} ORDER BY updated_at DESC", params
    ).fetchall()
    return [ContextItem(**dict(r)) for r in rows]


def create_context_item(
    conn: sqlite3.Connection,
    title: str,
    content: str,
    scope: str = "global",
    project_id: str | None = None,
    description: str | None = None,
    category: str | None = None,
    source: str | None = None,
    metadata: str | None = None,
) -> ContextItem:
    id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO context_items (id, scope, project_id, title, description, content, category, source, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (id, scope, project_id, title, description, content, category, source, metadata),
    )
    conn.commit()
    return get_context_item(conn, id)


def update_context_item(
    conn: sqlite3.Connection,
    id: str,
    title: str | None = None,
    content: str | None = None,
    scope: str | None = None,
    project_id: str | None = ...,
    description: str | None = ...,
    category: str | None = ...,
    source: str | None = ...,
    metadata: str | None = ...,
) -> ContextItem | None:
    sets = []
    params: list = []

    if title is not None:
        sets.append("title = ?")
        params.append(title)
    if content is not None:
        sets.append("content = ?")
        params.append(content)
    if scope is not None:
        sets.append("scope = ?")
        params.append(scope)
    if project_id is not ...:
        sets.append("project_id = ?")
        params.append(project_id)
    if description is not ...:
        sets.append("description = ?")
        params.append(description)
    if category is not ...:
        sets.append("category = ?")
        params.append(category)
    if source is not ...:
        sets.append("source = ?")
        params.append(source)
    if metadata is not ...:
        sets.append("metadata = ?")
        params.append(metadata)

    if not sets:
        return get_context_item(conn, id)

    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(id)
    conn.execute(f"UPDATE context_items SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    return get_context_item(conn, id)


def delete_context_item(conn: sqlite3.Connection, id: str) -> bool:
    cursor = conn.execute("DELETE FROM context_items WHERE id = ?", (id,))
    conn.commit()
    return cursor.rowcount > 0
