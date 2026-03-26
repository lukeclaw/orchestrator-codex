"""Integration tests for all repositories against real SQLite."""

from orchestrator.state.repositories import (
    projects,
    sessions,
    tasks,
)

# --- Sessions ---


class TestSessionsRepo:
    def test_create_and_get(self, db):
        s = sessions.create_session(db, "worker-1", "rdev1.example.com", work_dir="/path/to/repo")
        assert s.name == "worker-1"
        assert s.host == "rdev1.example.com"
        assert s.status == "idle"
        assert s.work_dir == "/path/to/repo"
        assert s.provider == "claude"

        fetched = sessions.get_session(db, s.id)
        assert fetched.name == s.name
        assert fetched.provider == "claude"

    def test_create_with_provider(self, db):
        s = sessions.create_session(db, "codex-worker", "localhost", provider="codex")
        assert s.provider == "codex"
        assert s.auto_reconnect is False

        fetched = sessions.get_session(db, s.id)
        assert fetched.provider == "codex"
        assert fetched.auto_reconnect is False

    def test_get_by_name(self, db):
        sessions.create_session(db, "named-worker", "local")
        s = sessions.get_session_by_name(db, "named-worker")
        assert s is not None
        assert s.host == "local"

    def test_list_sessions(self, db):
        sessions.create_session(db, "w1", "host1")
        sessions.create_session(db, "w2", "host2")
        all_sessions = sessions.list_sessions(db)
        assert len(all_sessions) == 2
        assert all(s.provider == "claude" for s in all_sessions)

    def test_update_session(self, db):
        s = sessions.create_session(db, "update-me", "host")
        updated = sessions.update_session(db, s.id, status="working")
        assert updated.status == "working"

    def test_delete_session(self, db):
        s = sessions.create_session(db, "delete-me", "host")
        assert sessions.delete_session(db, s.id) is True
        assert sessions.get_session(db, s.id) is None

    def test_delete_session_cleans_task_assignments(self, db):
        s = sessions.create_session(db, "del-test", "host")
        p = projects.create_project(db, "Del Project")
        t = tasks.create_task(db, p.id, "Assigned task")
        tasks.update_task(db, t.id, assigned_session_id=s.id)
        sessions.delete_session(db, s.id)
        refreshed = tasks.get_task(db, t.id)
        assert refreshed.assigned_session_id is None


# --- Projects ---


class TestProjectsRepo:
    def test_create_and_get(self, db):
        p = projects.create_project(db, "Test Project", "A test project")
        assert p.name == "Test Project"
        assert p.status == "active"

    def test_update_project(self, db):
        p = projects.create_project(db, "Updatable")
        updated = projects.update_project(db, p.id, status="completed")
        assert updated.status == "completed"

    def test_list_by_status(self, db):
        projects.create_project(db, "Active 1")
        p2 = projects.create_project(db, "Paused 1")
        projects.update_project(db, p2.id, status="paused")
        active = projects.list_projects(db, status="active")
        assert len(active) == 1

    def test_delete_project(self, db):
        p = projects.create_project(db, "Delete Me")
        assert projects.delete_project(db, p.id) is True
        assert projects.get_project(db, p.id) is None


# --- Tasks ---


class TestTasksRepo:
    def test_create_and_get(self, db):
        p = projects.create_project(db, "Task Project")
        t = tasks.create_task(db, p.id, "Do something", priority="H")
        assert t.title == "Do something"
        assert t.priority == "H"
        assert t.status == "todo"

    def test_update_status_sets_updated_at(self, db):
        p = projects.create_project(db, "TS Project")
        t = tasks.create_task(db, p.id, "Timestamped task")
        original_updated_at = t.updated_at

        t = tasks.update_task(db, t.id, status="in_progress")
        assert t.updated_at is not None
        assert t.updated_at >= original_updated_at

    def test_list_by_project(self, db):
        p1 = projects.create_project(db, "P1")
        p2 = projects.create_project(db, "P2")
        tasks.create_task(db, p1.id, "T1 for P1")
        tasks.create_task(db, p2.id, "T1 for P2")
        p1_tasks = tasks.list_tasks(db, project_id=p1.id)
        assert len(p1_tasks) == 1

    def test_delete_task_recursive(self, db):
        p = projects.create_project(db, "Recursive Del")
        parent = tasks.create_task(db, p.id, "Parent")
        child = tasks.create_task(db, p.id, "Child", parent_task_id=parent.id)
        assert tasks.delete_task(db, parent.id) is True
        assert tasks.get_task(db, child.id) is None
