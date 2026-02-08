"""Auto-approve engine: detect trivial prompts and send responses automatically."""

from __future__ import annotations

import json
import logging
import re
import sqlite3

from orchestrator.state.repositories.config import get_config_value

logger = logging.getLogger(__name__)

# Each rule: (config_key, pattern, response_keystroke)
# The config_key maps to auto_approve.{name} in the config table.
# If enabled, when the pattern matches terminal output, the response is sent.
DEFAULT_RULES: list[tuple[str, re.Pattern, str]] = [
    # Claude Code tool approval prompts
    (
        "auto_approve.tool_calls",
        re.compile(r"Allow\s+(Read|Write|Edit|Bash|Glob|Grep|WebFetch|NotebookEdit)", re.IGNORECASE),
        "y",
    ),
    # "Do you want to proceed?" / "Continue?" / "Shall I continue?"
    (
        "auto_approve.continue_work",
        re.compile(
            r"(Do you want to proceed|Continue\?|Shall I\s+(continue|proceed)|"
            r"Want me to\s+(continue|proceed)|Should I\s+(continue|proceed|go ahead)|"
            r"Would you like me to)",
            re.IGNORECASE,
        ),
        "y",
    ),
    # "Has this been completed?"
    (
        "auto_approve.completed_check",
        re.compile(r"Has this been completed", re.IGNORECASE),
        "y",
    ),
    # Generic (y/n) or (Y/n) prompts
    (
        "auto_approve.yes_no_prompts",
        re.compile(r"\([Yy]/[Nn]\)"),
        "y",
    ),
]


def check_auto_approve(
    conn: sqlite3.Connection,
    session_name: str,
    terminal_output: str,
) -> str | None:
    """Check if the terminal output matches an auto-approve rule.

    Returns the keystroke to send (e.g. "y") if a rule matches and is enabled,
    or None if human review is needed.
    """
    if not terminal_output:
        return None

    # Check the last ~15 lines for the prompt
    recent = "\n".join(terminal_output.strip().split("\n")[-15:])

    for config_key, pattern, response in DEFAULT_RULES:
        # Check if this rule is enabled in config (default: disabled)
        raw = get_config_value(conn, config_key, "false")
        try:
            enabled = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            enabled = False

        if not enabled:
            continue

        if pattern.search(recent):
            logger.info(
                "Auto-approve rule '%s' matched for session %s",
                config_key, session_name,
            )
            return response

    return None


def seed_auto_approve_defaults(conn: sqlite3.Connection):
    """Seed default auto-approve rules into config table (all disabled by default)."""
    from orchestrator.state.repositories.config import set_config

    defaults = {
        "auto_approve.tool_calls": {
            "value": "false",
            "description": "Auto-approve Claude Code tool calls (Read, Write, Bash, etc.)",
        },
        "auto_approve.continue_work": {
            "value": "false",
            "description": "Auto-approve 'continue?' and 'shall I proceed?' prompts",
        },
        "auto_approve.completed_check": {
            "value": "false",
            "description": "Auto-approve 'Has this been completed?' prompts",
        },
        "auto_approve.yes_no_prompts": {
            "value": "false",
            "description": "Auto-approve generic (y/n) prompts",
        },
    }

    for key, info in defaults.items():
        existing = get_config_value(conn, key, None)
        if existing is None:
            set_config(conn, key, info["value"], category="auto_approve")
            logger.info("Seeded config: %s", key)
