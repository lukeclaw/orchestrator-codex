"""Smart context selection algorithm (PRD Section 8.5.5)."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from orchestrator.state.repositories import (
    config as config_repo,
    decisions,
    projects,
    sessions,
    tasks,
)


@dataclass
class ContextItem:
    category: str  # "A", "B", "C"
    content: str
    score: float = 0.0
    token_estimate: int = 0


def select_context(
    conn: sqlite3.Connection,
    query: str = "",
    token_budget: int | None = None,
) -> str:
    """Assemble context for the LLM brain, fitting within token budget.

    Category A: Always include (system state summary)
    Category B: Score and select (details ranked by relevance)
    Category C: Compact summaries (for items that don't fit)
    """
    if token_budget is None:
        token_budget = config_repo.get_config_value(conn, "context.token_budget", 8000)

    weights = _load_weights(conn)
    now = datetime.now()

    # --- Category A: Always include ---
    cat_a = _build_category_a(conn)
    a_tokens = _estimate_tokens(cat_a)

    remaining = token_budget - a_tokens

    # --- Category B: Score and select ---
    b_items = _build_category_b(conn, query, weights, now)
    b_items.sort(key=lambda x: x.score, reverse=True)

    # Take B items until 80% of remaining budget
    b_budget = int(remaining * 0.8)
    selected_b = []
    b_used = 0
    for item in b_items:
        item.token_estimate = _estimate_tokens(item.content)
        if b_used + item.token_estimate <= b_budget:
            selected_b.append(item)
            b_used += item.token_estimate

    # --- Category C: Compact summaries for items that didn't fit ---
    remaining_b = [i for i in b_items if i not in selected_b]
    c_budget = remaining - b_used
    cat_c = _build_category_c(remaining_b, c_budget)

    # --- Assemble ---
    parts = ["## Current State", cat_a]

    if selected_b:
        parts.append("\n## Relevant Details")
        for item in selected_b:
            parts.append(item.content)

    if cat_c:
        parts.append("\n## Background")
        parts.append(cat_c)

    if query:
        parts.append(f"\n## Your Query\n{query}")

    return "\n\n".join(parts)


def _load_weights(conn: sqlite3.Connection) -> dict:
    return {
        "query_relevance": config_repo.get_config_value(conn, "context.weight.query_relevance", 0.35),
        "recency": config_repo.get_config_value(conn, "context.weight.recency", 0.25),
        "status": config_repo.get_config_value(conn, "context.weight.status", 0.20),
        "urgency": config_repo.get_config_value(conn, "context.weight.urgency", 0.10),
        "connection_depth": config_repo.get_config_value(conn, "context.weight.connection_depth", 0.10),
    }


def _build_category_a(conn: sqlite3.Connection) -> str:
    """Build always-include system state summary."""
    all_sessions = sessions.list_sessions(conn)
    all_projects = projects.list_projects(conn, status="active")
    pending = decisions.list_pending(conn)

    lines = [
        f"Sessions: {len(all_sessions)} total",
    ]

    # Session status summary
    status_counts: dict[str, int] = {}
    for s in all_sessions:
        status_counts[s.status] = status_counts.get(s.status, 0) + 1
    for status, count in sorted(status_counts.items()):
        lines.append(f"  - {status}: {count}")

    # Active session details (one line each)
    for s in all_sessions:
        # Look up task assigned to this session
        assigned = tasks.list_tasks(conn, assigned_session_id=s.id)
        task_info = f", task={assigned[0].id}" if assigned else ""
        lines.append(f"  [{s.status}] {s.name} @ {s.host}{task_info}")

    lines.append(f"\nProjects: {len(all_projects)} active")
    for p in all_projects:
        lines.append(f"  - {p.name} ({p.status})")

    lines.append(f"\nPending decisions: {len(pending)}")
    for d in pending:
        lines.append(f"  - [{d.urgency}] {d.question[:80]}")

    return "\n".join(lines)


def _build_category_b(
    conn: sqlite3.Connection,
    query: str,
    weights: dict,
    now: datetime,
) -> list[ContextItem]:
    """Build scored context items for category B."""
    items = []
    query_lower = query.lower()

    # Session details
    for s in sessions.list_sessions(conn):
        content = f"Session: {s.name}\nHost: {s.host}\nStatus: {s.status}"
        if s.work_dir:
            content += f"\nPath: {s.work_dir}"
        # Look up task assigned to this session
        assigned = tasks.list_tasks(conn, assigned_session_id=s.id)
        if assigned:
            t = assigned[0]
            content += f"\nCurrent task: {t.title}"
            if t.description:
                content += f"\n  {t.description[:200]}"

        score = _score_item(
            content, query_lower, weights, now,
            status=s.status,
            last_activity=s.last_activity,
        )
        items.append(ContextItem(category="B", content=content, score=score))

    # Task details
    for t in tasks.list_tasks(conn):
        content = f"Task: {t.title}\nStatus: {t.status}\nPriority: {t.priority}"
        if t.description:
            content += f"\n{t.description[:300]}"

        score = _score_item(
            content, query_lower, weights, now,
            status=t.status,
            created_at=t.created_at,
        )
        items.append(ContextItem(category="B", content=content, score=score))

    # Decision history
    for d in decisions.list_decisions(conn, status="responded"):
        content = f"Decision: {d.question}\nResponse: {d.response}"
        score = _score_item(
            content, query_lower, weights, now,
            created_at=d.created_at,
        )
        items.append(ContextItem(category="B", content=content, score=score * 0.7))

    return items


def _score_item(
    content: str,
    query_lower: str,
    weights: dict,
    now: datetime,
    status: str | None = None,
    last_activity: str | None = None,
    created_at: str | None = None,
    urgency: str | None = None,
) -> float:
    """Calculate relevance score for a context item."""
    # Query relevance: simple keyword overlap
    if query_lower:
        content_lower = content.lower()
        query_words = set(query_lower.split())
        content_words = set(content_lower.split())
        overlap = len(query_words & content_words)
        query_relevance = min(overlap / max(len(query_words), 1), 1.0)
    else:
        query_relevance = 0.5  # No query = neutral

    # Recency: exponential decay
    ts = last_activity or created_at
    if ts:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            hours_ago = max((now - dt.replace(tzinfo=None)).total_seconds() / 3600, 0)
            recency = math.exp(-hours_ago / 24)  # Half-life of ~24 hours
        except (ValueError, TypeError):
            recency = 0.5
    else:
        recency = 0.3

    # Status weight
    status_scores = {
        "working": 1.0, "error": 0.9, "blocked": 0.8,
        "in_progress": 0.8, "waiting": 0.7,
        "idle": 0.4, "todo": 0.5, "done": 0.2,
        "active": 0.6, "completed": 0.2, "archived": 0.1,
    }
    status_weight = status_scores.get(status or "", 0.5)

    # Urgency weight
    urgency_scores = {"critical": 1.0, "high": 0.7, "normal": 0.4, "low": 0.2}
    urgency_weight = urgency_scores.get(urgency or "normal", 0.4)

    # Connection depth (simplified: always 0.5 for now)
    connection = 0.5

    w = weights
    return (
        w["query_relevance"] * query_relevance
        + w["recency"] * recency
        + w["status"] * status_weight
        + w["urgency"] * urgency_weight
        + w["connection_depth"] * connection
    )


def _build_category_c(remaining_items: list[ContextItem], budget: int) -> str:
    """Build compact summaries for items that didn't fit in B."""
    if not remaining_items:
        return ""

    lines = []
    tokens_used = 0

    for item in remaining_items[:20]:  # Cap at 20 summaries
        # Compact: first line only
        first_line = item.content.split("\n")[0]
        summary = f"- {first_line} (score: {item.score:.2f})"
        est = _estimate_tokens(summary)
        if tokens_used + est > budget:
            break
        lines.append(summary)
        tokens_used += est

    return "\n".join(lines)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (4 chars per token)."""
    return len(text) // 4
