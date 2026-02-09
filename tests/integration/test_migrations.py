"""Tests for database migrations — fresh creation and idempotent re-runs."""

import sqlite3

from orchestrator.state.db import get_memory_connection
from orchestrator.state.migrations.runner import (
    apply_migrations,
    get_current_version,
    get_pending_migrations,
)


def test_fresh_migration():
    """Running migrations on an empty DB should create all tables."""
    conn = get_memory_connection()
    applied = apply_migrations(conn)
    # Migrations: 1=initial, 2=remove_cost, 3=context, 4=subtasks, 5=tunnel_pane,
    # 6=task_links, 7=session_type, 8=remove_current_task_id, 9=rename_mp_path_to_work_dir,
    # 10=task_index, 11=priority_to_string, 12=drop_pr_tables, 13=context_description, 14=timestamps
    assert applied == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]

    # Verify key tables exist
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = {r["name"] for r in tables}

    expected_tables = {
        "projects", "sessions", "project_workers",
        "tasks", "task_dependencies",
        "learned_patterns",
        "worker_capabilities", "task_requirements",
        "session_snapshots",
        "comm_events",
        "config", "prompt_templates", "skill_templates",
        "context_items",
        "schema_version",
    }
    assert expected_tables.issubset(table_names)
    # cost_events should have been dropped by migration 002
    assert "cost_events" not in table_names
    conn.close()


def test_idempotent_rerun():
    """Running migrations twice should be a no-op the second time."""
    conn = get_memory_connection()
    first = apply_migrations(conn)
    assert first == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]

    second = apply_migrations(conn)
    assert second == []
    conn.close()


def test_current_version_after_migration():
    conn = get_memory_connection()
    assert get_current_version(conn) == 0
    apply_migrations(conn)
    assert get_current_version(conn) == 14
    conn.close()


def test_schema_version_record():
    conn = get_memory_connection()
    apply_migrations(conn)
    row = conn.execute("SELECT * FROM schema_version WHERE version = 1").fetchone()
    assert row is not None
    assert "Initial schema" in row["description"]
    conn.close()


def test_indexes_created():
    """Verify that key indexes are created."""
    conn = get_memory_connection()
    apply_migrations(conn)
    indexes = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
    ).fetchall()
    index_names = {r["name"] for r in indexes}
    assert "idx_tasks_project" in index_names
    conn.close()
