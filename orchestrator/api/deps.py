"""Shared dependencies for API routes."""

import sqlite3
from collections.abc import Generator

from fastapi import Request

from orchestrator.state.db import ConnectionFactory


def get_db(request: Request) -> Generator[sqlite3.Connection, None, None]:
    """Get a database connection for this request.

    Uses connection-per-request pattern for thread safety.
    FastAPI runs sync endpoints in a thread pool, so sharing a single
    connection causes sqlite3.InterfaceError under concurrent load.

    Falls back to shared connection for tests with in-memory DBs.
    """
    factory: ConnectionFactory | None = getattr(request.app.state, "conn_factory", None)
    if factory is not None:
        with factory.connection() as conn:
            yield conn
    else:
        # Fallback for tests with in-memory DB
        yield request.app.state.conn
