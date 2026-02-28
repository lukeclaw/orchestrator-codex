"""Tests for context repository — CRUD and search operations."""

from orchestrator.state.repositories.context import (
    create_context_item,
    delete_context_item,
    get_context_item,
    list_context,
    update_context_item,
)


def test_create_and_get(db):
    item = create_context_item(db, title="Test", content="Hello world")
    assert item.id
    assert item.title == "Test"
    assert item.content == "Hello world"
    assert item.scope == "global"
    assert item.project_id is None

    fetched = get_context_item(db, item.id)
    assert fetched is not None
    assert fetched.title == "Test"


def test_create_project_scoped(db):
    # Create a project first
    db.execute("INSERT INTO projects (id, name) VALUES ('p1', 'Proj 1')")
    db.commit()

    item = create_context_item(
        db,
        title="Auth pattern",
        content="Use JWT",
        scope="project",
        project_id="p1",
        category="convention",
        source="brain",
    )
    assert item.scope == "project"
    assert item.project_id == "p1"
    assert item.category == "convention"
    assert item.source == "brain"


def test_list_all(db):
    create_context_item(db, title="A", content="aaa")
    create_context_item(db, title="B", content="bbb")
    items = list_context(db)
    assert len(items) == 2


def test_list_filter_scope(db):
    db.execute("INSERT INTO projects (id, name) VALUES ('p1', 'Proj')")
    db.commit()
    create_context_item(db, title="Global", content="g", scope="global")
    create_context_item(db, title="Project", content="p", scope="project", project_id="p1")

    assert len(list_context(db, scope="global")) == 1
    assert len(list_context(db, scope="project")) == 1


def test_list_filter_project_id(db):
    db.execute("INSERT INTO projects (id, name) VALUES ('p1', 'P1')")
    db.execute("INSERT INTO projects (id, name) VALUES ('p2', 'P2')")
    db.commit()

    create_context_item(db, title="For P1", content="x", scope="project", project_id="p1")
    create_context_item(db, title="For P2", content="y", scope="project", project_id="p2")

    assert len(list_context(db, project_id="p1")) == 1
    assert list_context(db, project_id="p1")[0].title == "For P1"


def test_list_filter_category(db):
    create_context_item(db, title="A", content="a", category="note")
    create_context_item(db, title="B", content="b", category="convention")

    notes = list_context(db, category="note")
    assert len(notes) == 1
    assert notes[0].title == "A"


def test_list_search(db):
    create_context_item(db, title="Auth pattern", content="Use JWT tokens")
    create_context_item(db, title="DB conventions", content="Use PostgreSQL")

    results = list_context(db, search="JWT")
    assert len(results) == 1
    assert results[0].title == "Auth pattern"

    results = list_context(db, search="conventions")
    assert len(results) == 1
    assert results[0].title == "DB conventions"


def test_update(db):
    item = create_context_item(db, title="Old", content="old content")
    updated = update_context_item(db, item.id, title="New", content="new content")
    assert updated.title == "New"
    assert updated.content == "new content"


def test_update_partial(db):
    item = create_context_item(db, title="Title", content="Content", category="note")
    updated = update_context_item(db, item.id, category="convention")
    assert updated.title == "Title"  # unchanged
    assert updated.category == "convention"


def test_update_no_changes(db):
    item = create_context_item(db, title="T", content="C")
    updated = update_context_item(db, item.id)
    assert updated.title == "T"


def test_delete(db):
    item = create_context_item(db, title="Del", content="me")
    assert delete_context_item(db, item.id) is True
    assert get_context_item(db, item.id) is None


def test_delete_nonexistent(db):
    assert delete_context_item(db, "nope") is False


def test_get_nonexistent(db):
    assert get_context_item(db, "nope") is None
