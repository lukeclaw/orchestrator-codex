"""Tests for CLI-to-API parameter parity.

Verifies that parameters accepted by CLI tools are correctly handled
by the corresponding API models and repository functions.
"""

from orchestrator.state.repositories import projects as projects_repo
from orchestrator.state.repositories import tasks as tasks_repo


class TestTaskCreateNotes:
    """orch-tasks create --notes should persist notes on the new task."""

    def test_create_task_with_notes(self, db):
        project = projects_repo.create_project(db, "Test Project")
        task = tasks_repo.create_task(db, project.id, "Test Task", notes="initial findings")
        assert task.notes == "initial findings"

    def test_create_task_without_notes(self, db):
        project = projects_repo.create_project(db, "Test Project")
        task = tasks_repo.create_task(db, project.id, "Test Task")
        assert task.notes is None

    def test_create_task_notes_via_api_model(self, db):
        """TaskCreate model should accept the notes field."""
        from orchestrator.api.routes.tasks import TaskCreate

        body = TaskCreate(project_id="proj-1", title="Test", notes="some notes")
        assert body.notes == "some notes"

    def test_create_task_notes_via_api_model_default(self):
        """TaskCreate model should default notes to None."""
        from orchestrator.api.routes.tasks import TaskCreate

        body = TaskCreate(project_id="proj-1", title="Test")
        assert body.notes is None


class TestProjectUpdateTaskPrefix:
    """orch-projects update --task-prefix should update the task prefix."""

    def test_update_project_task_prefix(self, db):
        project = projects_repo.create_project(db, "Test Project", task_prefix="OLD")
        assert project.task_prefix == "OLD"

        updated = projects_repo.update_project(db, project.id, task_prefix="NEW")
        assert updated.task_prefix == "NEW"

    def test_update_project_task_prefix_via_api_model(self):
        """ProjectUpdate model should accept the task_prefix field."""
        from orchestrator.api.routes.projects import ProjectUpdate

        body = ProjectUpdate(task_prefix="NEW")
        assert body.task_prefix == "NEW"

    def test_update_project_preserves_other_fields(self, db):
        project = projects_repo.create_project(
            db, "Test Project", description="desc", task_prefix="OLD"
        )

        updated = projects_repo.update_project(db, project.id, task_prefix="NEW")
        assert updated.task_prefix == "NEW"
        assert updated.name == "Test Project"
        assert updated.description == "desc"


class TestProjectUpdateTargetDate:
    """orch-projects update should propagate target_date to the repo."""

    def test_update_project_target_date(self, db):
        project = projects_repo.create_project(db, "Test Project", target_date="2026-06-01")
        assert project.target_date == "2026-06-01"

        updated = projects_repo.update_project(db, project.id, target_date="2026-12-31")
        assert updated.target_date == "2026-12-31"

    def test_clear_project_target_date(self, db):
        project = projects_repo.create_project(db, "Test Project", target_date="2026-06-01")

        updated = projects_repo.update_project(db, project.id, target_date=None)
        assert updated.target_date is None
