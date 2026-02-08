#!/usr/bin/env python3
"""Simple CLI task manager — test project for orchestrator dogfooding.

Usage:
    python task_cli.py add "Buy groceries"
    python task_cli.py list
    python task_cli.py done 1
    python task_cli.py search "groceries"
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "tasks.db"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            done BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def add_task(title: str):
    conn = get_db()
    conn.execute("INSERT INTO tasks (title) VALUES (?)", (title,))
    conn.commit()
    task_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    print(f"Added task #{task_id}: {title}")
    conn.close()


def list_tasks():
    conn = get_db()
    rows = conn.execute("SELECT * FROM tasks ORDER BY done, id").fetchall()
    if not rows:
        print("No tasks yet.")
        return
    for r in rows:
        status = "[x]" if r["done"] else "[ ]"
        print(f"  {status} #{r['id']}: {r['title']}")
    conn.close()


def complete_task(task_id: int):
    conn = get_db()
    conn.execute(
        "UPDATE tasks SET done = TRUE, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (task_id,),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row:
        print(f"Completed task #{task_id}: {row['title']}")
    else:
        print(f"Task #{task_id} not found.")
    conn.close()


def search_tasks(query: str):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE title LIKE ? ORDER BY id",
        (f"%{query}%",),
    ).fetchall()
    if not rows:
        print(f"No tasks matching '{query}'.")
        return
    for r in rows:
        status = "[x]" if r["done"] else "[ ]"
        print(f"  {status} #{r['id']}: {r['title']}")
    conn.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "add" and len(sys.argv) >= 3:
        add_task(" ".join(sys.argv[2:]))
    elif cmd == "list":
        list_tasks()
    elif cmd == "done" and len(sys.argv) >= 3:
        complete_task(int(sys.argv[2]))
    elif cmd == "search" and len(sys.argv) >= 3:
        search_tasks(" ".join(sys.argv[2:]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
