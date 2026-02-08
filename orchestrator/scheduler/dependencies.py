"""Dependency graph: resolve order, detect cycles, cascade blockers."""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from orchestrator.state.repositories import tasks


def build_dependency_graph(conn: sqlite3.Connection, project_id: str) -> dict[str, list[str]]:
    """Build a dependency graph for a project. Returns {task_id: [depends_on_ids]}."""
    all_tasks = tasks.list_tasks(conn, project_id=project_id)
    graph: dict[str, list[str]] = {}

    for task in all_tasks:
        deps = tasks.get_dependencies(conn, task.id)
        graph[task.id] = [d.depends_on_task_id for d in deps]

    return graph


def detect_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """Detect cycles in the dependency graph using DFS."""
    visited: set[str] = set()
    in_stack: set[str] = set()
    cycles: list[list[str]] = []
    path: list[str] = []

    def dfs(node: str):
        visited.add(node)
        in_stack.add(node)
        path.append(node)

        for dep in graph.get(node, []):
            if dep not in visited:
                dfs(dep)
            elif dep in in_stack:
                # Found a cycle
                cycle_start = path.index(dep)
                cycles.append(path[cycle_start:] + [dep])

        path.pop()
        in_stack.discard(node)

    for node in graph:
        if node not in visited:
            dfs(node)

    return cycles


def topological_sort(graph: dict[str, list[str]]) -> list[str] | None:
    """Return tasks in execution order. Returns None if cycles exist."""
    in_degree: dict[str, int] = defaultdict(int)
    for node in graph:
        if node not in in_degree:
            in_degree[node] = 0
        for dep in graph.get(node, []):
            in_degree[node] += 1
            if dep not in in_degree:
                in_degree[dep] = 0

    queue = [n for n, d in in_degree.items() if d == 0]
    result = []

    while queue:
        node = queue.pop(0)
        result.append(node)

        # Find nodes that depend on this one
        for candidate, deps in graph.items():
            if node in deps:
                in_degree[candidate] -= 1
                if in_degree[candidate] == 0:
                    queue.append(candidate)

    if len(result) != len(in_degree):
        return None  # Cycle detected

    return result


def get_ready_tasks(
    conn: sqlite3.Connection,
    project_id: str,
) -> list[str]:
    """Get task IDs that are ready to execute (all dependencies met)."""
    all_tasks = tasks.list_tasks(conn, project_id=project_id, status="todo")
    ready = []

    for task in all_tasks:
        deps = tasks.get_dependencies(conn, task.id)
        if not deps:
            ready.append(task.id)
            continue

        # Check if all dependencies are done
        all_done = True
        for dep in deps:
            dep_task = tasks.get_task(conn, dep.depends_on_task_id)
            if dep_task and dep_task.status != "done":
                all_done = False
                break

        if all_done:
            ready.append(task.id)

    return ready
