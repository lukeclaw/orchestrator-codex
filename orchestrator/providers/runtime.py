"""Provider runtime dispatch.

This module isolates provider-specific launch behavior behind a small runtime
interface so the API routes can stay provider-aware without owning provider
implementation details.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Protocol

from orchestrator.providers.registry import DEFAULT_PROVIDER_ID


@dataclass(frozen=True)
class WorkerLaunchRequest:
    """Provider-neutral worker launch request."""

    conn: sqlite3.Connection
    session_id: str
    name: str
    host: str
    tmux_session: str = "orchestrator"
    api_port: int = 8093
    work_dir: str | None = None
    tmp_dir: str | None = None
    tunnel_manager: Any = None
    custom_skills: list[dict] | None = None
    disabled_builtin_names: set[str] | None = None
    update_before_start: bool = False
    skip_permissions: bool = False
    model: str = "opus"
    effort: str = "high"


class ProviderRuntime(Protocol):
    """Minimal provider runtime contract for worker launch."""

    provider_id: str

    def launch_local_worker(self, request: WorkerLaunchRequest) -> dict:
        """Launch a local worker session for this provider."""

    def launch_remote_worker(self, request: WorkerLaunchRequest) -> dict:
        """Launch a remote worker session for this provider."""


def _load_runtime(provider_id: str) -> ProviderRuntime:
    # Transitional bridge: Codex sessions still route through the Claude
    # runtime until the dedicated Codex runtime lands.
    if provider_id in {"claude", "codex"}:
        from orchestrator.providers.runtimes.claude import CLAUDE_RUNTIME

        return CLAUDE_RUNTIME
    raise KeyError(f"Unknown provider runtime: {provider_id}")


def get_provider_runtime(provider_id: str | None) -> ProviderRuntime:
    """Return the runtime for *provider_id*.

    Provider validation is handled by the main provider registry. Runtime
    lookup keeps a defensive fallback to the default provider so existing
    Claude behavior remains stable while new runtimes are added incrementally.
    """

    runtime_id = provider_id or DEFAULT_PROVIDER_ID
    return _load_runtime(runtime_id)
