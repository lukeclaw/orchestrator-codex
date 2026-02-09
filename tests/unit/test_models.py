"""Tests for data model creation and properties."""

import json

from orchestrator.state.models import (
    Config,
    Project,
    PullRequest,
    Session,
    SkillTemplate,
    Task,
    WorkerCapability,
)


def test_project_defaults():
    p = Project(id="p1", name="Test Project")
    assert p.status == "active"
    assert p.description is None
    assert p.target_date is None


def test_session_defaults():
    s = Session(id="s1", name="worker-1", host="rdev1.example.com")
    assert s.status == "idle"
    assert s.takeover_mode is False


def test_task_defaults():
    t = Task(id="t1", project_id="p1", title="Implement feature")
    assert t.status == "todo"
    assert t.priority == 0
    assert t.assigned_session_id is None


def test_config_parsed_value_json():
    c = Config(key="test", value='{"nested": true}')
    assert c.parsed_value == {"nested": True}


def test_config_parsed_value_number():
    c = Config(key="test", value="42")
    assert c.parsed_value == 42


def test_config_parsed_value_string():
    c = Config(key="test", value='"hello"')
    assert c.parsed_value == "hello"


def test_config_parsed_value_boolean():
    c = Config(key="test", value="true")
    assert c.parsed_value is True


def test_pull_request_defaults():
    pr = PullRequest(id="pr1", url="https://github.com/org/repo/pull/1")
    assert pr.status == "open"
    assert pr.number is None


def test_worker_capability():
    wc = WorkerCapability(session_id="s1", capability_type="language", capability_value="python")
    assert wc.session_id == "s1"


def test_skill_template_defaults():
    st = SkillTemplate(id="st1", name="test", template="content")
    assert st.version == 1
    assert st.is_default is False
