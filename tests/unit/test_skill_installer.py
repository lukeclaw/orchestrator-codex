"""Tests for skill installer — template rendering and version parsing."""

import re

from scripts.seed_db import seed_all
from orchestrator.terminal.skill_installer import (
    VERSION_PATTERN,
    render_skill_template,
)


def test_version_pattern_matches():
    text = "<!-- orchestrator-skill-version: 3 -->"
    match = VERSION_PATTERN.search(text)
    assert match is not None
    assert match.group(1) == "3"


def test_version_pattern_no_match():
    text = "Some random text without version"
    assert VERSION_PATTERN.search(text) is None


def test_render_skill_template(db):
    seed_all(db)
    result = render_skill_template(db, "worker-1", "http://localhost:8093")
    assert result is not None
    content, instruction = result

    # Check variables are substituted
    assert "${SESSION_NAME}" not in content
    assert "${ORCHESTRATOR_URL}" not in content
    assert "worker-1" in content
    assert "http://localhost:8093" in content

    # Check instruction is present
    assert "create a custom slash command" in instruction.lower() or "orchestrator.md" in instruction


def test_render_skill_template_no_default(db):
    # Without seeding, there's no default template
    result = render_skill_template(db, "worker-1")
    assert result is None


def test_rendered_template_has_version(db):
    seed_all(db)
    result = render_skill_template(db, "test-session")
    assert result is not None
    content, _ = result
    match = VERSION_PATTERN.search(content)
    assert match is not None
    assert int(match.group(1)) >= 1


def test_rendered_template_has_curl_commands(db):
    seed_all(db)
    result = render_skill_template(db, "test-session", "http://localhost:9090")
    assert result is not None
    content, _ = result
    assert "curl" in content
    assert "/api/report" in content
    assert "/api/decision" in content
    assert "/api/guidance" in content
    assert "http://localhost:9090" in content
