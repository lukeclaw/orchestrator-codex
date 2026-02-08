"""Tests for path conflict detection."""

from orchestrator.scheduler.conflicts import Conflict, detect_path_conflicts
from orchestrator.state.repositories import sessions


def test_no_conflicts_empty(db):
    conflicts = detect_path_conflicts(db)
    assert conflicts == []


def test_no_conflicts_different_paths(db):
    sessions.create_session(db, "worker-1", "host-a", mp_path="/repo/a")
    sessions.create_session(db, "worker-2", "host-b", mp_path="/repo/b")
    conflicts = detect_path_conflicts(db)
    assert conflicts == []


def test_conflict_same_path(db):
    sessions.create_session(db, "worker-1", "host-a", mp_path="/shared/repo")
    sessions.create_session(db, "worker-2", "host-b", mp_path="/shared/repo")

    conflicts = detect_path_conflicts(db)
    assert len(conflicts) == 1
    assert conflicts[0].session_a == "worker-1"
    assert conflicts[0].session_b == "worker-2"
    assert "/shared/repo" in conflicts[0].overlap


def test_no_conflict_inactive_sessions(db):
    s1 = sessions.create_session(db, "stopped-1", "host", mp_path="/shared/repo")
    sessions.update_session(db, s1.id, status="disconnected")
    s2 = sessions.create_session(db, "stopped-2", "host", mp_path="/shared/repo")
    sessions.update_session(db, s2.id, status="disconnected")

    conflicts = detect_path_conflicts(db)
    assert conflicts == []


def test_no_conflict_no_mp_path(db):
    sessions.create_session(db, "worker-1", "host")
    sessions.create_session(db, "worker-2", "host")
    # Both idle but no mp_path set
    conflicts = detect_path_conflicts(db)
    assert conflicts == []


def test_conflict_dataclass():
    c = Conflict(session_a="a", session_b="b", overlap="same dir")
    assert c.session_a == "a"
    assert c.session_b == "b"
    assert c.overlap == "same dir"
