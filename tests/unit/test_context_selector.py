"""Tests for context selector — scoring, budget fitting, category assignment."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.seed_db import seed_all
from orchestrator.llm.context_selector import select_context, _score_item, _estimate_tokens
from orchestrator.state.repositories import projects, sessions, tasks, decisions
from datetime import datetime


def test_empty_state_produces_context(db):
    seed_all(db)
    context = select_context(db, "What's happening?")
    assert "Current State" in context
    assert "Sessions: 0 total" in context
    assert "Your Query" in context
    assert "What's happening?" in context


def test_context_includes_sessions(db):
    seed_all(db)
    sessions.create_session(db, "worker-1", "rdev1.example.com")
    sessions.create_session(db, "worker-2", "local")

    context = select_context(db, "show sessions")
    assert "worker-1" in context
    assert "worker-2" in context
    assert "Sessions: 2 total" in context


def test_context_includes_pending_decisions(db):
    seed_all(db)
    decisions.create_decision(db, "Which DB?", urgency="high")

    context = select_context(db)
    assert "Pending decisions: 1" in context
    assert "Which DB?" in context


def test_context_respects_budget(db):
    seed_all(db)
    # Create many items
    p = projects.create_project(db, "Big Project")
    for i in range(50):
        tasks.create_task(db, p.id, f"Task {i}", description="X" * 200)

    context = select_context(db, "overview", token_budget=2000)
    tokens = _estimate_tokens(context)
    # Should be roughly within budget (allow some slack)
    assert tokens < 3000  # Some overhead is expected


def test_score_item_query_relevance():
    weights = {
        "query_relevance": 0.35, "recency": 0.25,
        "status": 0.20, "urgency": 0.10, "connection_depth": 0.10,
    }
    now = datetime.now()

    # High relevance
    score_high = _score_item(
        "python typescript react", "python", weights, now,
        status="working",
    )
    # Low relevance
    score_low = _score_item(
        "java kotlin android", "python", weights, now,
        status="working",
    )
    assert score_high > score_low


def test_score_item_status_weight():
    weights = {
        "query_relevance": 0.35, "recency": 0.25,
        "status": 0.20, "urgency": 0.10, "connection_depth": 0.10,
    }
    now = datetime.now()

    score_working = _score_item("test", "", weights, now, status="working")
    score_done = _score_item("test", "", weights, now, status="done")
    assert score_working > score_done


def test_estimate_tokens():
    assert _estimate_tokens("") == 0
    assert _estimate_tokens("1234") == 1
    assert _estimate_tokens("12345678") == 2
