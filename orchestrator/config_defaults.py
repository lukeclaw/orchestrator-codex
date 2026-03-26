"""Centralized setting defaults — single source of truth.

DB only stores user overrides. GET /api/settings merges these defaults
for any key not yet in the database.
"""

from orchestrator.providers import DEFAULT_PROVIDER_ID

SETTING_DEFAULTS: dict[str, object] = {
    "worker.default_provider": DEFAULT_PROVIDER_ID,
    "brain.default_provider": DEFAULT_PROVIDER_ID,
    "claude.update_before_start": False,
    "claude.skip_permissions": False,
    "codex.default_model": "gpt-5-codex",
    "codex.default_effort": "high",
    "ui.preserve_filters": False,
    "ui.theme": "dark",
    "brain.heartbeat": "off",
    "claude.default_model": "opus",
    "claude.default_effort": "high",
}
