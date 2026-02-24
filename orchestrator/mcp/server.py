"""MCP server that gives the orchestrator brain access to project management tools.

Run as: python -m orchestrator.mcp.server
Claude Code launches this automatically via .mcp.json.
"""

from __future__ import annotations

import json
import os
import sqlite3

from mcp.server.fastmcp import FastMCP

# Resolve DB path — either from env or via centralized paths module
_DB_PATH = os.environ.get("ORCHESTRATOR_DB")
if not _DB_PATH:
    from orchestrator import paths
    _DB_PATH = str(paths.db_path())

mcp = FastMCP("orchestrator", instructions=(
    "Project management tools for the Orchestrator. "
    "Use these to manage projects, tasks, worker sessions, and monitor activity."
))


def _get_conn() -> sqlite3.Connection:
    """Open a read/write connection to the orchestrator database."""
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@mcp.tool()
def list_projects(status: str | None = None) -> str:
    """List all projects, optionally filtered by status (active/completed/paused).
    Returns project details including task counts."""
    conn = _get_conn()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM projects WHERE status = ? ORDER BY name", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()

        projects = []
        for r in rows:
            p = dict(r)
            # Add task counts
            counts = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM tasks WHERE project_id = ? GROUP BY status",
                (p["id"],),
            ).fetchall()
            p["task_counts"] = {c["status"]: c["cnt"] for c in counts}
            projects.append(p)
        return json.dumps(projects, indent=2, default=str)
    finally:
        conn.close()


@mcp.tool()
def create_project(name: str, description: str | None = None) -> str:
    """Create a new project. Returns the created project."""
    import uuid

    conn = _get_conn()
    try:
        project_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO projects (id, name, description) VALUES (?, ?, ?)",
            (project_id, name, description),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return json.dumps(dict(row), indent=2, default=str)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@mcp.tool()
def list_tasks(
    project_id: str | None = None,
    status: str | None = None,
    assigned_session_id: str | None = None,
) -> str:
    """List tasks with optional filters. Returns task details including session names."""
    conn = _get_conn()
    try:
        clauses, params = [], []
        if project_id:
            clauses.append("t.project_id = ?")
            params.append(project_id)
        if status:
            clauses.append("t.status = ?")
            params.append(status)
        if assigned_session_id:
            clauses.append("t.assigned_session_id = ?")
            params.append(assigned_session_id)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"""SELECT t.*, p.name as project_name, s.name as session_name
                FROM tasks t
                LEFT JOIN projects p ON t.project_id = p.id
                LEFT JOIN sessions s ON t.assigned_session_id = s.id
                {where}
                ORDER BY t.priority DESC, t.created_at""",
            params,
        ).fetchall()
        return json.dumps(_rows_to_dicts(rows), indent=2, default=str)
    finally:
        conn.close()


@mcp.tool()
def create_task(
    project_id: str,
    title: str,
    description: str | None = None,
    priority: int = 0,
) -> str:
    """Create a new task in a project. Returns the created task."""
    import uuid

    conn = _get_conn()
    try:
        task_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO tasks (id, project_id, title, description, priority) VALUES (?, ?, ?, ?, ?)",
            (task_id, project_id, title, description, priority),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return json.dumps(dict(row), indent=2, default=str)
    finally:
        conn.close()


@mcp.tool()
def update_task(
    task_id: str,
    status: str | None = None,
    assigned_session_id: str | None = None,
    title: str | None = None,
    description: str | None = None,
    priority: int | None = None,
) -> str:
    """Update a task's status, assignment, or other fields.
    Valid statuses: todo, in_progress, done, blocked.
    Set assigned_session_id to a session ID to assign, or 'none' to unassign."""
    conn = _get_conn()
    try:
        sets, params = [], []
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if assigned_session_id is not None:
            sets.append("assigned_session_id = ?")
            val = None if assigned_session_id.lower() == "none" else assigned_session_id
            params.append(val)
        if title is not None:
            sets.append("title = ?")
            params.append(title)
        if description is not None:
            sets.append("description = ?")
            params.append(description)
        if priority is not None:
            sets.append("priority = ?")
            params.append(priority)

        if not sets:
            return json.dumps({"error": "No fields to update"})

        # Always update updated_at timestamp
        sets.append("updated_at = CURRENT_TIMESTAMP")
        params.append(task_id)
        conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
        # Use explicit column list to avoid deprecated columns
        task_cols = "id, project_id, title, description, status, priority, assigned_session_id, created_at, updated_at, parent_task_id, notes, links, task_index"
        row = conn.execute(f"SELECT {task_cols} FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return json.dumps({"error": "Task not found"})
        return json.dumps(dict(row), indent=2, default=str)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Sessions (workers)
# ---------------------------------------------------------------------------

@mcp.tool()
def list_sessions(status: str | None = None) -> str:
    """List all worker sessions with their current status and assigned tasks.
    Statuses: idle, working, waiting, error, disconnected."""
    conn = _get_conn()
    try:
        if status:
            rows = conn.execute(
                """SELECT s.*, t.title as current_task_title
                   FROM sessions s
                   LEFT JOIN tasks t ON t.assigned_session_id = s.id
                   WHERE s.status = ? ORDER BY s.name""",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT s.*, t.title as current_task_title
                   FROM sessions s
                   LEFT JOIN tasks t ON t.assigned_session_id = s.id
                   ORDER BY s.name""",
            ).fetchall()
        return json.dumps(_rows_to_dicts(rows), indent=2, default=str)
    finally:
        conn.close()


@mcp.tool()
def create_session(name: str, host: str = "localhost", working_directory: str | None = None) -> str:
    """Create a new worker session with a tmux window.
    The session will be idle until Claude Code is started in it."""
    import subprocess
    import uuid

    conn = _get_conn()
    try:
        # Ensure tmux session exists
        tmux_session = "orchestrator"
        subprocess.run(
            ["tmux", "has-session", "-t", tmux_session],
            capture_output=True, check=False,
        )
        # Create window
        result = subprocess.run(
            ["tmux", "new-window", "-t", tmux_session, "-n", name],
            capture_output=True, text=True, check=False,
        )
        target = f"{tmux_session}:{name}"

        if working_directory:
            subprocess.run(
                ["tmux", "send-keys", "-t", target, f"cd {working_directory}", "Enter"],
                capture_output=True, check=False,
            )

        session_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO sessions (id, name, host, work_dir) VALUES (?, ?, ?, ?)",
            (session_id, name, host, working_directory),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return json.dumps(dict(row), indent=2, default=str)
    finally:
        conn.close()


@mcp.tool()
def send_message_to_worker(session_name: str, message: str) -> str:
    """Send a text message to a worker's Claude Code instance.
    The message is typed into their terminal as if the user typed it."""
    import subprocess

    tmux_session = "orchestrator"
    target = f"{tmux_session}:{session_name}"
    result = subprocess.run(
        ["tmux", "send-keys", "-t", target, message, "Enter"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        return json.dumps({"ok": True, "session": session_name, "message_sent": message[:200]})
    return json.dumps({"ok": False, "error": result.stderr.strip()})


@mcp.tool()
def get_worker_output(session_name: str, lines: int = 50) -> str:
    """Get recent terminal output from a worker session.
    Useful for checking what a worker is currently doing."""
    import subprocess

    tmux_session = "orchestrator"
    target = f"{tmux_session}:{session_name}"
    result = subprocess.run(
        ["tmux", "capture-pane", "-p", "-t", target, "-S", f"-{lines}"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        return result.stdout
    return json.dumps({"error": f"Could not capture output: {result.stderr.strip()}"})


@mcp.tool()
def start_claude_in_session(session_name: str) -> str:
    """Launch Claude Code in a worker session's terminal.
    The session must already exist (use create_session first)."""
    import subprocess

    tmux_session = "orchestrator"
    target = f"{tmux_session}:{session_name}"
    result = subprocess.run(
        ["tmux", "send-keys", "-t", target, "claude", "Enter"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        # Update status in DB
        conn = _get_conn()
        try:
            conn.execute(
                "UPDATE sessions SET status = 'working' WHERE name = ?",
                (session_name,),
            )
            conn.commit()
        finally:
            conn.close()
        return json.dumps({"ok": True, "session": session_name, "action": "claude started"})
    return json.dumps({"ok": False, "error": result.stderr.strip()})


# ---------------------------------------------------------------------------
# Orchestrator status (overview)
# ---------------------------------------------------------------------------

@mcp.tool()
def get_orchestrator_status() -> str:
    """Get a high-level overview of the orchestrator state.
    Shows counts of projects, tasks by status, and sessions by status."""
    conn = _get_conn()
    try:
        projects = conn.execute("SELECT COUNT(*) as cnt FROM projects").fetchone()["cnt"]
        tasks_by_status = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"
        ).fetchall()
        sessions_by_status = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM sessions GROUP BY status"
        ).fetchall()

        return json.dumps({
            "projects": projects,
            "tasks": {r["status"]: r["cnt"] for r in tasks_by_status},
            "sessions": {r["status"]: r["cnt"] for r in sessions_by_status},
        }, indent=2)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
