"""Tests for worker capability matching."""

from orchestrator.scheduler.matcher import find_best_worker, match_score
from orchestrator.state.repositories import projects, sessions, tasks


def test_match_score_perfect(db):
    s = sessions.create_session(db, "py-worker", "host")
    sessions.add_capability(db, s.id, "language", "python")
    sessions.add_capability(db, s.id, "tool", "pytest")

    p = projects.create_project(db, "Test")
    t = tasks.create_task(db, p.id, "Python task")
    tasks.add_requirement(db, t.id, "language", "python")
    tasks.add_requirement(db, t.id, "tool", "pytest")

    score = match_score(db, t, s)
    assert score == 1.0


def test_match_score_partial(db):
    s = sessions.create_session(db, "partial-worker", "host")
    sessions.add_capability(db, s.id, "language", "python")

    p = projects.create_project(db, "Test")
    t = tasks.create_task(db, p.id, "Multi-req task")
    tasks.add_requirement(db, t.id, "language", "python")
    tasks.add_requirement(db, t.id, "tool", "pytest")

    score = match_score(db, t, s)
    assert score == 0.5


def test_match_score_no_requirements(db):
    s = sessions.create_session(db, "any-worker", "host")

    p = projects.create_project(db, "Test")
    t = tasks.create_task(db, p.id, "No-req task")

    score = match_score(db, t, s)
    assert score == 0.5  # Neutral


def test_find_best_worker_single(db):
    s = sessions.create_session(db, "solo-worker", "host")
    sessions.add_capability(db, s.id, "language", "python")

    p = projects.create_project(db, "Test")
    t = tasks.create_task(db, p.id, "Task")
    tasks.add_requirement(db, t.id, "language", "python")

    best = find_best_worker(db, t)
    assert best is not None
    assert best.name == "solo-worker"


def test_find_best_worker_picks_better_match(db):
    s1 = sessions.create_session(db, "js-worker", "host")
    sessions.add_capability(db, s1.id, "language", "javascript")

    s2 = sessions.create_session(db, "py-worker", "host")
    sessions.add_capability(db, s2.id, "language", "python")

    p = projects.create_project(db, "Test")
    t = tasks.create_task(db, p.id, "Python task")
    tasks.add_requirement(db, t.id, "language", "python")

    best = find_best_worker(db, t)
    assert best is not None
    assert best.name == "py-worker"


def test_find_best_worker_no_idle(db):
    s = sessions.create_session(db, "busy-worker", "host")
    sessions.update_session(db, s.id, status="working")

    p = projects.create_project(db, "Test")
    t = tasks.create_task(db, p.id, "Task")

    best = find_best_worker(db, t)
    assert best is None
