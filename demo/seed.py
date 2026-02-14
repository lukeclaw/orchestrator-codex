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


def _links(*items: tuple[str, str]) -> str:
    """Build JSON links array from (url, tag) tuples."""
    return json.dumps([{"url": url, "tag": tag} for url, tag in items])


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------

def seed_projects(conn: sqlite3.Connection) -> dict[str, str]:
    """Create demo projects. Returns {short_name: project_id}."""
    projects = [
        {
            "id": _id(),
            "name": "Inventory Service Rewrite",
            "description": (
                "Rewrite the legacy Python inventory service in Go. The current service has "
                "performance issues under load and lacks proper observability. The new service "
                "will use gRPC for internal communication, PostgreSQL for storage, and expose "
                "a REST API for external consumers."
            ),
            "status": "active",
            "target_date": "2025-04-15",
            "task_prefix": "INV",
        },
        {
            "id": _id(),
            "name": "Customer Portal v2",
            "description": (
                "Rebuild the customer-facing portal with Next.js 14 and TypeScript. Migrate "
                "from the aging Angular app to a modern stack with server components, better "
                "accessibility, and mobile-first responsive design. Includes new order tracking "
                "and account management features."
            ),
            "status": "active",
            "target_date": "2025-05-01",
            "task_prefix": "CPV",
        },
        {
            "id": _id(),
            "name": "PostgreSQL 16 Upgrade",
            "description": (
                "Upgrade all production databases from PostgreSQL 14 to 16. Includes schema "
                "audit, pg_upgrade dry runs on staging, application compatibility testing, and "
                "coordinated cutover with zero downtime using logical replication."
            ),
            "status": "completed",
            "target_date": "2025-02-01",
            "task_prefix": "PGU",
        },
    ]
    ids = {}
    for p in projects:
        _insert(conn, "projects", p)
        ids[p["task_prefix"].lower()] = p["id"]
    return ids


def seed_sessions(conn: sqlite3.Connection) -> dict[str, str]:
    """Create demo sessions. Returns {short_name: session_id}."""
    sessions = [
        {"id": _id(), "name": "backend-1",  "host": "localhost", "status": "working", "session_type": "worker"},
        {"id": _id(), "name": "backend-2",  "host": "localhost", "status": "idle",    "session_type": "worker"},
        {"id": _id(), "name": "frontend-1", "host": "localhost", "status": "working", "session_type": "worker"},
        {"id": _id(), "name": "frontend-2", "host": "localhost", "status": "waiting", "session_type": "worker"},
        {"id": _id(), "name": "brain",      "host": "localhost", "status": "idle",    "session_type": "brain"},
    ]
    ids = {}
    for s in sessions:
        _insert(conn, "sessions", s)
        ids[s["name"]] = s["id"]
    return ids


def seed_project_workers(conn: sqlite3.Connection, proj: dict, sess: dict):
    """Assign workers to projects."""
    assignments = [
        (proj["inv"], sess["backend-1"]),
        (proj["inv"], sess["backend-2"]),
        (proj["cpv"], sess["frontend-1"]),
        (proj["cpv"], sess["frontend-2"]),
    ]
    for project_id, session_id in assignments:
        _insert(conn, "project_workers", {"project_id": project_id, "session_id": session_id})


def seed_tasks(conn: sqlite3.Connection, proj: dict, sess: dict) -> dict[str, str]:
    """Create demo tasks with subtasks. Returns {label: task_id}."""
    ids: dict[str, str] = {}

    def task(label: str, **kw):
        row = {"id": _id()}
        row.update(kw)
        _insert(conn, "tasks", row)
        ids[label] = row["id"]

    # -----------------------------------------------------------------------
    # Inventory Service Rewrite
    # -----------------------------------------------------------------------
    inv = proj["inv"]
    be1 = sess["backend-1"]
    be2 = sess["backend-2"]

    task("inv1", project_id=inv, title="Set up Go project structure and dependencies",
         description=(
             "Initialize the Go module, set up directory layout following standard Go project "
             "conventions (`cmd/`, `internal/`, `pkg/`). Configure linting with golangci-lint, "
             "add Makefile targets for build/test/lint.\n\n"
             "**Acceptance criteria:**\n"
             "- `make build` produces a binary\n"
             "- `make test` runs with zero failures\n"
             "- `make lint` passes clean"
         ),
         notes=(
             "Used `golang.org/x/tools` for code generation. Settled on Chi router over Gin "
             "after benchmarking — Chi has less overhead and we don't need Gin's middleware ecosystem. "
             "Also added `sqlc` for type-safe SQL queries."
         ),
         links=_links(
             ("https://github.com/acme-corp/inventory-service/pull/1", "PR"),
         ),
         status="done", assigned_session_id=be1, priority="M", task_index=1)

    task("inv2", project_id=inv, title="Implement product CRUD endpoints",
         description=(
             "Build REST endpoints for product management:\n"
             "- `GET /v1/products` — list with filtering and pagination\n"
             "- `GET /v1/products/:id` — single product detail\n"
             "- `POST /v1/products` — create new product\n"
             "- `PUT /v1/products/:id` — update product\n"
             "- `DELETE /v1/products/:id` — soft delete\n\n"
             "All endpoints should return proper HTTP status codes and validate input with "
             "structured error responses."
         ),
         notes=(
             "Added request validation middleware using `go-playground/validator`. Soft delete "
             "uses a `deleted_at` timestamp column. Pagination follows cursor-based pattern "
             "with `?cursor=<id>&limit=50` — tested with 100k rows, p99 under 12ms."
         ),
         links=_links(
             ("https://github.com/acme-corp/inventory-service/pull/4", "PR"),
             ("https://docs.google.com/document/d/1a2b3c/edit", "API spec"),
         ),
         status="done", assigned_session_id=be1, priority="H", task_index=2)

    task("inv3", project_id=inv, title="Build inventory tracking and stock management",
         description=(
             "Core business logic for tracking stock levels across warehouses. Needs to handle "
             "concurrent stock updates safely (optimistic locking), support bulk operations for "
             "receiving shipments, and emit events on stock changes for downstream consumers.\n\n"
             "**Key requirements:**\n"
             "- Atomic stock adjustments (no overselling)\n"
             "- Audit trail for all stock movements\n"
             "- Warehouse-level and aggregate stock queries\n"
             "- Event emission via outbox pattern"
         ),
         status="in_progress", assigned_session_id=be1, priority="H", task_index=3)

    # INV-3 subtasks
    task("inv3a", project_id=inv, title="Implement stock level calculations with optimistic locking",
         description=(
             "Core stock adjustment logic using PostgreSQL advisory locks or row-level "
             "versioning. Must handle concurrent writes without data races. Include "
             "`adjust_stock`, `transfer_stock`, and `bulk_receive` operations."
         ),
         notes=(
             "Went with row-level version column (`UPDATE ... WHERE version = $expected`). "
             "Retry logic handles conflicts — tested with 50 concurrent goroutines, no lost updates. "
             "Advisory locks added too much contention."
         ),
         links=_links(
             ("https://github.com/acme-corp/inventory-service/pull/8", "PR"),
         ),
         status="done", parent_task_id=None, priority="H", task_index=1)  # parent set below

    task("inv3b", project_id=inv, title="Add warehouse location mapping and multi-warehouse queries",
         description=(
             "Extend the inventory model with warehouse locations. Products can exist in multiple "
             "warehouses with independent stock levels. Add endpoints:\n"
             "- `GET /v1/warehouses/:id/stock` — stock levels for a warehouse\n"
             "- `GET /v1/products/:id/availability` — aggregate across warehouses\n"
             "- `POST /v1/warehouses/:id/receive` — bulk receive shipment"
         ),
         notes="Schema migration done. Working on the aggregation query — need to handle warehouses with zero stock correctly in the LEFT JOIN.",
         status="in_progress", parent_task_id=None, priority="M", task_index=2)

    task("inv3c", project_id=inv, title="Implement outbox pattern for stock change events",
         description=(
             "Stock changes need to emit domain events for downstream consumers (order service, "
             "analytics). Use the transactional outbox pattern — write events to an `outbox` "
             "table in the same transaction as stock changes, then relay to Kafka.\n\n"
             "Events: `stock.adjusted`, `stock.transferred`, `stock.received`"
         ),
         status="todo", parent_task_id=None, priority="M", task_index=3)

    # Fix parent_task_id for subtasks (can't reference forward)
    for label in ("inv3a", "inv3b", "inv3c"):
        conn.execute("UPDATE tasks SET parent_task_id = ? WHERE id = ?", [ids["inv3"], ids[label]])

    task("inv4", project_id=inv, title="Set up PostgreSQL schema and migration tooling",
         description=(
             "Design the production schema for products, warehouses, stock_levels, and "
             "stock_movements tables. Set up `golang-migrate` for versioned migrations. "
             "Include indexes for common query patterns and foreign key constraints.\n\n"
             "Schema should support the soft-delete pattern and outbox table."
         ),
         links=_links(
             ("https://docs.google.com/document/d/4d5e6f/edit", "Schema design"),
         ),
         status="todo", priority="H", task_index=4)

    task("inv5", project_id=inv, title="Configure Docker setup and CI pipeline",
         description=(
             "Create multi-stage Dockerfile for the Go service. Set up GitHub Actions CI:\n"
             "- Lint, test, build on every PR\n"
             "- Integration tests against PostgreSQL (use testcontainers)\n"
             "- Build and push Docker image on merge to main\n"
             "- Deploy to staging automatically"
         ),
         status="blocked", priority="M", task_index=5)

    task("inv6", project_id=inv, title="Write load tests and benchmark critical paths",
         description=(
             "Use k6 to write load test scenarios for:\n"
             "- Product listing with various filter combinations\n"
             "- Concurrent stock adjustments (the hot path)\n"
             "- Bulk shipment receiving\n\n"
             "Target: 2000 RPS with p99 < 50ms for reads, p99 < 100ms for writes. "
             "Run against staging with production-like data volume."
         ),
         status="todo", priority="L", task_index=6)

    # -----------------------------------------------------------------------
    # Customer Portal v2
    # -----------------------------------------------------------------------
    cpv = proj["cpv"]
    fe1 = sess["frontend-1"]
    fe2 = sess["frontend-2"]

    task("cpv1", project_id=cpv, title="Initialize Next.js 14 project with TypeScript and Tailwind",
         description=(
             "Scaffold the new customer portal using Next.js 14 App Router. Configure:\n"
             "- TypeScript strict mode\n"
             "- Tailwind CSS with custom design tokens\n"
             "- ESLint + Prettier\n"
             "- Husky pre-commit hooks\n"
             "- Path aliases (`@/components`, `@/lib`, etc.)"
         ),
         notes="Used `create-next-app` with the App Router template. Added Radix UI primitives for accessible base components. Tailwind config extends the brand color palette from the design system Figma.",
         links=_links(
             ("https://github.com/acme-corp/customer-portal/pull/1", "PR"),
             ("https://www.figma.com/file/abc123/Customer-Portal-v2", "Design"),
         ),
         status="done", priority="M", task_index=1)

    task("cpv2", project_id=cpv, title="Build authentication flow with NextAuth.js",
         description=(
             "Implement sign-in, sign-up, and sign-out flows using NextAuth.js with:\n"
             "- Email/password credentials provider\n"
             "- Google OAuth provider\n"
             "- JWT session strategy with refresh token rotation\n"
             "- Protected route middleware\n"
             "- \"Remember me\" persistent sessions\n\n"
             "Must handle edge cases: expired sessions, concurrent tabs, account linking."
         ),
         notes=(
             "NextAuth v5 beta had issues with the App Router middleware — rolled back to v4 "
             "stable. JWT refresh logic uses a sliding window: refresh if token expires within "
             "15 min. Added CSRF protection on all auth endpoints."
         ),
         links=_links(
             ("https://github.com/acme-corp/customer-portal/pull/5", "PR"),
             ("https://github.com/acme-corp/customer-portal/pull/7", "Fix: session refresh"),
         ),
         status="done", assigned_session_id=fe1, priority="H", task_index=2)

    task("cpv3", project_id=cpv, title="Implement order history page",
         description=(
             "Full order history view for authenticated customers:\n"
             "- Paginated order list with status filters (pending, shipped, delivered, returned)\n"
             "- Order detail view with item breakdown and shipment timeline\n"
             "- Search by order number or product name\n"
             "- CSV export for date ranges\n\n"
             "Use React Server Components for the initial data fetch, client components for "
             "interactive filters and infinite scroll."
         ),
         status="in_progress", assigned_session_id=fe1, priority="H", task_index=3)

    # CPV-3 subtasks
    task("cpv3a", project_id=cpv, title="Create order list component with status filters",
         description=(
             "Server component that fetches paginated orders. Client-side filter bar for "
             "status, date range, and search. Infinite scroll with `useInfiniteQuery` from "
             "TanStack Query. Empty state and loading skeletons."
         ),
         notes="Infinite scroll working. Had to debounce the search input (300ms) to avoid hammering the API. Status filter uses URL search params so it's shareable/bookmarkable.",
         links=_links(
             ("https://github.com/acme-corp/customer-portal/pull/12", "PR"),
         ),
         status="done", parent_task_id=None, priority="M", task_index=1)

    task("cpv3b", project_id=cpv, title="Build order detail view with shipment timeline",
         description=(
             "Detailed view for a single order showing:\n"
             "- Order items with quantities, prices, and product images\n"
             "- Shipment status timeline (ordered -> processing -> shipped -> delivered)\n"
             "- Tracking number with carrier link\n"
             "- Return/refund request button"
         ),
         notes="Timeline component renders correctly. Still need to wire up the tracking number deep links for FedEx and UPS.",
         status="in_progress", parent_task_id=None, priority="M", task_index=2)

    task("cpv3c", project_id=cpv, title="Add CSV export for order history",
         description=(
             "Allow users to export their order history as CSV. Should support:\n"
             "- Date range picker for filtering\n"
             "- Include order number, date, items, amounts, status\n"
             "- Stream download for large exports (> 1000 orders)\n"
             "- Format currency and dates according to user locale"
         ),
         status="todo", parent_task_id=None, priority="L", task_index=3)

    for label in ("cpv3a", "cpv3b", "cpv3c"):
        conn.execute("UPDATE tasks SET parent_task_id = ? WHERE id = ?", [ids["cpv3"], ids[label]])

    task("cpv4", project_id=cpv, title="Build account settings and profile page",
         description=(
             "Account management page with sections:\n"
             "- Profile info (name, email, avatar upload)\n"
             "- Password change with current password verification\n"
             "- Notification preferences (email, SMS toggles)\n"
             "- Connected accounts (Google, etc.)\n"
             "- Danger zone: delete account with confirmation\n\n"
             "All forms should have optimistic updates with rollback on error."
         ),
         notes="Working on avatar upload — using presigned S3 URLs. The image crop/resize component is surprisingly tricky to get right on mobile.",
         links=_links(
             ("https://www.figma.com/file/abc123/Customer-Portal-v2?node-id=42", "Design: Account"),
         ),
         status="in_progress", assigned_session_id=fe2, priority="M", task_index=4)

    task("cpv5", project_id=cpv, title="Add end-to-end tests with Playwright",
         description=(
             "E2E test coverage for critical user flows:\n"
             "- Sign up -> verify email -> first login\n"
             "- Browse orders -> view detail -> request return\n"
             "- Update profile -> change password -> sign out\n\n"
             "Run in CI against a seeded test database. Include both desktop and mobile viewport tests."
         ),
         status="todo", priority="M", task_index=5)

    task("cpv6", project_id=cpv, title="Performance optimization and Lighthouse audit",
         description=(
             "Target Lighthouse scores: Performance > 90, Accessibility > 95, Best Practices > 90.\n\n"
             "Focus areas:\n"
             "- Bundle size analysis with `@next/bundle-analyzer`\n"
             "- Image optimization with `next/image` and WebP\n"
             "- Critical CSS inlining\n"
             "- Lazy loading below-the-fold components\n"
             "- Core Web Vitals: LCP < 2.5s, FID < 100ms, CLS < 0.1"
         ),
         status="todo", priority="L", task_index=6)

    # -----------------------------------------------------------------------
    # PostgreSQL 16 Upgrade (all done)
    # -----------------------------------------------------------------------
    pgu = proj["pgu"]

    task("pgu1", project_id=pgu, title="Audit schema and create migration plan",
         description=(
             "Document all databases, schemas, extensions, and custom types. Identify "
             "deprecated features used in PG 14 that changed in PG 16. Create a sequenced "
             "migration plan with rollback procedures for each step."
         ),
         notes=(
             "Found 3 uses of `to_tsquery` without explicit config param — PG 16 changed "
             "the default behavior. Also flagged 2 extensions that need upgrading: "
             "`pg_stat_statements` and `pg_trgm`. Created runbook in Notion."
         ),
         links=_links(
             ("https://notion.so/acme/pg16-migration-runbook-abc123", "Runbook"),
         ),
         status="done", priority="H", task_index=1)

    task("pgu2", project_id=pgu, title="Set up PostgreSQL 16 staging and run pg_upgrade",
         description=(
             "Provision a PG 16 staging instance mirroring production config. Run "
             "`pg_upgrade --check` first, then actual upgrade. Validate data integrity with "
             "row counts, checksum comparisons, and application smoke tests."
         ),
         notes="pg_upgrade completed in 47 minutes on staging (1.2TB). Data checksums matched. All application smoke tests passed after updating the connection strings.",
         status="done", priority="H", task_index=2)

    task("pgu3", project_id=pgu, title="Production cutover with logical replication",
         description=(
             "Zero-downtime cutover using logical replication:\n"
             "1. Set up logical replication from PG 14 -> PG 16\n"
             "2. Let replication catch up (monitor lag)\n"
             "3. Stop writes, verify sync, switch DNS\n"
             "4. Verify application connectivity\n"
             "5. Decommission PG 14 instance after 48h soak"
         ),
         notes=(
             "Cutover executed during the Saturday maintenance window. Replication lag was "
             "under 200ms at switch time. Total write downtime: 8 seconds. All services "
             "reconnected within 30 seconds. PG 14 instance decommissioned Monday."
         ),
         links=_links(
             ("https://notion.so/acme/pg16-cutover-postmortem-def456", "Postmortem"),
         ),
         status="done", priority="H", task_index=3)

    return ids


def seed_context_items(conn: sqlite3.Connection, proj: dict):
    """Create demo context items."""
    items = [
        {
            "id": _id(),
            "scope": "global",
            "title": "Development Workflow",
            "description": "Branch, test, and deploy conventions",
            "content": (
                "## Branch naming\n"
                "- Feature branches: `feat/<ticket>-short-description`\n"
                "- Bug fixes: `fix/<ticket>-short-description`\n"
                "- Hotfixes: `hotfix/<description>`\n\n"
                "## Commit messages\n"
                "Follow Conventional Commits: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`\n\n"
                "## Pull requests\n"
                "- Require 1 approval minimum\n"
                "- CI must pass (lint, test, build)\n"
                "- Squash merge to main\n"
                "- Delete branch after merge"
            ),
            "category": "guideline",
            "source": "team",
        },
        {
            "id": _id(),
            "scope": "project",
            "project_id": proj["inv"],
            "title": "Go Service Conventions",
            "description": "Patterns and practices for the inventory service",
            "content": (
                "## Project layout\n"
                "```\n"
                "cmd/server/       — main entrypoint\n"
                "internal/handler/ — HTTP handlers\n"
                "internal/service/ — business logic\n"
                "internal/repo/    — database layer\n"
                "internal/model/   — domain types\n"
                "```\n\n"
                "## Error handling\n"
                "- Return domain errors from service layer, map to HTTP in handlers\n"
                "- Use `fmt.Errorf` with `%w` for wrapping\n"
                "- Structured logging with `slog`\n\n"
                "## Database\n"
                "- Use `sqlc` for type-safe queries\n"
                "- Migrations in `migrations/` with `golang-migrate`\n"
                "- Always use transactions for multi-step writes"
            ),
            "category": "guideline",
            "source": "team",
        },
        {
            "id": _id(),
            "scope": "project",
            "project_id": proj["cpv"],
            "title": "Frontend Standards",
            "description": "React and Next.js conventions for the customer portal",
            "content": (
                "## Components\n"
                "- Use Server Components by default, Client Components only when needed\n"
                "- Colocate styles with Tailwind utility classes\n"
                "- Extract reusable UI into `@/components/ui/`\n"
                "- Page-specific components live next to their page\n\n"
                "## Data fetching\n"
                "- Server Components: use `fetch` with Next.js caching\n"
                "- Client Components: use TanStack Query with `@/lib/api` client\n"
                "- Optimistic updates for user-initiated mutations\n\n"
                "## Accessibility\n"
                "- All interactive elements need ARIA labels\n"
                "- Keyboard navigation support (Tab, Enter, Escape)\n"
                "- Minimum contrast ratio: 4.5:1\n"
                "- Test with VoiceOver and axe-core"
            ),
            "category": "guideline",
            "source": "team",
        },
        {
            "id": _id(),
            "scope": "global",
            "title": "Code Review Checklist",
            "description": "Standard criteria for reviewing pull requests",
            "content": (
                "Before approving a PR, verify:\n\n"
                "- [ ] Tests cover new/changed behavior\n"
                "- [ ] No hardcoded secrets or credentials\n"
                "- [ ] Error handling for external calls\n"
                "- [ ] No N+1 query patterns\n"
                "- [ ] API changes are versioned or backwards-compatible\n"
                "- [ ] Logging sufficient for production debugging\n"
                "- [ ] Docs updated if public API changed\n"
                "- [ ] No unnecessary dependencies added"
            ),
            "category": "guideline",
            "source": "team",
        },
        {
            "id": _id(),
            "scope": "global",
            "title": "Incident Response",
            "description": "What to do when something breaks in production",
            "content": (
                "## Severity levels\n"
                "- **P0** — Complete outage, all users affected. Page on-call immediately.\n"
                "- **P1** — Major feature broken, >10% users impacted. Slack #incidents.\n"
                "- **P2** — Minor issue, workaround exists. File a ticket.\n\n"
                "## Response steps\n"
                "1. Acknowledge in #incidents with severity assessment\n"
                "2. Check Grafana dashboards and recent deploys\n"
                "3. If caused by a deploy, roll back first, investigate second\n"
                "4. Post updates every 30 min for P0, every hour for P1\n"
                "5. Write postmortem within 48 hours for P0/P1"
            ),
            "category": "knowledge",
            "source": "team",
        },
    ]
    for item in items:
        _insert(conn, "context_items", item)


def seed_notifications(conn: sqlite3.Connection, tasks: dict):
    """Create demo notifications."""
    notifications = [
        {
            "id": _id(),
            "task_id": tasks["cpv2"],
            "message": "PR #7 merged: fix session refresh race condition",
            "notification_type": "info",
            "link_url": "https://github.com/acme-corp/customer-portal/pull/7",
            "dismissed": 1,
        },
        {
            "id": _id(),
            "task_id": tasks["cpv3b"],
            "message": "PR #14 has 2 review comments requesting changes",
            "notification_type": "pr_comment",
            "link_url": "https://github.com/acme-corp/customer-portal/pull/14",
            "dismissed": 0,
        },
        {
            "id": _id(),
            "task_id": tasks["inv3b"],
            "message": "CI failed on branch feat/warehouse-mapping: test_aggregate_stock",
            "notification_type": "warning",
            "link_url": "https://github.com/acme-corp/inventory-service/actions/runs/12345",
            "dismissed": 0,
        },
        {
            "id": _id(),
            "task_id": tasks["inv2"],
            "message": "Staging deploy successful for inventory-service v0.3.0",
            "notification_type": "info",
            "dismissed": 1,
        },
        {
            "id": _id(),
            "task_id": tasks["cpv4"],
            "message": "Design updated: new avatar upload flow added to Figma",
            "notification_type": "info",
            "link_url": "https://www.figma.com/file/abc123/Customer-Portal-v2?node-id=42",
            "dismissed": 0,
        },
    ]
    for n in notifications:
        _insert(conn, "notifications", n)


def seed_config(conn: sqlite3.Connection):
    """Seed default runtime configuration."""
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
    seed_context_items(conn, proj)
    seed_notifications(conn, tasks)
    seed_config(conn)

    conn.commit()
    conn.close()

    print(f"Demo database created: {db_path}")
    print("  3 projects, 5 sessions, 18 tasks, 5 context items, 5 notifications")


if __name__ == "__main__":
    seed_demo()
