"""Task scheduler: pick next task for idle workers."""

from __future__ import annotations

import logging
import sqlite3

from orchestrator.scheduler.dependencies import get_ready_tasks
from orchestrator.scheduler.matcher import find_best_worker
from orchestrator.state.repositories import projects, sessions, tasks

logger = logging.getLogger(__name__)


def get_next_assignments(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Find optimal task-to-worker assignments.

    Returns list of (task_id, session_id) pairs.
    """
    assignments = []
    idle_workers = sessions.list_sessions(conn, status="idle")

    if not idle_workers:
        return []

    # Get ready tasks from all active projects
    active_projects = projects.list_projects(conn, status="active")
    ready_task_ids = []
    for project in active_projects:
        ready_task_ids.extend(get_ready_tasks(conn, project.id))

    # Sort by priority (higher first)
    ready_tasks = []
    for tid in ready_task_ids:
        task = tasks.get_task(conn, tid)
        if task and task.assigned_session_id is None:
            ready_tasks.append(task)
    ready_tasks.sort(key=lambda t: t.priority, reverse=True)

    # Match tasks to workers
    assigned_workers = set()
    for task in ready_tasks:
        if not idle_workers:
            break

        best = find_best_worker(conn, task)
        if best and best.id not in assigned_workers:
            assignments.append((task.id, best.id))
            assigned_workers.add(best.id)
            idle_workers = [w for w in idle_workers if w.id != best.id]

    return assignments
