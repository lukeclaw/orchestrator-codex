"""Migration runner: detect current version, apply pending .sql files."""

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "versions"


def get_current_version(conn: sqlite3.Connection) -> int:
    """Get the current schema version. Returns 0 if no schema exists."""
    try:
        row = conn.execute(
            "SELECT MAX(version) as v FROM schema_version"
        ).fetchone()
        return row["v"] or 0 if row else 0
    except sqlite3.OperationalError:
        return 0


def get_pending_migrations(current_version: int) -> list[tuple[int, Path]]:
    """Find all migration files with version > current_version."""
    if not MIGRATIONS_DIR.exists():
        return []

    migrations = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        # Extract version number from filename like "001_initial.sql"
        try:
            version = int(path.stem.split("_")[0])
        except (ValueError, IndexError):
            continue
        if version > current_version:
            migrations.append((version, path))

    return sorted(migrations, key=lambda x: x[0])


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply all pending migrations. Returns list of applied version numbers."""
    # Ensure schema_version table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    current = get_current_version(conn)
    pending = get_pending_migrations(current)

    applied = []
    for version, path in pending:
        sql = path.read_text()
        try:
            conn.executescript(sql)
        except sqlite3.OperationalError as e:
            # Handle "duplicate column" errors gracefully - column already exists
            if "duplicate column" in str(e).lower():
                pass  # Column already exists, migration is effectively applied
            else:
                raise
        # Record that this migration was applied
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
            (version,)
        )
        conn.commit()
        applied.append(version)

    return applied
