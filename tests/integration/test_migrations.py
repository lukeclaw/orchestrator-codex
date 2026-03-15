"""Tests for database migrations — fresh creation and idempotent re-runs."""

from orchestrator.state.db import get_memory_connection
from orchestrator.state.migrations.runner import (
    apply_migrations,
    get_current_version,
)


def test_fresh_migration():
    """Running migrations on an empty DB should create all tables."""
    conn = get_memory_connection()
    applied = apply_migrations(conn)
    # Migrations: 1=initial, 2=remove_cost, 3=context, 4=subtasks, 5=tunnel_pane,
    # 6=task_links, 7=session_type, 8=remove_current_task_id, 9=rename_mp_path_to_work_dir,
    # 10=task_index, 11=priority_to_string, 12=drop_pr_tables,
    # 13=context_description, 14=timestamps,
    # 15=notifications, 16=last_viewed_at, 17=last_status_changed_at, 18=tunnel_pid,
    # 19=drop_tunnel_pane, 20=drop_skill_templates, 21=drop_tmux_window,
    # 22=drop_dead_tables, 24=notification_metadata, 25=auto_reconnect,
    # 26=claude_session_id, 27=status_events, 28=skills, 29=skill_overrides,
    # 30=simplify_context_categories, 31=rws_pty_id,
    # 32=soft_delete_sessions, 33=session_name_in_events,
    # 34=drop_sessions_deleted_at, 35=human_activity_events
    assert applied == [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        19,
        20,
        21,
        22,
        24,
        25,
        26,
        27,
        28,
        29,
        30,
        31,
        32,
        33,
        34,
        35,
    ]

    # Verify key tables exist
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = {r["name"] for r in tables}

    expected_tables = {
        "projects",
        "sessions",
        "tasks",
        "notifications",
        "config",
        "context_items",
        "schema_version",
        "status_events",
        "skills",
        "skill_overrides",
        "human_activity_events",
    }
    assert expected_tables.issubset(table_names)
    # These tables should have been dropped by various migrations
    dropped_tables = {
        "cost_events",
        "pull_requests",
        "pr_dependencies",
        "skill_templates",
        "task_dependencies",
        "task_requirements",
        "activities",
        "decisions",
        "decision_history",
        "worker_capabilities",
        "session_snapshots",
        "comm_events",
        "learned_patterns",
        "prompt_templates",
        "project_workers",
    }
    for t in dropped_tables:
        assert t not in table_names, f"Legacy table {t} should have been dropped"
    conn.close()


def test_idempotent_rerun():
    """Running migrations twice should be a no-op the second time."""
    conn = get_memory_connection()
    first = apply_migrations(conn)
    assert first == [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        19,
        20,
        21,
        22,
        24,
        25,
        26,
        27,
        28,
        29,
        30,
        31,
        32,
        33,
        34,
        35,
    ]

    second = apply_migrations(conn)
    assert second == []
    conn.close()


def test_current_version_after_migration():
    conn = get_memory_connection()
    assert get_current_version(conn) == 0
    apply_migrations(conn)
    assert get_current_version(conn) == 35
    conn.close()


def test_schema_version_record():
    conn = get_memory_connection()
    apply_migrations(conn)
    row = conn.execute("SELECT * FROM schema_version WHERE version = 1").fetchone()
    assert row is not None
    assert "Initial schema" in row["description"]
    conn.close()


def test_migration_030_simplify_categories():
    """Migration 030 should convert old categories to instruction/reference/NULL."""
    conn = get_memory_connection()
    # Apply all migrations up to 029
    apply_migrations(conn)

    # Insert context items with old categories (simulate pre-migration data)
    # We need to revert version so 030 re-runs, but easier: just test the SQL logic directly
    conn.execute("DELETE FROM schema_version WHERE version >= 30")
    conn.commit()

    # Insert items with old categories
    for cat in ("requirement", "convention", "instruction", "reference", "note", "worker_note"):
        conn.execute(
            "INSERT INTO context_items (id, title, content, scope, category)"
            " VALUES (?, ?, ?, 'global', ?)",
            (f"test-{cat}", f"Test {cat}", f"Content for {cat}", cat),
        )
    # Also one with NULL category
    conn.execute(
        "INSERT INTO context_items (id, title, content, scope, category)"
        " VALUES ('test-null', 'Test null', 'No cat', 'global', NULL)"
    )
    conn.commit()

    # Re-apply migration 030
    applied = apply_migrations(conn)
    assert 30 in applied

    # Verify conversions
    rows = {
        r["id"]: r["category"]
        for r in conn.execute(
            "SELECT id, category FROM context_items WHERE id LIKE 'test-%'"
        ).fetchall()
    }
    assert rows["test-requirement"] == "instruction"  # requirement → instruction
    assert rows["test-convention"] == "instruction"  # convention → instruction
    assert rows["test-instruction"] == "instruction"  # instruction unchanged
    assert rows["test-reference"] == "reference"  # reference unchanged
    assert rows["test-note"] is None  # note → NULL
    assert rows["test-worker_note"] is None  # worker_note → NULL
    assert rows["test-null"] is None  # NULL unchanged
    conn.close()


def test_indexes_created():
    """Verify that key indexes are created."""
    conn = get_memory_connection()
    apply_migrations(conn)
    indexes = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
        " AND (name LIKE 'idx_%' OR name LIKE 'ux_%')"
    ).fetchall()
    index_names = {r["name"] for r in indexes}
    assert "idx_tasks_project" in index_names
    conn.close()
