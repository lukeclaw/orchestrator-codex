"""Tests for dependency graph resolution and cycle detection."""

from orchestrator.scheduler.dependencies import (
    build_dependency_graph,
    detect_cycles,
    get_ready_tasks,
    topological_sort,
)
from orchestrator.state.repositories import projects, tasks


def test_no_cycles_linear():
    graph = {"A": ["B"], "B": ["C"], "C": []}
    cycles = detect_cycles(graph)
    assert len(cycles) == 0


def test_detect_simple_cycle():
    graph = {"A": ["B"], "B": ["A"]}
    cycles = detect_cycles(graph)
    assert len(cycles) > 0


def test_detect_longer_cycle():
    graph = {"A": ["B"], "B": ["C"], "C": ["A"]}
    cycles = detect_cycles(graph)
    assert len(cycles) > 0


def test_topological_sort_linear():
    graph = {"A": ["B"], "B": ["C"], "C": []}
    result = topological_sort(graph)
    assert result is not None
    assert result.index("C") < result.index("B")
    assert result.index("B") < result.index("A")


def test_topological_sort_parallel():
    graph = {"A": [], "B": [], "C": ["A", "B"]}
    result = topological_sort(graph)
    assert result is not None
    assert result.index("A") < result.index("C")
    assert result.index("B") < result.index("C")


def test_topological_sort_cycle_returns_none():
    graph = {"A": ["B"], "B": ["A"]}
    assert topological_sort(graph) is None


def test_get_ready_tasks_no_deps(db):
    p = projects.create_project(db, "Ready Test")
    t1 = tasks.create_task(db, p.id, "Task 1")
    t2 = tasks.create_task(db, p.id, "Task 2")

    ready = get_ready_tasks(db, p.id)
    assert t1.id in ready
    assert t2.id in ready


def test_get_ready_tasks_with_deps(db):
    p = projects.create_project(db, "Dep Test")
    t1 = tasks.create_task(db, p.id, "Prerequisite")
    t2 = tasks.create_task(db, p.id, "Dependent")
    tasks.add_dependency(db, t2.id, t1.id)

    # t1 is ready but t2 is not (depends on t1)
    ready = get_ready_tasks(db, p.id)
    assert t1.id in ready
    assert t2.id not in ready

    # Complete t1, now t2 should be ready
    tasks.update_task(db, t1.id, status="done")
    ready = get_ready_tasks(db, p.id)
    assert t2.id in ready


def test_build_dependency_graph(db):
    p = projects.create_project(db, "Graph Test")
    t1 = tasks.create_task(db, p.id, "T1")
    t2 = tasks.create_task(db, p.id, "T2")
    t3 = tasks.create_task(db, p.id, "T3")
    tasks.add_dependency(db, t2.id, t1.id)
    tasks.add_dependency(db, t3.id, t2.id)

    graph = build_dependency_graph(db, p.id)
    assert t1.id in graph[t2.id]
    assert t2.id in graph[t3.id]
    assert len(graph[t1.id]) == 0
