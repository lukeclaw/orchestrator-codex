"""Data models for all entities. Plain dataclasses mapping to DB tables."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Project:
    id: str
    name: str
    description: str | None = None
    status: str = "active"
    target_date: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Session:
    id: str
    name: str
    host: str
    mp_path: str | None = None
    tmux_window: str | None = None
    tunnel_pane: str | None = None
    status: str = "idle"
    takeover_mode: bool = False
    current_task_id: str | None = None
    created_at: str = ""
    last_activity: str | None = None

    def __post_init__(self):
        self.takeover_mode = bool(self.takeover_mode)


@dataclass
class Task:
    id: str
    project_id: str
    title: str
    description: str | None = None
    status: str = "todo"
    priority: int = 0
    assigned_session_id: str | None = None
    blocked_by_decision_id: str | None = None
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    parent_task_id: str | None = None
    notes: str | None = None
    links: str | None = None  # JSON array of {url, title, type}

    @property
    def links_list(self) -> list[dict]:
        """Parse links JSON into list of dicts."""
        if self.links is None:
            return []
        if isinstance(self.links, list):
            return self.links
        try:
            return json.loads(self.links)
        except (json.JSONDecodeError, TypeError):
            return []


@dataclass
class TaskDependency:
    task_id: str
    depends_on_task_id: str


@dataclass
class Decision:
    id: str
    question: str
    project_id: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    options: list[str] | None = None
    context: str | None = None
    urgency: str = "normal"
    status: str = "pending"
    response: str | None = None
    created_at: str = ""
    resolved_at: str | None = None
    resolved_by: str | None = None

    @property
    def options_list(self) -> list[str]:
        if self.options is None:
            return []
        if isinstance(self.options, list):
            return self.options
        try:
            return json.loads(self.options)
        except (json.JSONDecodeError, TypeError):
            return []


@dataclass
class PullRequest:
    id: str
    url: str
    task_id: str | None = None
    session_id: str | None = None
    number: int | None = None
    title: str | None = None
    status: str = "open"
    created_at: str = ""
    merged_at: str | None = None


@dataclass
class Activity:
    id: str
    event_type: str
    project_id: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    event_data: str | None = None
    actor: str | None = None
    created_at: str = ""


@dataclass
class WorkerCapability:
    session_id: str
    capability_type: str
    capability_value: str


@dataclass
class TaskRequirement:
    task_id: str
    requirement_type: str
    requirement_value: str



@dataclass
class Config:
    key: str
    value: str
    description: str | None = None
    category: str | None = None
    updated_at: str = ""

    @property
    def parsed_value(self):
        """Parse JSON-encoded value."""
        try:
            return json.loads(self.value)
        except (json.JSONDecodeError, TypeError):
            return self.value


@dataclass
class PromptTemplate:
    id: str
    name: str
    template: str
    description: str | None = None
    version: int = 1
    is_active: bool = True
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        self.is_active = bool(self.is_active)


@dataclass
class SkillTemplate:
    id: str
    name: str
    template: str
    version: int = 1
    install_instruction: str | None = None
    description: str | None = None
    is_default: bool = False
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        self.is_default = bool(self.is_default)


@dataclass
class SessionSnapshot:
    id: str
    session_id: str
    task_summary: str | None = None
    key_decisions: str | None = None
    file_paths: str | None = None
    last_known_state: str | None = None
    created_at: str = ""


@dataclass
class CommEvent:
    id: str
    session_id: str
    channel: str
    event_type: str
    details: str | None = None
    created_at: str = ""


@dataclass
class DecisionHistory:
    id: str
    decision_id: str
    project_id: str | None = None
    question: str | None = None
    context: str | None = None
    decision: str | None = None
    user_feedback: str | None = None
    was_helpful: bool | None = None
    created_at: str = ""


@dataclass
class LearnedPattern:
    id: str
    pattern_type: str | None = None
    pattern_key: str | None = None
    pattern_value: str | None = None
    confidence: float | None = None
    usage_count: int = 0
    last_used_at: str | None = None
    created_at: str = ""


@dataclass
class PrDependency:
    pr_id: str
    depends_on_pr_id: str


@dataclass
class ContextItem:
    id: str
    scope: str = "global"
    project_id: str | None = None
    title: str = ""
    content: str = ""
    category: str | None = None
    source: str | None = None
    metadata: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ProjectWorker:
    project_id: str
    session_id: str
    assigned_at: str = ""
