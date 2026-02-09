"""Detect overlapping file paths between workers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from orchestrator.state.repositories import sessions


@dataclass
class Conflict:
    session_a: str
    session_b: str
    overlap: str  # Description of the conflict


def detect_path_conflicts(conn: sqlite3.Connection) -> list[Conflict]:
    """Detect sessions that share the same work_dir (working directory)."""
    all_sessions = sessions.list_sessions(conn)
    active = [s for s in all_sessions if s.status in ("working", "idle") and s.work_dir]

    conflicts = []
    seen_paths: dict[str, str] = {}

    for s in active:
        if s.work_dir in seen_paths:
            conflicts.append(Conflict(
                session_a=seen_paths[s.work_dir],
                session_b=s.name,
                overlap=f"Same working directory: {s.work_dir}",
            ))
        else:
            seen_paths[s.work_dir] = s.name

    return conflicts
