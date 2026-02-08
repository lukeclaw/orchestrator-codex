"""Tests for seed script — verify expected data is populated."""

import sys
from pathlib import Path

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.seed_db import seed_all
from orchestrator.state.repositories.config import get_config, list_config
from orchestrator.state.repositories.templates import (
    get_prompt_template,
    get_default_skill_template,
    list_prompt_templates,
    list_skill_templates,
)


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


def test_seed_populates_prompt_templates(db):
    seed_all(db)
    templates = list_prompt_templates(db)
    assert len(templates) >= 4

    system = get_prompt_template(db, "system_prompt")
    assert system is not None
    assert "Claude Orchestrator brain" in system.template

    rebrief = get_prompt_template(db, "rebrief")
    assert rebrief is not None
    assert "${session_name}" in rebrief.template


def test_seed_populates_skill_template(db):
    seed_all(db)
    skills = list_skill_templates(db)
    assert len(skills) >= 1

    default = get_default_skill_template(db)
    assert default is not None
    assert default.name == "orchestrator"
    assert "${SESSION_NAME}" in default.template
    assert "${ORCHESTRATOR_URL}" in default.template


def test_seed_idempotent(db):
    seed_all(db)
    count1 = len(list_config(db))
    seed_all(db)  # Run again
    count2 = len(list_config(db))
    assert count1 == count2


def test_seed_config_categories(db):
    seed_all(db)
    approval = list_config(db, category="approval")
    assert len(approval) >= 4
    context = list_config(db, category="context")
    assert len(context) >= 6
    monitoring = list_config(db, category="monitoring")
    assert len(monitoring) >= 3
