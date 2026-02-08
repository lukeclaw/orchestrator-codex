"""Install /orchestrator skill into remote Claude Code sessions."""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from string import Template

from orchestrator.state.repositories import templates as templates_repo
from orchestrator.terminal import manager as tmux

logger = logging.getLogger(__name__)

VERSION_PATTERN = re.compile(r"orchestrator-skill-version:\s*(\d+)")


def check_skill_exists(
    session_name: str,
    tmux_session: str = "orchestrator",
) -> bool:
    """Check if the orchestrator skill file exists in a session."""
    # Send ls command and capture output
    tmux.send_keys(
        tmux_session, session_name,
        "ls .claude/commands/orchestrator.md 2>/dev/null && echo SKILL_EXISTS || echo SKILL_MISSING"
    )
    time.sleep(1)

    output = tmux.capture_output(tmux_session, session_name, lines=5)
    return "SKILL_EXISTS" in output


def check_skill_version(
    session_name: str,
    tmux_session: str = "orchestrator",
) -> int | None:
    """Check the version of the installed skill. Returns None if not found."""
    tmux.send_keys(
        tmux_session, session_name,
        "head -5 .claude/commands/orchestrator.md 2>/dev/null || echo NO_SKILL"
    )
    time.sleep(1)

    output = tmux.capture_output(tmux_session, session_name, lines=10)

    if "NO_SKILL" in output:
        return None

    match = VERSION_PATTERN.search(output)
    if match:
        return int(match.group(1))
    return None


def render_skill_template(
    conn: sqlite3.Connection,
    session_name: str,
    orchestrator_url: str = "http://localhost:8093",
) -> tuple[str, str] | None:
    """Load the default skill template from DB and render it.

    Returns (rendered_content, install_instruction) or None if no template.
    """
    template = templates_repo.get_default_skill_template(conn)
    if template is None:
        logger.error("No default skill template found in DB")
        return None

    # Render template variables
    variables = {
        "SKILL_VERSION": str(template.version),
        "SESSION_NAME": session_name,
        "ORCHESTRATOR_URL": orchestrator_url,
    }

    rendered = Template(template.template).safe_substitute(variables)
    instruction = template.install_instruction or (
        "Please create a custom slash command at "
        ".claude/commands/orchestrator.md with the following content."
    )

    return rendered, instruction


def install_skill(
    conn: sqlite3.Connection,
    session_name: str,
    tmux_session: str = "orchestrator",
    orchestrator_url: str = "http://localhost:8093",
) -> bool:
    """Install the orchestrator skill into a session.

    Types the instruction and skill content into Claude Code via tmux.
    """
    result = render_skill_template(conn, session_name, orchestrator_url)
    if result is None:
        return False

    rendered_content, instruction = result

    # Compose the full message to type into Claude Code
    message = f"{instruction}\n\n{rendered_content}"

    # Send to Claude Code via tmux
    # Use a single send_keys to paste the full message
    success = tmux.send_keys(tmux_session, session_name, message)

    if success:
        logger.info("Sent skill install instruction to session: %s", session_name)
    else:
        logger.error("Failed to send skill install to session: %s", session_name)

    return success


def update_skill(
    conn: sqlite3.Connection,
    session_name: str,
    tmux_session: str = "orchestrator",
    orchestrator_url: str = "http://localhost:8093",
) -> bool:
    """Update the orchestrator skill in a session."""
    result = render_skill_template(conn, session_name, orchestrator_url)
    if result is None:
        return False

    rendered_content, _ = result

    message = (
        "Please update .claude/commands/orchestrator.md with the "
        f"following updated content:\n\n{rendered_content}"
    )

    return tmux.send_keys(tmux_session, session_name, message)


def ensure_skill(
    conn: sqlite3.Connection,
    session_name: str,
    tmux_session: str = "orchestrator",
    orchestrator_url: str = "http://localhost:8093",
) -> bool:
    """Ensure the skill is installed and up-to-date. Main entry point.

    - If skill doesn't exist: install it
    - If skill exists but outdated: update it
    - If skill is current: do nothing
    """
    template = templates_repo.get_default_skill_template(conn)
    if template is None:
        logger.warning("No default skill template in DB, skipping skill install")
        return False

    exists = check_skill_exists(session_name, tmux_session)

    if not exists:
        logger.info("Skill not found in %s, installing...", session_name)
        return install_skill(conn, session_name, tmux_session, orchestrator_url)

    # Check version
    current_version = check_skill_version(session_name, tmux_session)
    if current_version is not None and current_version < template.version:
        logger.info(
            "Skill outdated in %s (v%d < v%d), updating...",
            session_name, current_version, template.version,
        )
        return update_skill(conn, session_name, tmux_session, orchestrator_url)

    logger.info("Skill is up-to-date in %s", session_name)
    return True
