"""Unit tests for task link deduplication in the repository layer."""

import json

from orchestrator.state.db import get_memory_connection
from orchestrator.state.migrations.runner import apply_migrations
from orchestrator.state.repositories import tasks as repo


def _setup_db():
    conn = get_memory_connection()
    apply_migrations(conn)
    return conn


def _create_project_and_task(conn):
    """Helper to create a project and task for link tests."""
    import uuid

    pid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO projects (id, name, created_at, updated_at)"
        " VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (pid, "TestProj"),
    )
    conn.commit()
    task = repo.create_task(conn, pid, "Test task")
    return task


class TestTaskLinkDedup:
    def test_repo_deduplicates_links_on_write(self):
        """Repository layer silently deduplicates links by URL."""
        conn = _setup_db()
        task = _create_project_and_task(conn)

        dup_links = json.dumps([
            {"url": "https://example.com/pr/1", "tag": "PR"},
            {"url": "https://example.com/pr/1", "tag": "PR"},
            {"url": "https://example.com/pr/2", "tag": "PR"},
        ])
        updated = repo.update_task(conn, task.id, links=dup_links)
        assert len(updated.links_list) == 2
        urls = [link["url"] for link in updated.links_list]
        assert urls == ["https://example.com/pr/1", "https://example.com/pr/2"]

    def test_repo_keeps_first_occurrence_on_dedup(self):
        """When deduplicating, the first occurrence (with its metadata) is kept."""
        conn = _setup_db()
        task = _create_project_and_task(conn)

        dup_links = json.dumps([
            {"url": "https://example.com/pr/1", "tag": "First"},
            {"url": "https://example.com/pr/1", "tag": "Second"},
        ])
        updated = repo.update_task(conn, task.id, links=dup_links)
        assert len(updated.links_list) == 1
        assert updated.links_list[0]["tag"] == "First"

    def test_repo_no_dedup_needed(self):
        """Unique links pass through unchanged."""
        conn = _setup_db()
        task = _create_project_and_task(conn)

        links = json.dumps([
            {"url": "https://example.com/pr/1"},
            {"url": "https://example.com/pr/2"},
        ])
        updated = repo.update_task(conn, task.id, links=links)
        assert len(updated.links_list) == 2

    def test_repo_handles_null_links(self):
        """Setting links to None clears them."""
        conn = _setup_db()
        task = _create_project_and_task(conn)

        # First set some links
        repo.update_task(conn, task.id, links=json.dumps([{"url": "https://example.com"}]))
        # Then clear
        updated = repo.update_task(conn, task.id, links=None)
        assert updated.links_list == []
