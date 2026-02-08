"""Core reasoning engine — processes queries and state changes."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from orchestrator.llm.actions import Action, check_approval, parse_actions
from orchestrator.llm.client import LLMClient
from orchestrator.llm.context_selector import select_context
from orchestrator.llm.templates import render_template

logger = logging.getLogger(__name__)


@dataclass
class BrainResponse:
    summary: str
    actions: list[Action]
    raw_response: str = ""


class Brain:
    """LLM-powered reasoning engine for the orchestrator."""

    def __init__(self, conn: sqlite3.Connection, client: LLMClient | None = None):
        self.conn = conn
        self.client = client or LLMClient(conn=conn)

    def process_query(self, user_message: str) -> BrainResponse:
        """Process a user query and return a response with optional actions."""
        # Assemble context
        context = select_context(self.conn, query=user_message)

        # Load system prompt from DB
        system_prompt = render_template(
            self.conn, "system_prompt",
            {"system_state": context},
        )
        if system_prompt is None:
            system_prompt = f"You are the orchestrator brain.\n\n{context}"

        # Call LLM
        response = self.client.call(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            source="orchestrator_brain",
        )

        # Parse actions from response
        actions = parse_actions(response.content)

        # Check approval requirements
        for action in actions:
            check_approval(self.conn, action)

        return BrainResponse(
            summary=response.content,
            actions=actions,
            raw_response=response.content,
        )

    def process_state_change(self, event_type: str, event_data: dict) -> BrainResponse | None:
        """Process a state change event and optionally return actions.

        Returns None if no action is needed (Tier 1 handled it).
        """
        # Only invoke LLM for events that need intelligence
        significant_events = {
            "session.error", "session.compact",
            "session.state_changed",
        }
        if event_type not in significant_events:
            return None

        context = select_context(self.conn)
        system_prompt = render_template(
            self.conn, "system_prompt",
            {"system_state": context},
        )
        if system_prompt is None:
            system_prompt = f"You are the orchestrator brain.\n\n{context}"

        message = (
            f"A state change occurred: {event_type}\n"
            f"Event data: {event_data}\n\n"
            "Analyze this event and determine if any action is needed. "
            "If so, propose actions using ```action blocks."
        )

        response = self.client.call(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": message}],
            source="orchestrator_brain",
        )

        actions = parse_actions(response.content)
        if not actions:
            return None

        for action in actions:
            check_approval(self.conn, action)

        return BrainResponse(
            summary=response.content,
            actions=actions,
            raw_response=response.content,
        )
