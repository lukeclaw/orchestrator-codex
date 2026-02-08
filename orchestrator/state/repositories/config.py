"""Repository for config table — get/set config by key, list by category."""

import json
import sqlite3

from orchestrator.state.models import Config


def get_config(conn: sqlite3.Connection, key: str) -> Config | None:
    row = conn.execute("SELECT * FROM config WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return Config(**dict(row))


def get_config_value(conn: sqlite3.Connection, key: str, default=None):
    """Get parsed config value, or default if not found."""
    cfg = get_config(conn, key)
    if cfg is None:
        return default
    return cfg.parsed_value


def set_config(
    conn: sqlite3.Connection,
    key: str,
    value,
    description: str | None = None,
    category: str | None = None,
) -> Config:
    json_value = json.dumps(value)
    conn.execute(
        """INSERT INTO config (key, value, description, category, updated_at)
           VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(key) DO UPDATE SET
             value = excluded.value,
             description = COALESCE(excluded.description, config.description),
             category = COALESCE(excluded.category, config.category),
             updated_at = CURRENT_TIMESTAMP""",
        (key, json_value, description, category),
    )
    conn.commit()
    return get_config(conn, key)


def list_config(conn: sqlite3.Connection, category: str | None = None) -> list[Config]:
    if category:
        rows = conn.execute(
            "SELECT * FROM config WHERE category = ? ORDER BY key", (category,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM config ORDER BY key").fetchall()
    return [Config(**dict(r)) for r in rows]


def delete_config(conn: sqlite3.Connection, key: str) -> bool:
    cursor = conn.execute("DELETE FROM config WHERE key = ?", (key,))
    conn.commit()
    return cursor.rowcount > 0
