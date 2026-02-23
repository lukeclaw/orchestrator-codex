"""Tests for skills repository — CRUD, validation, and search."""

from unittest.mock import patch

import pytest

from orchestrator.state.repositories.skills import (
    create_skill,
    delete_skill,
    get_skill,
    is_builtin_skill_disabled,
    list_disabled_builtin_skills,
    list_skills,
    set_builtin_skill_enabled,
    update_skill,
)


# Mock _builtin_skill_names to avoid filesystem dependency in unit tests
@pytest.fixture(autouse=True)
def mock_builtin_names():
    with patch(
        "orchestrator.state.repositories.skills._builtin_skill_names",
        return_value={"create", "check-worker", "pr-workflow"},
    ):
        yield


# --- CRUD basics ---


def test_create_and_get(db):
    skill = create_skill(db, name="deploy-check", target="worker", content="# Deploy\nRun checks")
    assert skill.id
    assert skill.name == "deploy-check"
    assert skill.target == "worker"
    assert skill.content == "# Deploy\nRun checks"
    assert skill.description is None

    fetched = get_skill(db, skill.id)
    assert fetched is not None
    assert fetched.name == "deploy-check"


def test_create_with_description(db):
    skill = create_skill(
        db, name="lint", target="brain", content="body",
        description="Run linter on code",
    )
    assert skill.description == "Run linter on code"


def test_get_nonexistent(db):
    assert get_skill(db, "nope") is None


def test_list_all(db):
    create_skill(db, name="skill-a", target="worker", content="a")
    create_skill(db, name="skill-b", target="brain", content="b")
    items = list_skills(db)
    assert len(items) == 2


def test_list_filter_target(db):
    create_skill(db, name="worker-skill", target="worker", content="w")
    create_skill(db, name="brain-skill", target="brain", content="b")

    assert len(list_skills(db, target="worker")) == 1
    assert len(list_skills(db, target="brain")) == 1
    assert list_skills(db, target="worker")[0].name == "worker-skill"


def test_list_search_name(db):
    create_skill(db, name="deploy-check", target="worker", content="x")
    create_skill(db, name="lint-code", target="worker", content="y")

    results = list_skills(db, search="deploy")
    assert len(results) == 1
    assert results[0].name == "deploy-check"


def test_list_search_description(db):
    create_skill(db, name="skill-a", target="worker", content="x", description="Run unit tests")
    create_skill(db, name="skill-b", target="worker", content="y", description="Check formatting")

    results = list_skills(db, search="unit tests")
    assert len(results) == 1
    assert results[0].name == "skill-a"


def test_list_search_content(db):
    create_skill(db, name="skill-a", target="worker", content="pytest --cov")
    create_skill(db, name="skill-b", target="worker", content="eslint .")

    results = list_skills(db, search="pytest")
    assert len(results) == 1
    assert results[0].name == "skill-a"


def test_list_combined_filter(db):
    create_skill(db, name="deploy-check", target="worker", content="x")
    create_skill(db, name="deploy-brain", target="brain", content="x")
    create_skill(db, name="lint-code", target="worker", content="y")

    results = list_skills(db, target="worker", search="deploy")
    assert len(results) == 1
    assert results[0].name == "deploy-check"


# --- Update ---


def test_update(db):
    skill = create_skill(db, name="old-name", target="worker", content="old")
    updated = update_skill(db, skill.id, name="new-name", content="new content")
    assert updated.name == "new-name"
    assert updated.content == "new content"


def test_update_partial(db):
    skill = create_skill(db, name="my-skill", target="worker", content="body", description="desc")
    updated = update_skill(db, skill.id, content="new body")
    assert updated.name == "my-skill"  # unchanged
    assert updated.description == "desc"  # unchanged
    assert updated.content == "new body"


def test_update_description_to_none(db):
    """Setting description=None should clear it (sentinel ... means 'not provided')."""
    skill = create_skill(db, name="my-skill", target="worker", content="body", description="desc")
    updated = update_skill(db, skill.id, description=None)
    assert updated.description is None


def test_update_no_changes(db):
    skill = create_skill(db, name="my-skill", target="worker", content="body")
    updated = update_skill(db, skill.id)
    assert updated.name == "my-skill"


def test_update_nonexistent(db):
    result = update_skill(db, "nonexistent", name="x")
    assert result is None


def test_update_target(db):
    skill = create_skill(db, name="my-skill", target="worker", content="body")
    updated = update_skill(db, skill.id, target="brain")
    assert updated.target == "brain"


# --- Delete ---


def test_delete(db):
    skill = create_skill(db, name="del-me", target="worker", content="x")
    assert delete_skill(db, skill.id) is True
    assert get_skill(db, skill.id) is None


def test_delete_nonexistent(db):
    assert delete_skill(db, "nope") is False


# --- Validation ---


def test_invalid_name_uppercase(db):
    with pytest.raises(ValueError, match="lowercase"):
        create_skill(db, name="BadName", target="worker", content="x")


def test_invalid_name_spaces(db):
    with pytest.raises(ValueError, match="lowercase"):
        create_skill(db, name="bad name", target="worker", content="x")


def test_invalid_name_starts_with_digit(db):
    with pytest.raises(ValueError, match="lowercase"):
        create_skill(db, name="1invalid", target="worker", content="x")


def test_invalid_name_too_long(db):
    with pytest.raises(ValueError, match="50 characters"):
        create_skill(db, name="a" * 51, target="worker", content="x")


def test_invalid_target(db):
    with pytest.raises(ValueError, match="brain.*worker"):
        create_skill(db, name="my-skill", target="invalid", content="x")


def test_name_conflicts_with_builtin(db):
    with pytest.raises(ValueError, match="conflicts with a built-in"):
        create_skill(db, name="create", target="brain", content="x")


def test_update_name_conflicts_with_builtin(db):
    skill = create_skill(db, name="safe-name", target="brain", content="x")
    with pytest.raises(ValueError, match="conflicts with a built-in"):
        update_skill(db, skill.id, name="create")


def test_update_invalid_name(db):
    skill = create_skill(db, name="good-name", target="worker", content="x")
    with pytest.raises(ValueError, match="lowercase"):
        update_skill(db, skill.id, name="Bad Name")


def test_update_invalid_target(db):
    skill = create_skill(db, name="good-name", target="worker", content="x")
    with pytest.raises(ValueError, match="brain.*worker"):
        update_skill(db, skill.id, target="invalid")


# --- Unique constraint ---


def test_duplicate_name_target(db):
    create_skill(db, name="unique-skill", target="worker", content="first")
    with pytest.raises(Exception):  # sqlite3.IntegrityError
        create_skill(db, name="unique-skill", target="worker", content="second")


def test_same_name_different_target(db):
    """Same name is allowed for different targets."""
    s1 = create_skill(db, name="shared-name", target="worker", content="w")
    s2 = create_skill(db, name="shared-name", target="brain", content="b")
    assert s1.id != s2.id
    assert s1.target == "worker"
    assert s2.target == "brain"


# --- Enable/disable ---


def test_create_skill_default_enabled(db):
    skill = create_skill(db, name="enabled-skill", target="worker", content="x")
    assert skill.enabled == 1


def test_update_skill_disable(db):
    skill = create_skill(db, name="toggle-me", target="worker", content="x")
    updated = update_skill(db, skill.id, enabled=False)
    assert updated.enabled == 0


def test_update_skill_enable(db):
    skill = create_skill(db, name="toggle-back", target="worker", content="x")
    update_skill(db, skill.id, enabled=False)
    updated = update_skill(db, skill.id, enabled=True)
    assert updated.enabled == 1


def test_list_skills_enabled_only(db):
    create_skill(db, name="enabled-one", target="worker", content="x")
    s2 = create_skill(db, name="disabled-one", target="worker", content="y")
    update_skill(db, s2.id, enabled=False)

    all_skills = list_skills(db, target="worker")
    assert len(all_skills) == 2

    enabled = list_skills(db, target="worker", enabled_only=True)
    assert len(enabled) == 1
    assert enabled[0].name == "enabled-one"


# --- Built-in skill overrides ---


def test_builtin_default_is_enabled(db):
    """No override row means the built-in skill is enabled."""
    assert is_builtin_skill_disabled(db, "create", "brain") is False


def test_set_builtin_disabled(db):
    set_builtin_skill_enabled(db, "create", "brain", False)
    assert is_builtin_skill_disabled(db, "create", "brain") is True


def test_set_builtin_reenabled(db):
    set_builtin_skill_enabled(db, "create", "brain", False)
    assert is_builtin_skill_disabled(db, "create", "brain") is True

    set_builtin_skill_enabled(db, "create", "brain", True)
    assert is_builtin_skill_disabled(db, "create", "brain") is False


def test_list_disabled_builtins(db):
    set_builtin_skill_enabled(db, "create", "brain", False)
    set_builtin_skill_enabled(db, "check-worker", "brain", False)
    set_builtin_skill_enabled(db, "deploy", "worker", False)

    all_disabled = list_disabled_builtin_skills(db)
    assert ("create", "brain") in all_disabled
    assert ("check-worker", "brain") in all_disabled
    assert ("deploy", "worker") in all_disabled

    brain_disabled = list_disabled_builtin_skills(db, target="brain")
    assert ("create", "brain") in brain_disabled
    assert ("deploy", "worker") not in brain_disabled
