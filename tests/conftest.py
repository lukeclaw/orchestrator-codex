"""Shared test fixtures."""


import pytest

from orchestrator.state.db import get_memory_connection
from orchestrator.state.migrations.runner import apply_migrations


@pytest.fixture
def db():
    """In-memory SQLite DB with schema applied."""
    conn = get_memory_connection()
    apply_migrations(conn)
    yield conn
    conn.close()


@pytest.fixture
def seeded_db(db):
    """In-memory DB with schema + seed data."""
    from scripts.seed_db import seed_all
    seed_all(db)
    return db
