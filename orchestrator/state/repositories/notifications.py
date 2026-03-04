"""Repository for notifications table."""

import sqlite3
import uuid

from orchestrator.state.models import Notification


def get_notification(conn: sqlite3.Connection, id: str) -> Notification | None:
    row = conn.execute("SELECT * FROM notifications WHERE id = ?", (id,)).fetchone()
    if row is None:
        return None
    return Notification(**dict(row))


def list_notifications(
    conn: sqlite3.Connection,
    task_id: str | None = None,
    session_id: str | None = None,
    dismissed: bool | None = None,
    limit: int | None = None,
) -> list[Notification]:
    clauses = []
    params: list = []

    if task_id is not None:
        clauses.append("task_id = ?")
        params.append(task_id)
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    if dismissed is not None:
        clauses.append("dismissed = ?")
        params.append(1 if dismissed else 0)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit_clause = f"LIMIT {limit}" if limit else ""
    rows = conn.execute(
        f"SELECT * FROM notifications {where} ORDER BY created_at DESC {limit_clause}",
        params,
    ).fetchall()
    return [Notification(**dict(r)) for r in rows]


def count_active_notifications(conn: sqlite3.Connection) -> int:
    """Count non-dismissed notifications."""
    row = conn.execute("SELECT COUNT(*) as cnt FROM notifications WHERE dismissed = 0").fetchone()
    return row["cnt"] if row else 0


def count_notifications_for_task(conn: sqlite3.Connection, task_id: str) -> int:
    """Count non-dismissed notifications for a specific task."""
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM notifications WHERE task_id = ? AND dismissed = 0",
        (task_id,),
    ).fetchone()
    return row["cnt"] if row else 0


def create_notification(
    conn: sqlite3.Connection,
    message: str,
    task_id: str | None = None,
    session_id: str | None = None,
    notification_type: str = "info",
    link_url: str | None = None,
    metadata: str | None = None,
) -> Notification:
    id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO notifications (id, task_id, session_id, message, notification_type, link_url, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (id, task_id, session_id, message, notification_type, link_url, metadata),
    )
    conn.commit()
    return get_notification(conn, id)


def dismiss_notification(conn: sqlite3.Connection, id: str) -> Notification | None:
    conn.execute(
        "UPDATE notifications SET dismissed = 1, dismissed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (id,),
    )
    conn.commit()
    return get_notification(conn, id)


def dismiss_all_notifications(
    conn: sqlite3.Connection,
    task_id: str | None = None,
    session_id: str | None = None,
) -> int:
    """Dismiss all notifications, optionally filtered by task_id or session_id.

    Returns the number of notifications dismissed.
    """
    clauses = ["dismissed = 0"]
    params: list = []

    if task_id is not None:
        clauses.append("task_id = ?")
        params.append(task_id)
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)

    where = f"WHERE {' AND '.join(clauses)}"
    cursor = conn.execute(
        f"UPDATE notifications SET dismissed = 1, dismissed_at = CURRENT_TIMESTAMP {where}",
        params,
    )
    conn.commit()
    return cursor.rowcount


def delete_notification(conn: sqlite3.Connection, id: str) -> bool:
    cursor = conn.execute("DELETE FROM notifications WHERE id = ?", (id,))
    conn.commit()
    return cursor.rowcount > 0


def delete_notifications_by_ids(conn: sqlite3.Connection, ids: list[str]) -> int:
    """Delete notifications by a list of IDs. Returns count deleted."""
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    cursor = conn.execute(
        f"DELETE FROM notifications WHERE id IN ({placeholders})",
        ids,
    )
    conn.commit()
    return cursor.rowcount


def undismiss_notification(conn: sqlite3.Connection, id: str) -> Notification | None:
    """Restore a dismissed notification back to active."""
    conn.execute(
        "UPDATE notifications SET dismissed = 0, dismissed_at = NULL WHERE id = ?",
        (id,),
    )
    conn.commit()
    return get_notification(conn, id)


def delete_dismissed_notifications(conn: sqlite3.Connection) -> int:
    """Delete all dismissed notifications. Returns count deleted."""
    cursor = conn.execute("DELETE FROM notifications WHERE dismissed = 1")
    conn.commit()
    return cursor.rowcount
