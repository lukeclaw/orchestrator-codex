"""Data models for all entities. Plain dataclasses mapping to DB tables."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime


def generate_task_prefix(name: str) -> str:
    """Generate a 3-letter uppercase prefix from project name.
    
    Examples:
        "Unit Test Improve" -> "UTI"
        "API Gateway" -> "AG"
        "my-awesome-project" -> "MAP"
    """
    # Split by spaces, hyphens, underscores
    import re
    words = re.split(r'[\s\-_]+', name.strip())
    words = [w for w in words if w]  # Remove empty strings
    
    if not words:
        return "TSK"
    
    if len(words) >= 3:
        # Take first letter of first 3 words
        prefix = ''.join(w[0] for w in words[:3])
    elif len(words) == 2:
        # Take first letter of each word
        prefix = ''.join(w[0] for w in words)
    else:
        # Single word: take first 3 letters
        prefix = words[0][:3]
    
    return prefix.upper()


@dataclass
class Project:
    id: str
    name: str
    description: str | None = None
    status: str = "active"
    target_date: str | None = None
    task_prefix: str | None = None  # e.g., "UTI" for human-readable task keys
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Session:
    id: str
    name: str
    host: str
    work_dir: str | None = None
    tunnel_pid: int | None = None
    status: str = "idle"
    takeover_mode: bool = False
    created_at: str = ""
    last_status_changed_at: str | None = None
    session_type: str = "worker"  # "worker" | "brain" | "system"
    last_viewed_at: str | None = None

    def __post_init__(self):
        self.takeover_mode = bool(self.takeover_mode)


@dataclass
class Task:
    id: str
    project_id: str
    title: str
    description: str | None = None
    status: str = "todo"
    priority: str = "M"  # H (High), M (Medium), L (Low)
    assigned_session_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    parent_task_id: str | None = None
    notes: str | None = None
    links: str | None = None  # JSON array of {url, title, type}
    task_index: int | None = None  # Sequential number within project for human-readable key

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
class ContextItem:
    id: str
    scope: str = "global"
    project_id: str | None = None
    title: str = ""
    description: str | None = None
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


@dataclass
class Notification:
    id: str
    message: str
    task_id: str | None = None
    session_id: str | None = None
    notification_type: str = "info"  # info, pr_comment, warning
    link_url: str | None = None
    created_at: str = ""
    dismissed: bool = False
    dismissed_at: str | None = None

    def __post_init__(self):
        self.dismissed = bool(self.dismissed)
