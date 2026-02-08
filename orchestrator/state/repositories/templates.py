"""Repository for prompt_templates and skill_templates tables."""

import sqlite3
import uuid

from orchestrator.state.models import PromptTemplate, SkillTemplate


# --- Prompt Templates ---

def get_prompt_template(conn: sqlite3.Connection, name: str) -> PromptTemplate | None:
    row = conn.execute(
        "SELECT * FROM prompt_templates WHERE name = ? AND is_active = TRUE", (name,)
    ).fetchone()
    if row is None:
        return None
    return PromptTemplate(**dict(row))


def get_prompt_template_by_id(conn: sqlite3.Connection, id: str) -> PromptTemplate | None:
    row = conn.execute("SELECT * FROM prompt_templates WHERE id = ?", (id,)).fetchone()
    if row is None:
        return None
    return PromptTemplate(**dict(row))


def list_prompt_templates(conn: sqlite3.Connection) -> list[PromptTemplate]:
    rows = conn.execute(
        "SELECT * FROM prompt_templates ORDER BY name"
    ).fetchall()
    return [PromptTemplate(**dict(r)) for r in rows]


def create_prompt_template(
    conn: sqlite3.Connection,
    name: str,
    template: str,
    description: str | None = None,
) -> PromptTemplate:
    id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO prompt_templates (id, name, template, description)
           VALUES (?, ?, ?, ?)""",
        (id, name, template, description),
    )
    conn.commit()
    return get_prompt_template_by_id(conn, id)


def update_prompt_template(
    conn: sqlite3.Connection,
    name: str,
    template: str | None = None,
    description: str | None = None,
    is_active: bool | None = None,
) -> PromptTemplate | None:
    sets = []
    params = []
    if template is not None:
        sets.append("template = ?")
        params.append(template)
        sets.append("version = version + 1")
    if description is not None:
        sets.append("description = ?")
        params.append(description)
    if is_active is not None:
        sets.append("is_active = ?")
        params.append(is_active)
    if not sets:
        return get_prompt_template(conn, name)
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(name)
    conn.execute(
        f"UPDATE prompt_templates SET {', '.join(sets)} WHERE name = ?", params
    )
    conn.commit()
    return get_prompt_template(conn, name)


# --- Skill Templates ---

def get_skill_template(conn: sqlite3.Connection, name: str) -> SkillTemplate | None:
    row = conn.execute(
        "SELECT * FROM skill_templates WHERE name = ?", (name,)
    ).fetchone()
    if row is None:
        return None
    return SkillTemplate(**dict(row))


def get_default_skill_template(conn: sqlite3.Connection) -> SkillTemplate | None:
    row = conn.execute(
        "SELECT * FROM skill_templates WHERE is_default = TRUE"
    ).fetchone()
    if row is None:
        return None
    return SkillTemplate(**dict(row))


def list_skill_templates(conn: sqlite3.Connection) -> list[SkillTemplate]:
    rows = conn.execute("SELECT * FROM skill_templates ORDER BY name").fetchall()
    return [SkillTemplate(**dict(r)) for r in rows]


def create_skill_template(
    conn: sqlite3.Connection,
    name: str,
    template: str,
    install_instruction: str | None = None,
    description: str | None = None,
    is_default: bool = False,
) -> SkillTemplate:
    id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO skill_templates
           (id, name, template, install_instruction, description, is_default)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (id, name, template, install_instruction, description, is_default),
    )
    conn.commit()
    return get_skill_template(conn, name)


def update_skill_template(
    conn: sqlite3.Connection,
    name: str,
    template: str | None = None,
    install_instruction: str | None = None,
    description: str | None = None,
) -> SkillTemplate | None:
    sets = []
    params = []
    if template is not None:
        sets.append("template = ?")
        params.append(template)
        sets.append("version = version + 1")
    if install_instruction is not None:
        sets.append("install_instruction = ?")
        params.append(install_instruction)
    if description is not None:
        sets.append("description = ?")
        params.append(description)
    if not sets:
        return get_skill_template(conn, name)
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(name)
    conn.execute(
        f"UPDATE skill_templates SET {', '.join(sets)} WHERE name = ?", params
    )
    conn.commit()
    return get_skill_template(conn, name)
