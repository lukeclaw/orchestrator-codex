"""Tests for action parsing and validation."""

from orchestrator.llm.actions import Action, parse_actions, check_approval
from scripts.seed_db import seed_all


def test_parse_single_action():
    text = """Here's what I suggest:

```action
{"type": "send_message", "params": {"session": "worker-1", "message": "hello"}}
```

That should help."""

    actions = parse_actions(text)
    assert len(actions) == 1
    assert actions[0].type == "send_message"
    assert actions[0].params["session"] == "worker-1"


def test_parse_multiple_actions():
    text = """```action
[
  {"type": "create_task", "params": {"project_id": "p1", "title": "New task"}},
  {"type": "assign_task", "params": {"task_id": "t1", "session": "w1"}}
]
```"""

    actions = parse_actions(text)
    assert len(actions) == 2
    assert actions[0].type == "create_task"
    assert actions[1].type == "assign_task"


def test_parse_no_actions():
    text = "Just a regular response with no actions."
    actions = parse_actions(text)
    assert len(actions) == 0


def test_parse_invalid_json():
    text = """```action
{not valid json}
```"""
    actions = parse_actions(text)
    assert len(actions) == 0


def test_check_approval_default_requires(db):
    seed_all(db)
    action = Action(type="send_message", params={})
    requires = check_approval(db, action)
    assert requires is True
    assert action.requires_approval is True


def test_check_approval_no_approval_needed(db):
    seed_all(db)
    action = Action(type="create_task", params={})
    requires = check_approval(db, action)
    assert requires is False
