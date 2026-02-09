#!/usr/bin/env python3
"""Populate the database with default config, prompt templates, and skill templates."""

import sys
from pathlib import Path

# Add project root to path so we can import orchestrator
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.state.db import get_connection
from orchestrator.state.migrations.runner import apply_migrations
from orchestrator.state.repositories import config as config_repo
from orchestrator.state.repositories import context as context_repo
from orchestrator.state.repositories import templates as templates_repo


def seed_config(conn):
    """Seed default configuration values."""
    defaults = [
        # Approval policies
        ("approval_policy.send_message", True, "Require approval before sending messages to sessions", "approval"),
        ("approval_policy.assign_task", True, "Require approval before assigning tasks", "approval"),
        ("approval_policy.create_task", False, "Require approval before creating tasks", "approval"),
        ("approval_policy.rebrief_session", True, "Require approval before re-briefing sessions", "approval"),
        ("approval_policy.alert_user", False, "Require approval before alerting user", "approval"),

        # Context selection weights
        ("context.weight.query_relevance", 0.35, "Weight for query relevance in context scoring", "context"),
        ("context.weight.recency", 0.25, "Weight for recency in context scoring", "context"),
        ("context.weight.status", 0.20, "Weight for status in context scoring", "context"),
        ("context.weight.urgency", 0.10, "Weight for urgency in context scoring", "context"),
        ("context.weight.connection_depth", 0.10, "Weight for connection depth in context scoring", "context"),
        ("context.token_budget", 8000, "Max tokens for assembled context", "context"),

        # Autonomy settings
        ("autonomy.mode", "advisory", "Current autonomy mode: advisory or autonomous", "autonomy"),
        ("autonomy.auto_actions", [], "Actions that can be auto-executed in autonomous mode", "autonomy"),

        # Monitoring settings
        ("monitoring.poll_interval_seconds", 5, "Default poll interval for passive monitor", "monitoring"),
        ("monitoring.heartbeat_timeout_seconds", 120, "Mark session stale after this many seconds", "monitoring"),
        ("monitoring.reconciliation_interval_seconds", 300, "Full state reconciliation interval", "monitoring"),
    ]

    for key, value, description, category in defaults:
        existing = config_repo.get_config(conn, key)
        if existing is None:
            config_repo.set_config(conn, key, value, description, category)


def seed_prompt_templates(conn):
    """Seed default LLM prompt templates."""
    templates = [
        (
            "system_prompt",
            """You are the Claude Orchestrator brain — an intelligent coordinator managing multiple Claude Code sessions working on software engineering tasks.

## Current State
${system_state}

## Your Role
- Analyze the current state of all sessions, tasks, and projects
- Answer user questions about session status, task progress, and project health
- Propose actions when appropriate (send messages, assign tasks)
- Always explain your reasoning before proposing actions

## Rules
- Never fabricate information about session states — only report what you observe
- When unsure, say so and suggest how to investigate
- Propose actions but don't assume approval — wait for user confirmation
- Keep responses concise and actionable""",
            "Main system prompt for the LLM brain",
        ),
        (
            "status_query",
            """Summarize the current state of the orchestrator:

${system_state}

Provide a brief, structured summary covering:
1. Active sessions and what they're doing
2. Task progress and any blockers
3. Any issues that need attention""",
            "Template for status summary queries",
        ),
        (
            "task_planning",
            """Given this project and its current tasks:

Project: ${project_name}
Description: ${project_description}

Current Tasks:
${task_list}

Available Workers:
${worker_list}

Suggest a task assignment plan. Consider:
- Worker capabilities and current workload
- Task dependencies and priority
- Parallelization opportunities""",
            "Template for task planning and assignment",
        ),
        (
            "rebrief",
            """You previously lost context (due to /compact or restart). Here is your current assignment:

## Session: ${session_name}
## Current Task: ${task_summary}

## Files You Were Working On:
${file_paths}

## Last Known State:
${last_known_state}

Please acknowledge this context and continue your work. If you need any clarification, ask and wait for guidance.""",
            "Template for re-briefing sessions after context loss",
        ),
    ]

    for name, template, description in templates:
        existing = templates_repo.get_prompt_template(conn, name)
        if existing is None:
            templates_repo.create_prompt_template(conn, name, template, description)


def seed_skill_templates(conn):
    """Seed default skill template for remote sessions."""
    existing = templates_repo.get_skill_template(conn, "orchestrator")
    if existing is not None:
        return

    skill_template = """# Orchestrator Integration Skill
<!-- orchestrator-skill-version: ${SKILL_VERSION} -->

You are connected to an orchestrator system managing multiple Claude Code
sessions. Use this skill to report your progress.

## Environment

- Session Name: ${SESSION_NAME}
- Orchestrator URL: ${ORCHESTRATOR_URL}

## Report Progress

After completing significant milestones, report them:

    curl -sX POST ${ORCHESTRATOR_URL}/api/report \\
      -H "Content-Type: application/json" \\
      -d '{"session":"${SESSION_NAME}","event":"task_progress",
           "data":{"task":"DESCRIPTION","progress":PERCENT,
                   "subtasks":[{"name":"...","done":true/false}]}}'

## Report PR Creation

When you create a pull request:

    curl -sX POST ${ORCHESTRATOR_URL}/api/report \\
      -H "Content-Type: application/json" \\
      -d '{"session":"${SESSION_NAME}","event":"pr_created",
           "data":{"url":"PR_URL","title":"PR_TITLE"}}'

## Check for Guidance

Before starting major work:

    curl -s "${ORCHESTRATOR_URL}/api/guidance?session=${SESSION_NAME}"

## Report Errors

When blocked by errors:

    curl -sX POST ${ORCHESTRATOR_URL}/api/report \\
      -H "Content-Type: application/json" \\
      -d '{"session":"${SESSION_NAME}","event":"error",
           "data":{"type":"ERROR_TYPE","message":"DESCRIPTION"}}'

## Best Practices

1. Report progress after completing each significant subtask
2. Check for guidance at the start of each major task
3. Report PRs immediately after creation
4. Report errors when blocked

The orchestrator may send you messages directly through the terminal.
Always acknowledge received instructions."""

    install_instruction = (
        "Please create a custom slash command at .claude/commands/orchestrator.md "
        "with the following content. This is an orchestrator integration skill "
        "that I need you to use for reporting progress."
    )

    templates_repo.create_skill_template(
        conn,
        name="orchestrator",
        template=skill_template,
        install_instruction=install_instruction,
        description="Default orchestrator integration skill installed into remote Claude Code sessions",
        is_default=True,
    )


def seed_context(conn):
    """Seed default global context items."""
    items = [
        (
            "LinkedIn rdev VM Workflow",
            """LinkedIn engineers use rdev VMs for developing on multiproduct (MP) repositories.

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

The `--non-tmux` flag opens a plain SSH session instead of attaching to a tmux session inside the VM.

Inside the VM, Claude Code can be run with `claude --dangerously-skip-permissions` (safe because rdev VMs are isolated sandboxes).""",
            "knowledge",
            "rdev",
        ),
        (
            "Creating rdev Workers",
            """To create a worker on an rdev VM, call the session API with the rdev session path as the host:

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

The host format `MP_NAME/SESSION_NAME` (with a forward slash) triggers rdev mode. For local workers, use `host: "localhost"`.

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
    seed_prompt_templates(conn)
    seed_skill_templates(conn)
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
