"""SQLite database connection with WAL mode, retry logic, and connection factory."""

from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Default busy timeout in milliseconds
DEFAULT_BUSY_TIMEOUT_MS = 30000  # 30 seconds

# Retry configuration
MAX_RETRIES = 5
RETRY_DELAY_BASE = 0.2  # seconds (will do 0.2, 0.4, 0.8, 1.6, 3.2s = 6.2s total)


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and recommended pragmas."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={DEFAULT_BUSY_TIMEOUT_MS}")
    # Improve concurrent read performance
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def get_memory_connection() -> sqlite3.Connection:
    """Open an in-memory SQLite connection (for testing)."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class ConnectionFactory:
    """Factory for creating database connections.
    
    Provides connection-per-request pattern for better concurrency.
    """
    
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
    
    def create(self) -> sqlite3.Connection:
        """Create a new connection."""
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={DEFAULT_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn
    
    @contextmanager
    def connection(self):
        """Context manager for connection-per-request pattern."""
        conn = self.create()
        try:
            yield conn
        finally:
            conn.close()


def with_retry(func: Callable[..., T]) -> Callable[..., T]:
    """Decorator to retry database operations on lock errors.
    
    Usage:
        @with_retry
        def update_something(conn, ...):
            conn.execute(...)
            conn.commit()
    
    Note: Properly handles KeyboardInterrupt to allow Ctrl-C to work.
    """
    @wraps(func)
    def wrapper(*args, **kwargs) -> T:
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e):
                    last_error = e
                    delay = RETRY_DELAY_BASE * (2 ** attempt)
                    logger.warning(
                        "Database locked in %s (attempt %d/%d), retrying in %.2fs",
                        func.__name__, attempt + 1, MAX_RETRIES, delay
                    )
                    try:
                        time.sleep(delay)
                    except KeyboardInterrupt:
                        logger.info("Interrupted during retry, exiting")
                        raise
                else:
                    raise
        raise last_error
    return wrapper


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Context manager for explicit transaction with automatic rollback on error.
    
    Usage:
        with transaction(conn):
            conn.execute(...)
            conn.execute(...)
        # Commits automatically, rolls back on exception
    """
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
