"""Repository for pull_requests and pr_dependencies tables."""

import sqlite3
import uuid

from orchestrator.state.models import PrDependency, PullRequest


def get_pull_request(conn: sqlite3.Connection, id: str) -> PullRequest | None:
    row = conn.execute("SELECT * FROM pull_requests WHERE id = ?", (id,)).fetchone()
    if row is None:
        return None
    return PullRequest(**dict(row))


def list_pull_requests(
    conn: sqlite3.Connection,
    task_id: str | None = None,
    session_id: str | None = None,
    status: str | None = None,
) -> list[PullRequest]:
    clauses = []
    params = []
    if task_id:
        clauses.append("task_id = ?")
        params.append(task_id)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if status:
        clauses.append("status = ?")
        params.append(status)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM pull_requests{where} ORDER BY created_at DESC", params
    ).fetchall()
    return [PullRequest(**dict(r)) for r in rows]


def create_pull_request(
    conn: sqlite3.Connection,
    url: str,
    task_id: str | None = None,
    session_id: str | None = None,
    number: int | None = None,
    title: str | None = None,
) -> PullRequest:
    id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO pull_requests (id, task_id, session_id, url, number, title)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (id, task_id, session_id, url, number, title),
    )
    conn.commit()
    return get_pull_request(conn, id)


def update_pull_request(
    conn: sqlite3.Connection,
    id: str,
    status: str | None = None,
    title: str | None = None,
    merged_at: str | None = None,
) -> PullRequest | None:
    sets = []
    params = []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if title is not None:
        sets.append("title = ?")
        params.append(title)
    if merged_at is not None:
        sets.append("merged_at = ?")
        params.append(merged_at)
    if not sets:
        return get_pull_request(conn, id)
    params.append(id)
    conn.execute(f"UPDATE pull_requests SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    return get_pull_request(conn, id)


# --- PR Dependencies ---

def add_pr_dependency(conn: sqlite3.Connection, pr_id: str, depends_on_pr_id: str) -> PrDependency:
    conn.execute(
        "INSERT OR IGNORE INTO pr_dependencies (pr_id, depends_on_pr_id) VALUES (?, ?)",
        (pr_id, depends_on_pr_id),
    )
    conn.commit()
    return PrDependency(pr_id, depends_on_pr_id)


def get_pr_dependencies(conn: sqlite3.Connection, pr_id: str) -> list[PrDependency]:
    rows = conn.execute(
        "SELECT * FROM pr_dependencies WHERE pr_id = ?", (pr_id,)
    ).fetchall()
    return [PrDependency(**dict(r)) for r in rows]
