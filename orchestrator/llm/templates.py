"""Load templates from DB, substitute ${variables}."""

from __future__ import annotations

import sqlite3
from string import Template

from orchestrator.state.repositories.templates import get_prompt_template


def render_template(
    conn: sqlite3.Connection,
    template_name: str,
    variables: dict[str, str],
) -> str | None:
    """Load a prompt template from DB and render it with variables."""
    tpl = get_prompt_template(conn, template_name)
    if tpl is None:
        return None
    return Template(tpl.template).safe_substitute(variables)
