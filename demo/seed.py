#!/usr/bin/env python3
"""Seed the demo database with realistic sample data.

Represents a snapshot of Wednesday morning from the demo story:
- Notifications service extraction: Phase 2 in progress
- Structured logging migration: 3/8 repos done, 2 workers active
- Maintenance: P1 fixed, flaky tests in backlog

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
            "name": "Notifications Service Extraction",
            "description": (
                "Extract the notifications module from the monolith into a standalone gRPC service. "
                "The module currently handles user notification preferences, email/push delivery, "
                "and real-time delivery status — all tightly coupled to the shared database and "
                "imported as a library by a dozen API consumers.\n\n"
                "**Phases:**\n"
                "1. Define gRPC API contract\n"
                "2. Build the new service (data layer, business logic, delivery integration)\n"
                "3. Dual-write migration to move consumers off the monolith library\n"
                "4. Decommission old code and drop shared DB tables"
            ),
            "status": "active",
            "target_date": "2025-06-30",
            "task_prefix": "NSE",
        },
        {
            "id": _id(),
            "name": "Structured Logging Migration",
            "description": (
                "Replace log4j with structured JSON logging across all team services. "
                "Security flagged log4j as a priority remediation item. Eight repos total — "
                "most are straightforward, but a few have custom appenders for compliance "
                "and metrics extraction that need careful handling."
            ),
            "status": "active",
            "target_date": "2025-04-15",
            "task_prefix": "LOG",
        },
        {
            "id": _id(),
            "name": "Maintenance & Operations",
            "description": (
                "Ongoing production issues, tech debt, flaky tests, dependency updates, "
                "and ad-hoc operational work. Things that don't fit a project but still "
                "need to get done."
            ),
            "status": "active",
            "target_date": None,
            "task_prefix": "OPS",
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
        {
            "id": _id(),
            "name": "backend-1",
            "host": "localhost",
            "status": "working",
            "session_type": "worker",
        },
        {
            "id": _id(),
            "name": "backend-2",
            "host": "localhost",
            "status": "waiting",
            "session_type": "worker",
        },
        {
            "id": _id(),
            "name": "logging-1",
            "host": "localhost",
            "status": "working",
            "session_type": "worker",
        },
        {
            "id": _id(),
            "name": "logging-2",
            "host": "localhost",
            "status": "waiting",
            "session_type": "worker",
        },
        {
            "id": _id(),
            "name": "brain",
            "host": "localhost",
            "status": "idle",
            "session_type": "brain",
        },
    ]
    ids = {}
    for s in sessions:
        _insert(conn, "sessions", s)
        ids[s["name"]] = s["id"]
    return ids


def seed_tasks(conn: sqlite3.Connection, proj: dict, sess: dict) -> dict[str, str]:
    """Create demo tasks with subtasks. Returns {label: task_id}."""
    ids: dict[str, str] = {}

    def task(label: str, **kw):
        row = {"id": _id()}
        row.update(kw)
        _insert(conn, "tasks", row)
        ids[label] = row["id"]

    # ===================================================================
    # Notifications Service Extraction (NSE)
    # ===================================================================
    nse = proj["nse"]
    be1 = sess["backend-1"]
    be2 = sess["backend-2"]

    # --- Phase 1: done ---

    task(
        "nse1",
        project_id=nse,
        title="Define gRPC API contract and proto definitions",
        description=(
            "Design the service API as protobuf definitions. Must cover:\n"
            "- `NotificationPreferences` CRUD (get, update, bulk get)\n"
            "- `SendNotification` for single and batch sends\n"
            "- `DeliveryStatus` streaming endpoint for real-time status\n"
            "- Standard error codes and pagination patterns\n\n"
            "Follow the team proto style guide. Field numbering must leave room "
            "for future expansion (skip to 10 for each logical group)."
        ),
        notes=(
            "Initial version used a unary RPC for batch sends with a repeated field. "
            "Changed to client-streaming after review — batch sizes vary from 10 to 50k "
            "during campaigns, and unary would hit the 4MB gRPC message limit. "
            "Streaming also lets the server start processing before the full batch arrives."
        ),
        links=_links(
            ("https://github.com/acme-corp/notification-service/pull/1", "PR"),
            ("https://github.com/acme-corp/notification-service/pull/3", "Fix: batch streaming"),
        ),
        status="done",
        priority="H",
        task_index=1,
    )

    task(
        "nse2",
        project_id=nse,
        title="Set up database schema and preferences CRUD",
        description=(
            "Create the notification service's own database schema. Migrate the preferences "
            "data model from the monolith's shared tables into standalone tables.\n\n"
            "Tables needed:\n"
            "- `notification_preferences` (user_id, channel, enabled, frequency, updated_at)\n"
            "- `notification_templates` (id, type, channel, template, variables)\n"
            "- `delivery_log` (id, user_id, type, channel, status, sent_at, delivered_at)\n\n"
            "Implement the preferences CRUD gRPC endpoints with full test coverage."
        ),
        notes=(
            "Used the same ULID generation for IDs as the monolith for consistency. "
            "Added a composite index on (user_id, channel) for the preferences lookup — "
            "that's the hot path. Delivery log uses a time-partitioned table for retention."
        ),
        links=_links(
            ("https://github.com/acme-corp/notification-service/pull/5", "PR"),
        ),
        status="done",
        priority="H",
        task_index=2,
    )

    # --- Phase 2: in progress ---

    task(
        "nse3",
        project_id=nse,
        title="Implement delivery pipeline integration",
        description=(
            "The new service needs to actually deliver notifications. Integrate with:\n"
            "- SendGrid API for email delivery\n"
            "- Firebase Cloud Messaging for push notifications\n"
            "- Internal webhook system for in-app notifications\n\n"
            "Extract the retry/backoff logic from the monolith's `NotificationSender` class "
            "into a reusable package. The monolith uses exponential backoff with jitter — "
            "keep the same behavior but make it configurable per channel.\n\n"
            "Must handle partial failures in batch sends (some succeed, some fail)."
        ),
        status="in_progress",
        assigned_session_id=be1,
        priority="H",
        task_index=3,
    )

    # NSE-3 subtasks
    task(
        "nse3a",
        project_id=nse,
        title="Extract retry/backoff logic into reusable package",
        description=(
            "Pull the exponential backoff with jitter logic from "
            "`monolith/notifications/sender.py` into a standalone Go package. "
            "Make max retries, base delay, and max delay configurable per channel."
        ),
        notes=(
            "Extracted to `pkg/retry`. Configurable per-channel: email gets 5 retries "
            "with 30s base, push gets 3 retries with 5s base. Added circuit breaker "
            "on top — if a provider fails 10 times in a row, stop sending for 60s."
        ),
        links=_links(
            ("https://github.com/acme-corp/notification-service/pull/8", "PR"),
        ),
        status="done",
        parent_task_id=None,
        priority="H",
        task_index=1,
    )

    task(
        "nse3b",
        project_id=nse,
        title="Integrate email and push notification providers",
        description=(
            "Wire up the SendGrid and FCM clients using the retry package. "
            "Implement the `SendNotification` gRPC handler:\n"
            "- Look up user preferences to determine channels\n"
            "- Fan out to enabled channels in parallel\n"
            "- Record delivery status in the delivery log\n"
            "- Return per-recipient results for batch sends"
        ),
        notes="SendGrid integration done. Working on FCM — their v1 API requires OAuth2 service account auth instead of the old server key. Updating the auth flow.",
        status="in_progress",
        parent_task_id=None,
        priority="H",
        task_index=2,
    )

    task(
        "nse3c",
        project_id=nse,
        title="Add delivery status streaming endpoint",
        description=(
            "Implement the `DeliveryStatus` server-streaming RPC. Clients subscribe "
            "with a notification ID and receive status updates as delivery progresses "
            "(queued -> sending -> delivered/failed per channel).\n\n"
            "Use a Redis pub/sub channel per notification ID. Clean up subscriptions "
            "after 5 minutes of inactivity."
        ),
        status="todo",
        parent_task_id=None,
        priority="M",
        task_index=3,
    )

    for label in ("nse3a", "nse3b", "nse3c"):
        conn.execute("UPDATE tasks SET parent_task_id = ? WHERE id = ?", [ids["nse3"], ids[label]])

    task(
        "nse4",
        project_id=nse,
        title="Write data migration script and consistency checker",
        description=(
            "Build the dual-write migration for moving notification data from the "
            "monolith's shared database to the new service's database.\n\n"
            "**Migration script:**\n"
            "- Backfill existing preferences and templates from shared DB\n"
            "- Set up CDC (change data capture) for ongoing sync during transition\n"
            "- Use the existing CDC framework, don't build a custom sync\n\n"
            "**Consistency checker:**\n"
            "- Nightly job that compares records between old and new DB\n"
            "- Alert on discrepancies but don't block writes\n"
            "- Dashboard showing sync status and lag"
        ),
        notes=(
            "Backfill complete — 2.3M preference records migrated in 12 minutes using "
            "batch inserts. CDC stream set up via Debezium, lag is under 500ms. "
            "Consistency checker runs nightly at 3am UTC. Found 0 discrepancies in "
            "the first run against staging."
        ),
        links=_links(
            ("https://github.com/acme-corp/notification-service/pull/12", "PR"),
        ),
        status="in_progress",
        assigned_session_id=be2,
        priority="H",
        task_index=4,
    )

    # --- Phase 3 & 4: future ---

    task(
        "nse5",
        project_id=nse,
        title="Build consumer migration SDK for Phase 3",
        description=(
            "Create a drop-in replacement SDK that mirrors the monolith library's API "
            "but calls the new gRPC service underneath. This lets consumers migrate "
            "with minimal code changes — swap the import, update the config, done.\n\n"
            "The SDK should:\n"
            "- Match the existing `NotificationClient` interface exactly\n"
            "- Add gRPC connection pooling and deadline propagation\n"
            "- Include a feature flag to toggle between old (library) and new (gRPC) paths\n"
            "- Log any behavior differences during the transition"
        ),
        status="todo",
        priority="M",
        task_index=5,
    )

    task(
        "nse6",
        project_id=nse,
        title="Decommission old notification code in monolith",
        description=(
            "Once all consumers are migrated to the new service (Phase 3 complete):\n"
            "- Remove the `notifications` module from the monolith\n"
            "- Drop the shared database tables (preferences, templates, delivery_log)\n"
            "- Remove the CDC stream\n"
            "- Update the monolith's dependency graph\n\n"
            "This is the final cleanup. Only start after all consumers are verified on the new service."
        ),
        status="blocked",
        priority="L",
        task_index=6,
    )

    # ===================================================================
    # Structured Logging Migration (LOG)
    # ===================================================================
    log = proj["log"]
    lg1 = sess["logging-1"]
    lg2 = sess["logging-2"]

    task(
        "log1",
        project_id=log,
        title="Update shared logging config library",
        description=(
            "The shared `logging-config` library is imported by all services for log setup. "
            "Update it to:\n"
            "- Default to structured JSON output on stdout\n"
            "- Support a `LOG_FORMAT` env var to switch between JSON and human-readable\n"
            "- Keep backward compatibility so services can migrate incrementally\n"
            "- Update the README with migration instructions"
        ),
        notes=(
            "Published v2.0.0 with structured JSON as default. Added `LOG_FORMAT=pretty` "
            "for local development. All existing tests pass — backward compat maintained "
            "through the env var toggle. Updated the migration guide with examples."
        ),
        links=_links(
            ("https://github.com/acme-corp/logging-config/pull/28", "PR"),
            (
                "https://github.com/acme-corp/logging-config/blob/main/MIGRATION.md",
                "Migration guide",
            ),
        ),
        status="done",
        priority="H",
        task_index=1,
    )

    task(
        "log2",
        project_id=log,
        title="Migrate user-service to structured logging",
        description=(
            "Replace log4j usage in the user-service with the updated logging-config library. "
            "This repo is a straightforward migration — no custom appenders or sinks.\n\n"
            "- Swap log4j imports for logging-config v2\n"
            "- Remove log4j.xml config file\n"
            "- Update any tests that assert on log output format\n"
            "- Run full test suite, verify structured output in staging"
        ),
        notes="Clean migration. 14 files changed, all test assertions updated. No surprises.",
        links=_links(
            ("https://github.com/acme-corp/user-service/pull/91", "PR"),
        ),
        status="done",
        priority="M",
        task_index=2,
    )

    task(
        "log3",
        project_id=log,
        title="Migrate delivery-pipeline to structured logging",
        description=(
            "Replace logging in the delivery-pipeline service. **Watch out:** this repo has "
            "a custom log appender that writes to a shared NFS volume at `/var/log/compliance/`. "
            "The compliance team reads from that path for audit trail.\n\n"
            "Keep the NFS appender as a secondary sink alongside structured stdout. "
            "File a follow-up ticket to move compliance to the centralized log store."
        ),
        notes=(
            "Kept the NFS compliance appender as a secondary sink — writes both structured "
            "JSON to stdout and the existing format to /var/log/compliance/. Filed JIRA-5102 "
            "to migrate compliance team to the centralized log store. They acknowledged and "
            "added it to their Q2 roadmap."
        ),
        links=_links(
            ("https://github.com/acme-corp/delivery-pipeline/pull/67", "PR"),
        ),
        status="done",
        priority="M",
        task_index=3,
    )

    task(
        "log4",
        project_id=log,
        title="Migrate payments-service to structured logging",
        description=(
            "Replace commons-logging in the payments-service. This repo uses the older "
            "`commons-logging` framework instead of log4j. Same migration pattern:\n"
            "- Swap to logging-config v2\n"
            "- Remove commons-logging config\n"
            "- Update test assertions\n"
            "- Verify in staging"
        ),
        status="in_progress",
        assigned_session_id=lg1,
        priority="M",
        task_index=4,
    )

    task(
        "log5",
        project_id=log,
        title="Migrate search-service to structured logging",
        description=(
            "Replace log4j in the search-service. **Note:** this repo has a custom metrics "
            "appender that parses latency percentiles (p50/p95/p99) from log lines and "
            "exposes them as Prometheus metrics.\n\n"
            "If changing the log format will break this, migrate to proper metrics "
            "instrumentation instead of parsing logs. The `micrometer` library is already "
            "a dependency in this repo."
        ),
        notes=(
            "Found the custom `LatencyMetricsAppender` that regex-parses log lines for "
            "timing data. Changing to structured JSON will break the regex patterns. "
            "Flagged for decision: migrate to proper micrometer histograms, or preserve "
            "the old log format for this service?"
        ),
        status="in_progress",
        assigned_session_id=lg2,
        priority="M",
        task_index=5,
    )

    task(
        "log6",
        project_id=log,
        title="Migrate analytics-service to structured logging",
        description=(
            "Replace the homegrown logging wrapper in analytics-service. This repo uses a "
            "custom `AnalyticsLogger` class that wraps log4j. Swap the underlying implementation "
            "to logging-config v2 while keeping the `AnalyticsLogger` interface for now.\n\n"
            "Was blocked on the shared logging-config library update (LOG-1). Now unblocked."
        ),
        status="todo",
        priority="M",
        task_index=6,
    )

    task(
        "log7",
        project_id=log,
        title="Migrate recommendations-service to structured logging",
        description=(
            "Replace log4j in the recommendations-service. Straightforward migration, no "
            "custom appenders. Was blocked on the shared logging-config library update (LOG-1)."
        ),
        status="todo",
        priority="M",
        task_index=7,
    )

    task(
        "log8",
        project_id=log,
        title="Migrate gateway-service to structured logging",
        description=(
            "Replace log4j in the API gateway service. This is a high-traffic service — "
            "verify that structured JSON serialization doesn't add measurable latency. "
            "Benchmark before and after with a load test.\n\n"
            "The gateway also has access logs in a specific format that the WAF team parses. "
            "Check with them before changing the access log format."
        ),
        status="todo",
        priority="M",
        task_index=8,
    )

    # ===================================================================
    # Maintenance & Operations (OPS)
    # ===================================================================
    ops = proj["ops"]

    task(
        "ops1",
        project_id=ops,
        title="Fix connection pool exhaustion on shared notifications DB",
        description=(
            "P1: Notification preferences API returning 500s. Users can't update email "
            "settings. Root cause appears to be connection pool exhaustion on the shared "
            "database — likely a long-running analytics query hogging connections.\n\n"
            "**Immediate fix:** identify and kill the blocking query, add statement timeout.\n"
            "**Follow-up:** write a one-pager on read replica setup for analytics workload."
        ),
        notes=(
            "Found the culprit: an unindexed JOIN from the analytics dashboard running "
            "for 45+ minutes, holding 8 of 10 pool connections. Killed the query, added "
            "a 30-second `statement_timeout` for the analytics role, and added the missing "
            "index on `delivery_log(created_at, status)` so the query runs in 2 seconds.\n\n"
            "Read replica one-pager written and shared in #notifications-eng. The connection "
            "pool issue is a symptom of the shared DB problem — this accelerates the case "
            "for the service extraction."
        ),
        links=_links(
            ("https://github.com/acme-corp/monolith/pull/482", "PR: statement timeout + index"),
            ("https://docs.google.com/document/d/1x2y3z/edit", "Read replica one-pager"),
        ),
        status="done",
        priority="H",
        task_index=1,
    )

    task(
        "ops2",
        project_id=ops,
        title="Fix flaky test suite in notifications module",
        description=(
            "The notifications module test suite has been failing intermittently for three "
            "months. The team re-runs and hopes. Root cause is test isolation — tests share "
            "a database and don't clean up after themselves.\n\n"
            "**Fix:** Each test should get its own database transaction that rolls back after "
            "the test. Look at how the users-service tests handle this — they have a good "
            "pattern with transaction-scoped fixtures.\n\n"
            "**Target:** Zero flaky failures in 50 consecutive CI runs."
        ),
        status="todo",
        priority="M",
        task_index=2,
    )

    task(
        "ops3",
        project_id=ops,
        title="Deprecate v1 notification preferences endpoint",
        description=(
            "Three external teams still hit the deprecated `/v1/preferences` REST endpoint "
            "instead of the `/v2/preferences` endpoint. Add deprecation headers, emit metrics "
            "to track remaining traffic, and reach out to the teams with a migration timeline.\n\n"
            "Don't remove it yet — just make it visible and set a date."
        ),
        status="todo",
        priority="L",
        task_index=3,
    )

    return ids


def seed_context_items(conn: sqlite3.Connection, proj: dict):
    """Create demo context items."""
    items = [
        {
            "id": _id(),
            "scope": "global",
            "title": "Code Review Standards",
            "description": "Team conventions for reviewing pull requests",
            "content": (
                "Before approving a PR, verify:\n\n"
                "- [ ] Tests cover new and changed behavior\n"
                "- [ ] No hardcoded secrets, credentials, or API keys\n"
                "- [ ] Error handling present for all external calls\n"
                "- [ ] No N+1 query patterns introduced\n"
                "- [ ] API changes are backward compatible or versioned\n"
                "- [ ] Logging adequate for production debugging\n"
                "- [ ] No unnecessary new dependencies"
            ),
            "category": "guideline",
            "source": "team",
        },
        {
            "id": _id(),
            "scope": "global",
            "title": "Incident Response",
            "description": "What to do when something breaks",
            "content": (
                "## Severity\n"
                "- **P0** — Full outage. Page on-call. All hands.\n"
                "- **P1** — Major feature broken, >10% users affected. Slack #incidents.\n"
                "- **P2** — Minor issue, workaround available. File a ticket.\n\n"
                "## Response\n"
                "1. Acknowledge in #incidents with severity\n"
                "2. Check dashboards and recent deploys\n"
                "3. If deploy-related: roll back first, investigate second\n"
                "4. Updates every 30min (P0) or hourly (P1)\n"
                "5. Postmortem within 48h for P0/P1"
            ),
            "category": "knowledge",
            "source": "team",
        },
        {
            "id": _id(),
            "scope": "project",
            "project_id": proj["nse"],
            "title": "Notifications Service Architecture",
            "description": "Design decisions and technical context for the service extraction",
            "content": (
                "## Service boundary\n"
                "The notifications service owns: user notification preferences, delivery "
                "(email/push/webhook), delivery status tracking, and notification templates.\n\n"
                "It does NOT own: notification content generation (that stays in the producing "
                "services), user contact info (owned by user-service), or notification UI "
                "(owned by frontend).\n\n"
                "## Data\n"
                "- Own database, no shared tables with the monolith after migration\n"
                "- CDC via Debezium during the transition period\n"
                "- IDs use ULIDs for consistency with the monolith\n\n"
                "## Delivery\n"
                "- SendGrid for email, FCM for push, internal webhook for in-app\n"
                "- Retry with exponential backoff + jitter, configurable per channel\n"
                "- Circuit breaker per provider (10 consecutive failures = 60s cooldown)\n\n"
                "## API\n"
                "- gRPC for service-to-service communication\n"
                "- Client-streaming for batch sends (campaign sizes up to 50k)\n"
                "- Server-streaming for delivery status updates"
            ),
            "category": "reference",
            "source": "design",
        },
        {
            "id": _id(),
            "scope": "project",
            "project_id": proj["log"],
            "title": "Logging Migration Guide",
            "description": "Step-by-step guide for migrating services to structured logging",
            "content": (
                "## Overview\n"
                "Replace log4j/commons-logging/custom wrappers with `logging-config` v2.0+. "
                "The new library outputs structured JSON to stdout by default.\n\n"
                "## Steps\n"
                "1. Update `logging-config` dependency to v2.0+\n"
                "2. Remove old logging framework dependency and config files\n"
                "3. Replace import statements\n"
                "4. Update test assertions that check log output format\n"
                "5. Test locally with `LOG_FORMAT=pretty` for readable output\n"
                "6. Deploy to staging and verify JSON output in Kibana\n\n"
                "## Watch out for\n"
                "- Custom appenders (NFS, metrics, etc.) — don't remove without checking consumers\n"
                "- Log format parsing in monitoring tools — update dashboards and alerts\n"
                "- Access log formats used by WAF or security tools\n"
                "- Performance: structured JSON adds ~2% overhead at high throughput"
            ),
            "category": "guideline",
            "source": "team",
        },
        {
            "id": _id(),
            "scope": "global",
            "title": "Git and PR Conventions",
            "description": "Branch naming, commit style, and PR workflow",
            "content": (
                "## Branches\n"
                "- Feature: `feat/<ticket>-short-description`\n"
                "- Bug fix: `fix/<ticket>-short-description`\n"
                "- Hotfix: `hotfix/<description>`\n\n"
                "## Commits\n"
                "Conventional commits: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`\n\n"
                "## PRs\n"
                "- Require 1 approval\n"
                "- CI must be green (lint, test, build)\n"
                "- Squash merge to main\n"
                "- Delete branch after merge"
            ),
            "category": "guideline",
            "source": "team",
        },
    ]
    for item in items:
        _insert(conn, "context_items", item)


def seed_notifications(conn: sqlite3.Connection, tasks: dict, sess: dict):
    """Create demo notifications."""
    notifications = [
        {
            "id": _id(),
            "task_id": tasks["nse1"],
            "message": "PR #3 merged: gRPC batch sends changed from unary to client-streaming",
            "notification_type": "info",
            "link_url": "https://github.com/acme-corp/notification-service/pull/3",
            "dismissed": 1,
        },
        {
            "id": _id(),
            "task_id": tasks["nse4"],
            "message": "PR #12 ready for review: data migration script with consistency checker",
            "notification_type": "info",
            "link_url": "https://github.com/acme-corp/notification-service/pull/12",
            "dismissed": 0,
        },
        {
            "id": _id(),
            "task_id": tasks["log5"],
            "session_id": sess["logging-2"],
            "message": "logging-2 needs a decision: search-service has a custom metrics appender that parses latency from logs. Migrate to proper metrics instrumentation, or preserve the log format?",
            "notification_type": "warning",
            "dismissed": 0,
        },
        {
            "id": _id(),
            "task_id": tasks["ops1"],
            "message": "P1 resolved: connection pool fix merged, statement timeout added",
            "notification_type": "info",
            "link_url": "https://github.com/acme-corp/monolith/pull/482",
            "dismissed": 1,
        },
        {
            "id": _id(),
            "task_id": tasks["log3"],
            "message": "CI passed: delivery-pipeline logging migration verified on staging",
            "notification_type": "info",
            "dismissed": 1,
        },
    ]
    for n in notifications:
        _insert(conn, "notifications", n)


def seed_config(conn: sqlite3.Connection):
    """Seed default runtime configuration."""
    defaults = [
        (
            "approval_policy.send_message",
            True,
            "Require approval before sending messages to sessions",
            "approval",
        ),
        (
            "approval_policy.assign_task",
            True,
            "Require approval before assigning tasks",
            "approval",
        ),
        (
            "approval_policy.create_task",
            False,
            "Require approval before creating tasks",
            "approval",
        ),
        ("approval_policy.alert_user", False, "Require approval before alerting user", "approval"),
        (
            "context.weight.query_relevance",
            0.35,
            "Weight for query relevance in context scoring",
            "context",
        ),
        ("context.weight.recency", 0.25, "Weight for recency in context scoring", "context"),
        ("context.weight.status", 0.20, "Weight for status in context scoring", "context"),
        ("context.weight.urgency", 0.10, "Weight for urgency in context scoring", "context"),
        (
            "context.weight.connection_depth",
            0.10,
            "Weight for connection depth in context scoring",
            "context",
        ),
        ("context.token_budget", 8000, "Max tokens for assembled context", "context"),
        ("autonomy.mode", "advisory", "Current autonomy mode: advisory or autonomous", "autonomy"),
        (
            "autonomy.auto_actions",
            [],
            "Actions that can be auto-executed in autonomous mode",
            "autonomy",
        ),
        (
            "monitoring.poll_interval_seconds",
            5,
            "Default poll interval for passive monitor",
            "monitoring",
        ),
        (
            "monitoring.heartbeat_timeout_seconds",
            120,
            "Mark session stale after this many seconds",
            "monitoring",
        ),
        (
            "monitoring.reconciliation_interval_seconds",
            300,
            "Full state reconciliation interval",
            "monitoring",
        ),
    ]
    for key, value, description, category in defaults:
        _insert(
            conn,
            "config",
            {
                "key": key,
                "value": json.dumps(value),
                "description": description,
                "category": category,
            },
        )


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
    tasks = seed_tasks(conn, proj, sess)
    seed_context_items(conn, proj)
    seed_notifications(conn, tasks, sess)
    seed_config(conn)

    conn.commit()
    conn.close()

    print(f"Demo database created: {db_path}")
    print("  3 projects, 5 sessions, 20 tasks, 5 context items, 5 notifications")


if __name__ == "__main__":
    seed_demo()
