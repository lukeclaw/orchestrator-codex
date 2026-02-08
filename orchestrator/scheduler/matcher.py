"""Match tasks to workers by capability."""

from __future__ import annotations

import sqlite3

from orchestrator.state.models import Session, Task
from orchestrator.state.repositories import sessions, tasks


def match_score(
    conn: sqlite3.Connection,
    task: Task,
    session: Session,
) -> float:
    """Calculate a match score between a task and a worker session.

    Higher score = better match. Score is 0-1.
    """
    requirements = tasks.get_requirements(conn, task.id)
    if not requirements:
        return 0.5  # No requirements = neutral match

    capabilities = sessions.get_capabilities(conn, session.id)
    cap_set = {(c.capability_type, c.capability_value) for c in capabilities}

    matched = 0
    for req in requirements:
        if (req.requirement_type, req.requirement_value) in cap_set:
            matched += 1

    return matched / len(requirements) if requirements else 0.5


def find_best_worker(
    conn: sqlite3.Connection,
    task: Task,
) -> Session | None:
    """Find the best idle worker for a task."""
    idle_sessions = sessions.list_sessions(conn, status="idle")
    if not idle_sessions:
        return None

    scored = [(s, match_score(conn, task, s)) for s in idle_sessions]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Return best match if score > 0
    if scored and scored[0][1] > 0:
        return scored[0][0]

    # Fallback: return first idle session
    return idle_sessions[0] if idle_sessions else None
