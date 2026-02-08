"""Repository for decisions and decision_history tables."""

import json
import sqlite3
import uuid

from orchestrator.state.models import Decision, DecisionHistory


def get_decision(conn: sqlite3.Connection, id: str) -> Decision | None:
    row = conn.execute("SELECT * FROM decisions WHERE id = ?", (id,)).fetchone()
    if row is None:
        return None
    return Decision(**dict(row))


def list_decisions(
    conn: sqlite3.Connection,
    status: str | None = None,
    project_id: str | None = None,
    session_id: str | None = None,
) -> list[Decision]:
    clauses = []
    params = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM decisions{where} ORDER BY created_at DESC", params
    ).fetchall()
    return [Decision(**dict(r)) for r in rows]


def list_pending(conn: sqlite3.Connection) -> list[Decision]:
    return list_decisions(conn, status="pending")


def create_decision(
    conn: sqlite3.Connection,
    question: str,
    project_id: str | None = None,
    task_id: str | None = None,
    session_id: str | None = None,
    options: list[str] | None = None,
    context: str | None = None,
    urgency: str = "normal",
) -> Decision:
    id = str(uuid.uuid4())
    options_json = json.dumps(options) if options else None
    conn.execute(
        """INSERT INTO decisions
           (id, project_id, task_id, session_id, question, options, context, urgency)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (id, project_id, task_id, session_id, question, options_json, context, urgency),
    )
    conn.commit()
    return get_decision(conn, id)


def respond_decision(
    conn: sqlite3.Connection,
    id: str,
    response: str,
    resolved_by: str = "user",
) -> Decision | None:
    conn.execute(
        """UPDATE decisions
           SET response = ?, status = 'responded',
               resolved_at = CURRENT_TIMESTAMP, resolved_by = ?
           WHERE id = ?""",
        (response, resolved_by, id),
    )
    conn.commit()

    # Also unblock any tasks blocked by this decision
    conn.execute(
        """UPDATE tasks SET blocked_by_decision_id = NULL, status = 'todo'
           WHERE blocked_by_decision_id = ? AND status = 'blocked'""",
        (id,),
    )
    conn.commit()

    return get_decision(conn, id)


def dismiss_decision(conn: sqlite3.Connection, id: str) -> Decision | None:
    conn.execute(
        """UPDATE decisions
           SET status = 'dismissed', resolved_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (id,),
    )
    conn.commit()
    return get_decision(conn, id)


# --- Decision History ---

def record_history(
    conn: sqlite3.Connection,
    decision_id: str,
    project_id: str | None = None,
    question: str | None = None,
    context: str | None = None,
    decision: str | None = None,
) -> DecisionHistory:
    id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO decision_history
           (id, decision_id, project_id, question, context, decision)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (id, decision_id, project_id, question, context, decision),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM decision_history WHERE id = ?", (id,)).fetchone()
    return DecisionHistory(**dict(row))
