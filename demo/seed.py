#!/usr/bin/env python3
"""Seed the demo database with realistic sample data.

Can be run standalone to (re)create the demo DB:
    python -m demo.seed

Or imported and called from demo/app.py on first launch.
"""

import json
import sqlite3
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _id() -> str:
    return str(uuid.uuid4())


def _insert(conn: sqlite3.Connection, table: str, row: dict):
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" * len(row))
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", list(row.values()))


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------

def seed_projects(conn: sqlite3.Connection) -> dict[str, str]:
    """Create demo projects. Returns {short_name: project_id}."""
    projects = [
        {
            "id": _id(),
            "name": "API Gateway Migration",
            "description": "Migrate REST endpoints from monolith to new API gateway service using Spring Boot. Decompose user, order, and product services.",
            "status": "active",
            "target_date": "2025-03-15",
            "task_prefix": "AGM",
        },
        {
            "id": _id(),
            "name": "Frontend Dashboard Redesign",
            "description": "Redesign the analytics dashboard with React 19, improving performance and adding real-time streaming charts.",
            "status": "active",
            "target_date": "2025-04-01",
            "task_prefix": "FDR",
        },
        {
            "id": _id(),
            "name": "Auth Service Hardening",
            "description": "Security audit and hardening of authentication service — rate limiting, MFA support, and token rotation.",
            "status": "completed",
            "target_date": "2025-01-30",
            "task_prefix": "ASH",
        },
    ]
    ids = {}
    for p in projects:
        _insert(conn, "projects", p)
        # Map short key to id  (e.g. "agm" -> uuid)
        ids[p["task_prefix"].lower()] = p["id"]
    return ids


def seed_sessions(conn: sqlite3.Connection) -> dict[str, str]:
    """Create demo sessions. Returns {short_name: session_id}."""
    sessions = [
        {"id": _id(), "name": "gateway-1", "host": "localhost", "status": "working", "session_type": "worker"},
        {"id": _id(), "name": "gateway-2", "host": "localhost", "status": "idle",    "session_type": "worker"},
        {"id": _id(), "name": "frontend-1","host": "localhost", "status": "working", "session_type": "worker"},
        {"id": _id(), "name": "frontend-2","host": "localhost", "status": "waiting", "session_type": "worker"},
        {"id": _id(), "name": "brain",     "host": "localhost", "status": "idle",    "session_type": "brain"},
    ]
    ids = {}
    for s in sessions:
        _insert(conn, "sessions", s)
        ids[s["name"]] = s["id"]
    return ids


def seed_project_workers(conn: sqlite3.Connection, proj: dict, sess: dict):
    """Assign workers to projects."""
    assignments = [
        (proj["agm"], sess["gateway-1"]),
        (proj["agm"], sess["gateway-2"]),
        (proj["fdr"], sess["frontend-1"]),
        (proj["fdr"], sess["frontend-2"]),
    ]
    for project_id, session_id in assignments:
        _insert(conn, "project_workers", {"project_id": project_id, "session_id": session_id})


def seed_tasks(conn: sqlite3.Connection, proj: dict, sess: dict) -> dict[str, str]:
    """Create demo tasks with subtasks. Returns {label: task_id}."""
    ids: dict[str, str] = {}

    def task(label: str, **kw):
        """Helper: create a task and store its id under `label`."""
        row = {"id": _id()}
        row.update(kw)
        _insert(conn, "tasks", row)
        ids[label] = row["id"]

    # -----------------------------------------------------------------------
    # API Gateway Migration
    # -----------------------------------------------------------------------
    agm = proj["agm"]
    gw1 = sess["gateway-1"]

    task("agm1", project_id=agm, title="Define OpenAPI schemas for user endpoints",
         status="done", assigned_session_id=gw1, priority="M", task_index=1)
    task("agm2", project_id=agm, title="Implement /users CRUD handlers",
         status="done", assigned_session_id=gw1, priority="M", task_index=2)
    task("agm3", project_id=agm, title="Implement /users/search with pagination",
         status="in_progress", assigned_session_id=gw1, priority="H", task_index=3)
    # AGM-3 subtasks
    task("agm3a", project_id=agm, title="Add Elasticsearch query builder",
         status="done", parent_task_id=ids["agm3"], priority="M", task_index=1)
    task("agm3b", project_id=agm, title="Add pagination response wrapper",
         status="in_progress", parent_task_id=ids["agm3"], priority="M", task_index=2)
    task("agm3c", project_id=agm, title="Write integration tests for search",
         status="todo", parent_task_id=ids["agm3"], priority="M", task_index=3)

    task("agm4", project_id=agm, title="Set up API rate limiting middleware",
         status="todo", priority="H", task_index=4)
    task("agm5", project_id=agm, title="Configure CI/CD pipeline for gateway service",
         status="blocked", priority="M", task_index=5)
    task("agm6", project_id=agm, title="Write load test suite with k6",
         status="todo", priority="L", task_index=6)

    # -----------------------------------------------------------------------
    # Frontend Dashboard Redesign
    # -----------------------------------------------------------------------
    fdr = proj["fdr"]
    fe1 = sess["frontend-1"]
    fe2 = sess["frontend-2"]

    task("fdr1", project_id=fdr, title="Set up Vite + React 19 project scaffold",
         status="done", priority="M", task_index=1)
    task("fdr2", project_id=fdr, title="Implement real-time WebSocket data provider",
         status="done", assigned_session_id=fe1, priority="H", task_index=2)
    task("fdr3", project_id=fdr, title="Build chart components with Recharts",
         status="in_progress", assigned_session_id=fe1, priority="H", task_index=3)
    # FDR-3 subtasks
    task("fdr3a", project_id=fdr, title="Line chart for time-series metrics",
         status="done", parent_task_id=ids["fdr3"], priority="M", task_index=1)
    task("fdr3b", project_id=fdr, title="Bar chart for comparison views",
         status="in_progress", parent_task_id=ids["fdr3"], priority="M", task_index=2)
    task("fdr3c", project_id=fdr, title="Heatmap for activity matrix",
         status="todo", parent_task_id=ids["fdr3"], priority="M", task_index=3)

    task("fdr4", project_id=fdr, title="Implement responsive grid layout",
         status="in_progress", assigned_session_id=fe2, priority="M", task_index=4)
    task("fdr5", project_id=fdr, title="Add dark mode theme support",
         status="todo", priority="M", task_index=5)
    task("fdr6", project_id=fdr, title="Performance audit and bundle optimization",
         status="todo", priority="L", task_index=6)

    # -----------------------------------------------------------------------
    # Auth Service Hardening (all done)
    # -----------------------------------------------------------------------
    ash = proj["ash"]

    task("ash1", project_id=ash, title="Implement token rotation with refresh tokens",
         status="done", priority="H", task_index=1)
    task("ash2", project_id=ash, title="Add rate limiting to login endpoint",
         status="done", priority="H", task_index=2)
    task("ash3", project_id=ash, title="Integrate TOTP-based MFA flow",
         status="done", priority="H", task_index=3)

    return ids


def seed_task_dependencies(conn: sqlite3.Connection, tasks: dict):
    """AGM-5 depends on AGM-3."""
    _insert(conn, "task_dependencies", {
        "task_id": tasks["agm5"],
        "depends_on_task_id": tasks["agm3"],
    })


def seed_decisions(conn: sqlite3.Connection, proj: dict, tasks: dict):
    """Create demo decisions."""
    # Pending decision
    _insert(conn, "decisions", {
        "id": _id(),
        "project_id": proj["agm"],
        "task_id": tasks["agm3"],
        "question": "Should the search endpoint use GraphQL or REST?",
        "options": json.dumps(["GraphQL", "REST"]),
        "context": "The search endpoint needs to support complex filtering and field selection. GraphQL would give clients flexibility but adds complexity.",
        "urgency": "normal",
        "status": "pending",
    })

    # Resolved decision
    _insert(conn, "decisions", {
        "id": _id(),
        "project_id": proj["fdr"],
        "task_id": tasks["fdr3"],
        "question": "Which charting library — Recharts vs D3 vs Chart.js?",
        "options": json.dumps(["Recharts", "D3", "Chart.js"]),
        "context": "Need a library for building interactive charts. D3 is most powerful but has a steep learning curve.",
        "urgency": "normal",
        "status": "responded",
        "response": "Recharts — simpler API, sufficient for our use case, good React integration.",
        "resolved_by": "user",
    })


def seed_context_items(conn: sqlite3.Connection, proj: dict):
    """Create demo context items."""
    items = [
        {
            "id": _id(),
            "scope": "global",
            "title": "Development Environment Setup",
            "description": "How to set up and use rdev VMs for development",
            "content": (
                "Engineers use rdev VMs for development. Run `rdev list` to see available sessions.\n\n"
                "Connect with: `rdev ssh MP_NAME/SESSION_NAME --non-tmux`\n\n"
                "Inside the VM, use `claude --dangerously-skip-permissions` (safe in sandbox)."
            ),
            "category": "knowledge",
            "source": "rdev",
        },
        {
            "id": _id(),
            "scope": "project",
            "project_id": proj["agm"],
            "title": "API Design Guidelines",
            "description": "REST conventions and patterns for the gateway service",
            "content": (
                "## Naming\n"
                "- Use plural nouns for collections: `/users`, `/orders`\n"
                "- Use kebab-case for multi-word paths: `/user-profiles`\n\n"
                "## Pagination\n"
                "- Use cursor-based pagination for large datasets\n"
                "- Return `next_cursor` and `has_more` in response envelope\n\n"
                "## Versioning\n"
                "- Prefix all routes with `/v1/`\n"
                "- Use header `Accept-Version` for breaking changes"
            ),
            "category": "guideline",
            "source": "team",
        },
        {
            "id": _id(),
            "scope": "project",
            "project_id": proj["fdr"],
            "title": "Component Library Standards",
            "description": "React patterns, accessibility, and testing conventions",
            "content": (
                "## React Patterns\n"
                "- Use functional components with hooks exclusively\n"
                "- Colocate styles using CSS modules or Tailwind utility classes\n"
                "- Extract reusable logic into custom hooks (`useXxx`)\n\n"
                "## Accessibility\n"
                "- All interactive elements must have ARIA labels\n"
                "- Support keyboard navigation (Tab, Enter, Escape)\n"
                "- Minimum color contrast ratio: 4.5:1\n\n"
                "## Testing\n"
                "- Unit test hooks and utilities with Vitest\n"
                "- Component tests with React Testing Library\n"
                "- Visual regression via Chromatic snapshots"
            ),
            "category": "guideline",
            "source": "team",
        },
        {
            "id": _id(),
            "scope": "global",
            "title": "Code Review Checklist",
            "description": "Standard checklist for reviewers",
            "content": (
                "Before approving a PR, verify:\n\n"
                "- [ ] Tests pass and cover new/changed behavior\n"
                "- [ ] No hardcoded secrets or credentials\n"
                "- [ ] Error handling is present for external calls\n"
                "- [ ] No N+1 query patterns introduced\n"
                "- [ ] API changes are backwards-compatible or versioned\n"
                "- [ ] Logging is adequate for debugging in production\n"
                "- [ ] Documentation updated if public API changed"
            ),
            "category": "guideline",
            "source": "team",
        },
    ]
    for item in items:
        _insert(conn, "context_items", item)


def seed_notifications(conn: sqlite3.Connection, tasks: dict):
    """Create demo notifications."""
    _insert(conn, "notifications", {
        "id": _id(),
        "task_id": tasks["fdr2"],
        "message": "PR #142 received 2 new review comments",
        "notification_type": "info",
        "link_url": "https://github.com/example/dashboard/pull/142",
        "dismissed": 0,
    })
    _insert(conn, "notifications", {
        "id": _id(),
        "task_id": tasks["agm3"],
        "message": "CI build passed for branch feat/user-search",
        "notification_type": "info",
        "dismissed": 0,
    })


def seed_config(conn: sqlite3.Connection):
    """Seed default runtime configuration (same as production defaults)."""
    defaults = [
        ("approval_policy.send_message", True, "Require approval before sending messages to sessions", "approval"),
        ("approval_policy.assign_task", True, "Require approval before assigning tasks", "approval"),
        ("approval_policy.create_task", False, "Require approval before creating tasks", "approval"),
        ("approval_policy.alert_user", False, "Require approval before alerting user", "approval"),
        ("context.weight.query_relevance", 0.35, "Weight for query relevance in context scoring", "context"),
        ("context.weight.recency", 0.25, "Weight for recency in context scoring", "context"),
        ("context.weight.status", 0.20, "Weight for status in context scoring", "context"),
        ("context.weight.urgency", 0.10, "Weight for urgency in context scoring", "context"),
        ("context.weight.connection_depth", 0.10, "Weight for connection depth in context scoring", "context"),
        ("context.token_budget", 8000, "Max tokens for assembled context", "context"),
        ("autonomy.mode", "advisory", "Current autonomy mode: advisory or autonomous", "autonomy"),
        ("autonomy.auto_actions", [], "Actions that can be auto-executed in autonomous mode", "autonomy"),
        ("monitoring.poll_interval_seconds", 5, "Default poll interval for passive monitor", "monitoring"),
        ("monitoring.heartbeat_timeout_seconds", 120, "Mark session stale after this many seconds", "monitoring"),
        ("monitoring.reconciliation_interval_seconds", 300, "Full state reconciliation interval", "monitoring"),
    ]
    for key, value, description, category in defaults:
        _insert(conn, "config", {
            "key": key,
            "value": json.dumps(value),
            "description": description,
            "category": category,
        })


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def seed_demo(db_path: str | None = None):
    """Create and populate the demo database."""
    if db_path is None:
        db_path = str(Path(__file__).parent / "data" / "demo.db")

    db_path_obj = Path(db_path)
    db_path_obj.parent.mkdir(parents=True, exist_ok=True)

    # Remove old DB if it exists (for clean reseeding)
    if db_path_obj.exists():
        db_path_obj.unlink()

    # Import from the main orchestrator package
    from orchestrator.state.db import get_connection
    from orchestrator.state.migrations.runner import apply_migrations

    conn = get_connection(db_path)
    apply_migrations(conn)

    # Seed all data in a single transaction
    proj = seed_projects(conn)
    sess = seed_sessions(conn)
    seed_project_workers(conn, proj, sess)
    tasks = seed_tasks(conn, proj, sess)
    seed_task_dependencies(conn, tasks)
    seed_decisions(conn, proj, tasks)
    seed_context_items(conn, proj)
    seed_notifications(conn, tasks)
    seed_config(conn)

    conn.commit()
    conn.close()

    print(f"Demo database created: {db_path}")
    print("  3 projects, 5 sessions, 15+ tasks, 2 decisions, 4 context items, 2 notifications")


if __name__ == "__main__":
    seed_demo()
