#!/usr/bin/env python3
"""Populate the database with default config, prompt templates, and context."""

import sys
from pathlib import Path

# Add project root to path so we can import orchestrator
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.state.db import get_connection  # noqa: E402
from orchestrator.state.migrations.runner import apply_migrations  # noqa: E402
from orchestrator.state.repositories import config as config_repo  # noqa: E402
from orchestrator.state.repositories import context as context_repo  # noqa: E402


def seed_config(conn):
    """Seed default configuration values."""
    defaults = [
        # Approval policies
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
        # Context selection weights
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
        # Autonomy settings
        ("autonomy.mode", "advisory", "Current autonomy mode: advisory or autonomous", "autonomy"),
        (
            "autonomy.auto_actions",
            [],
            "Actions that can be auto-executed in autonomous mode",
            "autonomy",
        ),
        # Monitoring settings
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
        existing = config_repo.get_config(conn, key)
        if existing is None:
            config_repo.set_config(conn, key, value, description, category)


def seed_context(conn):
    """Seed default global context items."""
    items = [
        (
            "rdev VM Workflow",
            """Developers use rdev VMs for developing on remote repositories.

## Listing rdev Sessions

```bash
rdev list
```

Example output:
```
MP                  Name               Status
subs-mt             sleepy-franklin    RUNNING
jobs-mt             epic-turing        RUNNING
```

## Connecting Manually

```bash
rdev ssh MP_NAME/SESSION_NAME --non-tmux
```

The `--non-tmux` flag opens a plain SSH session instead of
attaching to a tmux session inside the VM.

Inside the VM, Claude Code can be run with
`claude --dangerously-skip-permissions`
(safe because rdev VMs are isolated sandboxes).""",
            "knowledge",
            "rdev",
        ),
        (
            "Creating rdev Workers",
            """To create a worker on an rdev VM, call the session API
with the rdev session path as the host:

```bash
curl -s -X POST http://127.0.0.1:8093/api/sessions \\
  -H 'Content-Type: application/json' \\
  -d '{"name": "worker-1", "host": "subs-mt/sleepy-franklin"}'
```

The API automatically handles the full setup:
1. Sets up a reverse SSH tunnel for API callbacks
2. Connects to the rdev VM via `rdev ssh`
3. Launches Claude Code with `--dangerously-skip-permissions`
4. Sends the worker instructions as the first chat message

The host format `MP_NAME/SESSION_NAME` (with a forward slash)
triggers rdev mode. For local workers, use `host: "localhost"`.

Use `rdev list` to discover available rdev sessions before creating workers.""",
            "guideline",
            "rdev",
        ),
    ]

    # Remove stale context items that are no longer seeded
    stale_titles = [
        "Orchestrator Architecture Overview",
        "Task and Sub-task Conventions",
        "Connecting Workers to rdev VMs",
        "LinkedIn rdev VM Workflow",
    ]
    existing = context_repo.list_context(conn, scope="global")
    existing_titles = {item.title: item for item in existing}

    for title in stale_titles:
        if title in existing_titles:
            context_repo.delete_context_item(conn, existing_titles[title].id)

    for title, content, category, source in items:
        if title not in existing_titles:
            context_repo.create_context_item(
                conn,
                title=title,
                content=content,
                scope="global",
                category=category,
                source=source,
            )


def seed_all(conn):
    """Run all seed functions."""
    seed_config(conn)
    seed_context(conn)


def main():
    """Seed the database from command line."""
    import yaml

    config_path = PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        print(f"Error: {config_path} not found")
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f)

    db_path = PROJECT_ROOT / config["database"]["path"]
    print(f"Database: {db_path}")

    conn = get_connection(db_path)

    # Apply migrations first
    applied = apply_migrations(conn)
    if applied:
        print(f"Applied migrations: {applied}")
    else:
        print("Schema is up to date.")

    # Seed data
    seed_all(conn)
    print("Seed data loaded successfully.")

    conn.close()


if __name__ == "__main__":
    main()
