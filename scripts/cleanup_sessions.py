#!/usr/bin/env python3
"""Clean up stale sessions from the database."""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "orchestrator.db"


def main():
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    
    # List current sessions
    print("Current sessions:")
    sessions = conn.execute("SELECT id, name, status FROM sessions").fetchall()
    for row in sessions:
        print(f"  id={row[0]}, name={row[1]}, status={row[2]}")
    
    if not sessions:
        print("No sessions found.")
        conn.close()
        return
    
    # Get names to delete (sessions without tmux windows)
    stale_names = ["c1", "brain", "worker-alpha"]
    
    # Delete stale sessions
    cursor = conn.execute(
        "DELETE FROM sessions WHERE name IN (?, ?, ?)", 
        stale_names
    )
    deleted = cursor.rowcount
    conn.commit()
    
    print(f"\nDeleted {deleted} stale session(s).")
    
    # Show remaining
    remaining = conn.execute("SELECT id, name, status FROM sessions").fetchall()
    if remaining:
        print("\nRemaining sessions:")
        for row in remaining:
            print(f"  id={row[0]}, name={row[1]}, status={row[2]}")
    else:
        print("No sessions remaining.")
    
    conn.close()


if __name__ == "__main__":
    main()
