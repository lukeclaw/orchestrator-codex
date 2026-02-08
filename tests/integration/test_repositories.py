"""Integration tests for all repositories against real SQLite."""

from orchestrator.state.repositories import (
    activities,
    config as config_repo,
    decisions,
    projects,
    pull_requests,
    sessions,
    tasks,
    templates,
)


# --- Sessions ---

class TestSessionsRepo:
    def test_create_and_get(self, db):
        s = sessions.create_session(db, "worker-1", "rdev1.example.com", "/path/to/mp")
        assert s.name == "worker-1"
        assert s.host == "rdev1.example.com"
        assert s.status == "idle"

        fetched = sessions.get_session(db, s.id)
        assert fetched.name == s.name

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

    def test_update_session(self, db):
        s = sessions.create_session(db, "update-me", "host")
        updated = sessions.update_session(db, s.id, status="working")
        assert updated.status == "working"

    def test_delete_session(self, db):
        s = sessions.create_session(db, "delete-me", "host")
        assert sessions.delete_session(db, s.id) is True
        assert sessions.get_session(db, s.id) is None

    def test_capabilities(self, db):
        s = sessions.create_session(db, "cap-test", "host")
        sessions.add_capability(db, s.id, "language", "python")
        sessions.add_capability(db, s.id, "language", "typescript")
        caps = sessions.get_capabilities(db, s.id)
        assert len(caps) == 2
        assert sessions.remove_capability(db, s.id, "language", "python") is True
        assert len(sessions.get_capabilities(db, s.id)) == 1


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

    def test_assign_worker(self, db):
        p = projects.create_project(db, "Worker Test")
        s = sessions.create_session(db, "pw-worker", "host")
        projects.assign_worker(db, p.id, s.id)
        workers = projects.list_project_workers(db, p.id)
        assert len(workers) == 1
        assert projects.unassign_worker(db, p.id, s.id) is True


# --- Tasks ---

class TestTasksRepo:
    def test_create_and_get(self, db):
        p = projects.create_project(db, "Task Project")
        t = tasks.create_task(db, p.id, "Do something", priority=5)
        assert t.title == "Do something"
        assert t.priority == 5
        assert t.status == "todo"

    def test_update_status_sets_timestamps(self, db):
        p = projects.create_project(db, "TS Project")
        t = tasks.create_task(db, p.id, "Timestamped task")

        t = tasks.update_task(db, t.id, status="in_progress")
        assert t.started_at is not None

        t = tasks.update_task(db, t.id, status="done")
        assert t.completed_at is not None

    def test_list_by_project(self, db):
        p1 = projects.create_project(db, "P1")
        p2 = projects.create_project(db, "P2")
        tasks.create_task(db, p1.id, "T1 for P1")
        tasks.create_task(db, p2.id, "T1 for P2")
        p1_tasks = tasks.list_tasks(db, project_id=p1.id)
        assert len(p1_tasks) == 1

    def test_dependencies(self, db):
        p = projects.create_project(db, "Dep Project")
        t1 = tasks.create_task(db, p.id, "First")
        t2 = tasks.create_task(db, p.id, "Second")
        tasks.add_dependency(db, t2.id, t1.id)

        deps = tasks.get_dependencies(db, t2.id)
        assert len(deps) == 1
        assert deps[0].depends_on_task_id == t1.id

        dependents = tasks.get_dependents(db, t1.id)
        assert len(dependents) == 1

    def test_requirements(self, db):
        p = projects.create_project(db, "Req Project")
        t = tasks.create_task(db, p.id, "Requires Python")
        tasks.add_requirement(db, t.id, "language", "python")
        reqs = tasks.get_requirements(db, t.id)
        assert len(reqs) == 1


# --- Decisions ---

class TestDecisionsRepo:
    def test_create_and_list_pending(self, db):
        d = decisions.create_decision(db, "Which framework?", options=["FastAPI", "Flask"])
        assert d.status == "pending"
        pending = decisions.list_pending(db)
        assert len(pending) == 1

    def test_respond_decision(self, db):
        d = decisions.create_decision(db, "DB choice?", urgency="high")
        responded = decisions.respond_decision(db, d.id, "PostgreSQL", "admin")
        assert responded.status == "responded"
        assert responded.response == "PostgreSQL"
        assert responded.resolved_by == "admin"
        assert decisions.list_pending(db) == []

    def test_dismiss_decision(self, db):
        d = decisions.create_decision(db, "Ignore me")
        dismissed = decisions.dismiss_decision(db, d.id)
        assert dismissed.status == "dismissed"

    def test_respond_unblocks_task(self, db):
        p = projects.create_project(db, "Block Test")
        d = decisions.create_decision(db, "Blocking question")
        t = tasks.create_task(db, p.id, "Blocked task")
        tasks.update_task(db, t.id, status="blocked", blocked_by_decision_id=d.id)

        decisions.respond_decision(db, d.id, "Answer")
        t = tasks.get_task(db, t.id)
        assert t.status == "todo"
        assert t.blocked_by_decision_id is None


# --- Pull Requests ---

class TestPullRequestsRepo:
    def test_create_and_list(self, db):
        pr = pull_requests.create_pull_request(
            db, "https://github.com/org/repo/pull/1", number=1, title="Fix bug"
        )
        assert pr.status == "open"
        all_prs = pull_requests.list_pull_requests(db)
        assert len(all_prs) == 1

    def test_update_status(self, db):
        pr = pull_requests.create_pull_request(db, "https://example.com/pr/2")
        updated = pull_requests.update_pull_request(db, pr.id, status="merged")
        assert updated.status == "merged"


# --- Activities ---

class TestActivitiesRepo:
    def test_log_and_list(self, db):
        a = activities.log_activity(db, "task_started", actor="system")
        assert a.event_type == "task_started"
        recent = activities.list_activities(db, limit=10)
        assert len(recent) == 1

    def test_list_by_session(self, db):
        s = sessions.create_session(db, "act-worker", "host")
        activities.log_activity(db, "event1", session_id=s.id)
        activities.log_activity(db, "event2", session_id=s.id)
        activities.log_activity(db, "event3")  # no session
        session_acts = activities.list_activities(db, session_id=s.id)
        assert len(session_acts) == 2


# --- Templates ---

class TestTemplatesRepo:
    def test_create_and_get_prompt_template(self, db):
        t = templates.create_prompt_template(db, "test_tpl", "Hello ${name}", "Test template")
        assert t.name == "test_tpl"
        fetched = templates.get_prompt_template(db, "test_tpl")
        assert fetched.template == "Hello ${name}"

    def test_update_prompt_template_increments_version(self, db):
        templates.create_prompt_template(db, "versioned", "v1")
        updated = templates.update_prompt_template(db, "versioned", template="v2")
        assert updated.version == 2
        assert updated.template == "v2"

    def test_create_and_get_skill_template(self, db):
        t = templates.create_skill_template(
            db, "test_skill", "# Skill", is_default=True
        )
        assert t.is_default is True
        default = templates.get_default_skill_template(db)
        assert default.name == "test_skill"

    def test_list_skill_templates(self, db):
        templates.create_skill_template(db, "s1", "content1")
        templates.create_skill_template(db, "s2", "content2")
        all_skills = templates.list_skill_templates(db)
        assert len(all_skills) == 2
