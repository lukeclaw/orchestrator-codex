"""Shared dependencies for API routes."""

import sqlite3

from fastapi import Request


def get_db(request: Request) -> sqlite3.Connection:
    """Get the database connection from app state."""
    return request.app.state.conn
