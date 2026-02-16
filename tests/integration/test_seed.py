"""Tests for seed script — verify expected data is populated."""

import sys
from pathlib import Path

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.seed_db import seed_all
from orchestrator.state.repositories.config import get_config, list_config


def test_seed_populates_config(db):
    seed_all(db)
    # Check approval policies
    cfg = get_config(db, "approval_policy.send_message")
    assert cfg is not None
    assert cfg.parsed_value is True

    # Check context weights
    cfg = get_config(db, "context.weight.query_relevance")
    assert cfg is not None
    assert cfg.parsed_value == 0.35

    # Check monitoring
    cfg = get_config(db, "monitoring.poll_interval_seconds")
    assert cfg is not None
    assert cfg.parsed_value == 5


def test_seed_idempotent(db):
    seed_all(db)
    count1 = len(list_config(db))
    seed_all(db)  # Run again
    count2 = len(list_config(db))
    assert count1 == count2


def test_seed_config_categories(db):
    seed_all(db)
    approval = list_config(db, category="approval")
    assert len(approval) >= 3  # send_message, assign_task, create_task, alert_user (rebrief_session removed)
    context = list_config(db, category="context")
    assert len(context) >= 6
    monitoring = list_config(db, category="monitoring")
    assert len(monitoring) >= 3


def test_seed_context_items(db):
    from orchestrator.state.repositories.context import list_context
    seed_all(db)
    items = list_context(db, scope="global")
    titles = {item.title for item in items}

    # These should exist
    assert "LinkedIn rdev VM Workflow" in titles
    assert "Creating rdev Workers" in titles

    # These should NOT exist (removed)
    assert "Orchestrator Architecture Overview" not in titles
    assert "Task and Sub-task Conventions" not in titles
    assert "Connecting Workers to rdev VMs" not in titles


def test_seed_context_idempotent(db):
    from orchestrator.state.repositories.context import list_context
    seed_all(db)
    count1 = len(list_context(db, scope="global"))
    seed_all(db)
    count2 = len(list_context(db, scope="global"))
    assert count1 == count2
