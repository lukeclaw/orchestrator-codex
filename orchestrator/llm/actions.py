"""Execute parsed actions from the LLM brain."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass

from orchestrator.state.repositories import (
    config as config_repo,
    decisions,
    sessions,
    tasks,
    activities,
)

logger = logging.getLogger(__name__)


@dataclass
class Action:
    type: str
    params: dict
    requires_approval: bool = False


def parse_actions(text: str) -> list[Action]:
    """Parse structured actions from LLM response text.

    Expects actions in JSON format within ```action blocks.
    """
    actions = []
    import re

    # Look for ```action ... ``` blocks
    pattern = re.compile(r"```action\s*\n(.*?)```", re.DOTALL)
    for match in pattern.finditer(text):
        try:
            data = json.loads(match.group(1).strip())
            if isinstance(data, list):
                for item in data:
                    actions.append(Action(
                        type=item.get("type", "unknown"),
                        params=item.get("params", {}),
                    ))
            elif isinstance(data, dict):
                actions.append(Action(
                    type=data.get("type", "unknown"),
                    params=data.get("params", {}),
                ))
        except json.JSONDecodeError:
            logger.warning("Failed to parse action block: %s", match.group(1)[:100])

    return actions


def check_approval(conn: sqlite3.Connection, action: Action) -> bool:
    """Check if an action requires user approval based on config."""
    key = f"approval_policy.{action.type}"
    needs_approval = config_repo.get_config_value(conn, key, True)
    action.requires_approval = bool(needs_approval)
    return action.requires_approval


def execute_action(
    conn: sqlite3.Connection,
    action: Action,
    tmux_session: str = "orchestrator",
) -> dict:
    """Execute an approved action. Returns result dict."""
    executor = EXECUTORS.get(action.type)
    if executor is None:
        return {"ok": False, "error": f"Unknown action type: {action.type}"}

    try:
        return executor(conn, action.params, tmux_session)
    except Exception as e:
        logger.exception("Action execution failed: %s", action.type)
        return {"ok": False, "error": str(e)}


# --- Action executors ---

def _send_message(conn: sqlite3.Connection, params: dict, tmux_session: str) -> dict:
    from orchestrator.terminal.session import send_to_session
    session_name = params.get("session")
    message = params.get("message", "")
    if not session_name or not message:
        return {"ok": False, "error": "Missing session or message"}

    success = send_to_session(session_name, message, tmux_session)
    if success:
        activities.log_activity(
            conn, "message_sent",
            session_id=sessions.get_session_by_name(conn, session_name).id
                if sessions.get_session_by_name(conn, session_name) else None,
            event_data=json.dumps({"message": message[:200]}),
            actor="orchestrator",
        )
    return {"ok": success}


def _assign_task(conn: sqlite3.Connection, params: dict, tmux_session: str) -> dict:
    task_id = params.get("task_id")
    session_name = params.get("session")
    if not task_id or not session_name:
        return {"ok": False, "error": "Missing task_id or session"}

    session = sessions.get_session_by_name(conn, session_name)
    if not session:
        return {"ok": False, "error": f"Session not found: {session_name}"}

    tasks.update_task(conn, task_id, assigned_session_id=session.id, status="in_progress")
    sessions.update_session(conn, session.id, status="working")

    activities.log_activity(
        conn, "task_assigned",
        task_id=task_id, session_id=session.id,
        actor="orchestrator",
    )
    return {"ok": True}


def _create_task(conn: sqlite3.Connection, params: dict, tmux_session: str) -> dict:
    project_id = params.get("project_id")
    title = params.get("title")
    if not project_id or not title:
        return {"ok": False, "error": "Missing project_id or title"}

    task = tasks.create_task(
        conn, project_id, title,
        description=params.get("description"),
        priority=params.get("priority", 0),
    )
    return {"ok": True, "task_id": task.id}


def _update_task(conn: sqlite3.Connection, params: dict, tmux_session: str) -> dict:
    task_id = params.get("task_id")
    status = params.get("status")
    if not task_id:
        return {"ok": False, "error": "Missing task_id"}

    tasks.update_task(conn, task_id, status=status)
    return {"ok": True}


def _respond_decision(conn: sqlite3.Connection, params: dict, tmux_session: str) -> dict:
    decision_id = params.get("decision_id")
    response = params.get("response")
    if not decision_id or not response:
        return {"ok": False, "error": "Missing decision_id or response"}

    decisions.respond_decision(conn, decision_id, response, resolved_by="orchestrator")
    return {"ok": True}


def _alert_user(conn: sqlite3.Connection, params: dict, tmux_session: str) -> dict:
    message = params.get("message", "")
    urgency = params.get("urgency", "normal")
    activities.log_activity(
        conn, "user_alert",
        event_data=json.dumps({"message": message, "urgency": urgency}),
        actor="orchestrator",
    )
    # In a real implementation, this would push to WebSocket
    logger.warning("USER ALERT [%s]: %s", urgency, message)
    return {"ok": True}


def _rebrief_session(conn: sqlite3.Connection, params: dict, tmux_session: str) -> dict:
    session_name = params.get("session")
    context = params.get("context", "")
    if not session_name:
        return {"ok": False, "error": "Missing session"}

    from orchestrator.terminal.session import send_to_session
    success = send_to_session(session_name, context, tmux_session)
    return {"ok": success}


EXECUTORS = {
    "send_message": _send_message,
    "assign_task": _assign_task,
    "create_task": _create_task,
    "update_task": _update_task,
    "respond_decision": _respond_decision,
    "alert_user": _alert_user,
    "rebrief_session": _rebrief_session,
}
