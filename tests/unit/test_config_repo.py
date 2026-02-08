"""Tests for config repository — get/set/list operations."""

from orchestrator.state.repositories.config import (
    delete_config,
    get_config,
    get_config_value,
    list_config,
    set_config,
)


def test_set_and_get(db):
    set_config(db, "test.key", "test_value", "A test key", "test")
    cfg = get_config(db, "test.key")
    assert cfg is not None
    assert cfg.key == "test.key"
    assert cfg.parsed_value == "test_value"
    assert cfg.description == "A test key"
    assert cfg.category == "test"


def test_get_config_value_default(db):
    assert get_config_value(db, "nonexistent", "default") == "default"


def test_get_config_value_existing(db):
    set_config(db, "num.key", 42)
    assert get_config_value(db, "num.key") == 42


def test_set_config_upsert(db):
    set_config(db, "upsert.key", "v1", "First", "cat")
    set_config(db, "upsert.key", "v2")
    cfg = get_config(db, "upsert.key")
    assert cfg.parsed_value == "v2"
    # Description should be preserved on upsert
    assert cfg.description == "First"


def test_list_config_all(db):
    set_config(db, "a.key", 1, category="alpha")
    set_config(db, "b.key", 2, category="beta")
    all_cfg = list_config(db)
    assert len(all_cfg) >= 2


def test_list_config_by_category(db):
    set_config(db, "cat.a", 1, category="mycat")
    set_config(db, "cat.b", 2, category="mycat")
    set_config(db, "other.a", 3, category="other")
    mycat = list_config(db, category="mycat")
    assert len(mycat) == 2
    assert all(c.category == "mycat" for c in mycat)


def test_delete_config(db):
    set_config(db, "del.key", "val")
    assert delete_config(db, "del.key") is True
    assert get_config(db, "del.key") is None


def test_delete_config_nonexistent(db):
    assert delete_config(db, "nope") is False


def test_set_config_complex_value(db):
    set_config(db, "complex", {"nested": [1, 2, 3], "flag": True})
    val = get_config_value(db, "complex")
    assert val == {"nested": [1, 2, 3], "flag": True}
