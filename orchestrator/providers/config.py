"""Provider-specific settings helpers."""

from __future__ import annotations

from orchestrator.state.repositories.config import get_config_value

MODEL_SETTING_KEY_BY_PROVIDER = {
    "claude": "claude.default_model",
    "codex": "codex.default_model",
}

EFFORT_SETTING_KEY_BY_PROVIDER = {
    "claude": "claude.default_effort",
    "codex": "codex.default_effort",
}

MODEL_DEFAULT_BY_PROVIDER = {
    "claude": "opus",
    "codex": "gpt-5-codex",
}

EFFORT_DEFAULT_BY_PROVIDER = {
    "claude": "high",
    "codex": "high",
}


def get_provider_model_setting_key(provider: str | None) -> str:
    return MODEL_SETTING_KEY_BY_PROVIDER.get(provider or "claude", "claude.default_model")


def get_provider_effort_setting_key(provider: str | None) -> str:
    return EFFORT_SETTING_KEY_BY_PROVIDER.get(provider or "claude", "claude.default_effort")


def get_provider_default_model(conn, provider: str | None) -> str:
    provider_id = provider or "claude"
    key = get_provider_model_setting_key(provider_id)
    default = MODEL_DEFAULT_BY_PROVIDER.get(provider_id, MODEL_DEFAULT_BY_PROVIDER["claude"])
    return str(get_config_value(conn, key, default=default))


def get_provider_default_effort(conn, provider: str | None) -> str:
    provider_id = provider or "claude"
    key = get_provider_effort_setting_key(provider_id)
    default = EFFORT_DEFAULT_BY_PROVIDER.get(provider_id, EFFORT_DEFAULT_BY_PROVIDER["claude"])
    return str(get_config_value(conn, key, default=default))
