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
    """Detect sessions that share the same mp_path (working directory)."""
    all_sessions = sessions.list_sessions(conn)
    active = [s for s in all_sessions if s.status in ("working", "idle") and s.mp_path]

    conflicts = []
    seen_paths: dict[str, str] = {}

    for s in active:
        if s.mp_path in seen_paths:
            conflicts.append(Conflict(
                session_a=seen_paths[s.mp_path],
                session_b=s.name,
                overlap=f"Same working directory: {s.mp_path}",
            ))
        else:
            seen_paths[s.mp_path] = s.name

    return conflicts
