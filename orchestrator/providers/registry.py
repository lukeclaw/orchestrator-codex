"""Provider capability registry.

This is the backend source of truth for provider identity, display names,
and coarse capability flags used by settings/UI gating.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

DEFAULT_PROVIDER_ID: Final[str] = "claude"

CAPABILITY_WORKER_SESSIONS = "worker_sessions"
CAPABILITY_BRAIN_SESSIONS = "brain_sessions"
CAPABILITY_LOCAL_SESSIONS = "local_sessions"
CAPABILITY_REMOTE_SESSIONS = "remote_sessions"
CAPABILITY_MODEL_SELECTION = "model_selection"
CAPABILITY_EFFORT_SELECTION = "effort_selection"
CAPABILITY_SKIP_PERMISSIONS = "skip_permissions"
CAPABILITY_HOOKS = "hooks"
CAPABILITY_SKILLS_DEPLOYMENT = "skills_deployment"
CAPABILITY_HEARTBEAT_LOOP = "heartbeat_loop"
CAPABILITY_QUICK_CLEAR = "quick_clear"
CAPABILITY_RECONNECT = "reconnect"

CAPABILITY_KEYS: tuple[str, ...] = (
    CAPABILITY_WORKER_SESSIONS,
    CAPABILITY_BRAIN_SESSIONS,
    CAPABILITY_LOCAL_SESSIONS,
    CAPABILITY_REMOTE_SESSIONS,
    CAPABILITY_MODEL_SELECTION,
    CAPABILITY_EFFORT_SELECTION,
    CAPABILITY_SKIP_PERMISSIONS,
    CAPABILITY_HOOKS,
    CAPABILITY_SKILLS_DEPLOYMENT,
    CAPABILITY_HEARTBEAT_LOOP,
    CAPABILITY_QUICK_CLEAR,
    CAPABILITY_RECONNECT,
)


@dataclass(frozen=True)
class ProviderCapability:
    """A coarse capability flag with optional disabled tooltip text."""

    supported: bool
    disabled_reason: str | None = None

    def __post_init__(self):
        if self.supported and self.disabled_reason is not None:
            raise ValueError("supported capabilities must not define a disabled reason")
        if not self.supported and not self.disabled_reason:
            raise ValueError("unsupported capabilities must include a disabled reason")

    def as_dict(self) -> dict[str, object]:
        return {
            "supported": self.supported,
            "disabled_reason": self.disabled_reason,
        }


@dataclass(frozen=True)
class ProviderDefinition:
    """Provider metadata for the backend and UI."""

    id: str
    label: str
    capabilities: dict[str, ProviderCapability]

    def __post_init__(self):
        missing = [key for key in CAPABILITY_KEYS if key not in self.capabilities]
        if missing:
            raise ValueError(f"provider {self.id!r} missing capabilities: {missing}")

    def as_dict(self) -> dict[str, object]:
        capabilities = {
            key: capability.as_dict() for key, capability in self.capabilities.items()
        }
        return {
            "id": self.id,
            "label": self.label,
            "capabilities": capabilities,
        }


def _supported() -> ProviderCapability:
    return ProviderCapability(supported=True)


def _unsupported(reason: str) -> ProviderCapability:
    return ProviderCapability(supported=False, disabled_reason=reason)


PROVIDERS: dict[str, ProviderDefinition] = {
    "claude": ProviderDefinition(
        id="claude",
        label="Claude",
        capabilities={
            CAPABILITY_WORKER_SESSIONS: _supported(),
            CAPABILITY_BRAIN_SESSIONS: _supported(),
            CAPABILITY_LOCAL_SESSIONS: _supported(),
            CAPABILITY_REMOTE_SESSIONS: _supported(),
            CAPABILITY_MODEL_SELECTION: _supported(),
            CAPABILITY_EFFORT_SELECTION: _supported(),
            CAPABILITY_SKIP_PERMISSIONS: _supported(),
            CAPABILITY_HOOKS: _supported(),
            CAPABILITY_SKILLS_DEPLOYMENT: _supported(),
            CAPABILITY_HEARTBEAT_LOOP: _supported(),
            CAPABILITY_QUICK_CLEAR: _supported(),
            CAPABILITY_RECONNECT: _supported(),
        },
    ),
    "codex": ProviderDefinition(
        id="codex",
        label="Codex",
        capabilities={
            CAPABILITY_WORKER_SESSIONS: _supported(),
            CAPABILITY_BRAIN_SESSIONS: _supported(),
            CAPABILITY_LOCAL_SESSIONS: _supported(),
            CAPABILITY_REMOTE_SESSIONS: _unsupported(
                "Remote Codex support is not available in MVP."
            ),
            CAPABILITY_MODEL_SELECTION: _supported(),
            CAPABILITY_EFFORT_SELECTION: _supported(),
            CAPABILITY_SKIP_PERMISSIONS: _unsupported(
                "Codex skip-permissions support is not implemented yet."
            ),
            CAPABILITY_HOOKS: _unsupported("Codex hook automation is not implemented yet."),
            CAPABILITY_SKILLS_DEPLOYMENT: _unsupported(
                "Codex skills deployment is not implemented yet."
            ),
            CAPABILITY_HEARTBEAT_LOOP: _unsupported(
                "Codex heartbeat loop support is not implemented yet."
            ),
            CAPABILITY_QUICK_CLEAR: _unsupported(
                "Codex quick-clear support is not implemented yet."
            ),
            CAPABILITY_RECONNECT: _unsupported("Codex reconnect support is not implemented yet."),
        },
    ),
}


def get_provider(provider_id: str) -> ProviderDefinition:
    """Return the provider definition for *provider_id*."""
    try:
        return PROVIDERS[provider_id]
    except KeyError as exc:
        raise KeyError(f"Unknown provider: {provider_id}") from exc


def list_providers() -> list[ProviderDefinition]:
    """Return providers in canonical display order."""
    return [PROVIDERS[provider_id] for provider_id in ("claude", "codex")]
