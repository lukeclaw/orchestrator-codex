#!/usr/bin/env python3
"""Backfill task_prefix for projects and task_index for tasks."""

import re
import sys

sys.path.insert(0, "/Users/yuqiu/projects/my_assistant/orchestrator")

from orchestrator.state.db import get_connection


def generate_task_prefix(name: str) -> str:
    words = re.split(r"[\s\-_]+", name.strip())
    words = [w for w in words if w]
    if not words:
        return "TSK"
    if len(words) >= 3:
        return "".join(w[0] for w in words[:3]).upper()
    if len(words) == 2:
        return "".join(w[0] for w in words).upper()
    return words[0][:3].upper()


def main():
    conn = get_connection("data/orchestrator.db")

    # 1. Backfill projects with task_prefix
    projects = conn.execute("SELECT id, name, task_prefix FROM projects").fetchall()
    print(f"Found {len(projects)} projects")
    for p in projects:
        if not p["task_prefix"]:
            prefix = generate_task_prefix(p["name"])
            conn.execute("UPDATE projects SET task_prefix = ? WHERE id = ?", (prefix, p["id"]))
            print(f'  Set prefix for "{p["name"]}" -> {prefix}')

    # 2. Backfill tasks with task_index (per project, for top-level tasks)
    projects = conn.execute("SELECT id, task_prefix FROM projects").fetchall()
    for proj in projects:
        # Get top-level tasks ordered by created_at
        tasks = conn.execute(
            "SELECT id, title, task_index FROM tasks"
            " WHERE project_id = ? AND parent_task_id IS NULL ORDER BY created_at",
            (proj["id"],),
        ).fetchall()

        idx = 1
        for t in tasks:
            if t["task_index"] is None:
                conn.execute("UPDATE tasks SET task_index = ? WHERE id = ?", (idx, t["id"]))
                title_preview = t["title"][:30] if len(t["title"]) > 30 else t["title"]
                print(f'  Task "{title_preview}" -> {proj["task_prefix"]}-{idx}')
            idx += 1

        # Now handle subtasks for each parent
        parent_tasks = conn.execute(
            "SELECT id, task_index FROM tasks WHERE project_id = ? AND parent_task_id IS NULL",
            (proj["id"],),
        ).fetchall()

        for parent in parent_tasks:
            subtasks = conn.execute(
                "SELECT id, title, task_index FROM tasks"
                " WHERE parent_task_id = ? ORDER BY created_at",
                (parent["id"],),
            ).fetchall()

            sub_idx = 1
            for st in subtasks:
                if st["task_index"] is None:
                    conn.execute(
                        "UPDATE tasks SET task_index = ? WHERE id = ?", (sub_idx, st["id"])
                    )
                    title_preview = st["title"][:25] if len(st["title"]) > 25 else st["title"]
                    parent_idx = parent["task_index"]
                    print(
                        f'    Subtask "{title_preview}"'
                        f" -> {proj['task_prefix']}-{parent_idx}-{sub_idx}"
                    )
                sub_idx += 1

    conn.commit()
    print("\nBackfill complete!")
    conn.close()


if __name__ == "__main__":
    main()
