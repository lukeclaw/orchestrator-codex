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
    cdp_port: int = 9222
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

    def start_brain(self, conn: sqlite3.Connection) -> dict:
        """Start the provider's brain session."""

    def stop_brain(self, conn: sqlite3.Connection) -> dict:
        """Stop the provider's brain session."""

    def redeploy_brain(self, conn: sqlite3.Connection) -> dict:
        """Redeploy provider-managed brain assets."""

    def get_launch_command(
        self,
        session_id: str,
        tmp_dir: str,
        model: str | None = None,
        effort: str | None = None,
        skip_permissions: bool = False,
    ) -> str:
        """Return the shell command to launch the provider's CLI."""

    def is_alive(self, tmux_sess: str, tmux_win: str, session_id: str) -> tuple[bool, str]:
        """Check if the provider session is still alive in the given tmux pane."""

    def check_session_exists(self, host: str, session_id: str) -> bool:
        """Check if a session with the given ID exists (locally or remotely)."""


def _load_runtime(provider_id: str) -> ProviderRuntime:
    if provider_id == "claude":
        from orchestrator.providers.runtimes.claude import CLAUDE_RUNTIME

        return CLAUDE_RUNTIME
    if provider_id == "codex":
        from orchestrator.providers.runtimes.codex import CODEX_RUNTIME

        return CODEX_RUNTIME
    if provider_id == "gemini":
        from orchestrator.providers.runtimes.gemini import GEMINI_RUNTIME

        return GEMINI_RUNTIME
    raise KeyError(f"Unknown provider runtime: {provider_id}")


def get_provider_runtime(provider_id: str | None) -> ProviderRuntime:
    """Return the runtime for *provider_id*.

    Provider validation is handled by the main provider registry. Runtime
    lookup keeps a defensive fallback to the default provider so existing
    Claude behavior remains stable while new runtimes are added incrementally.
    """

    runtime_id = provider_id or DEFAULT_PROVIDER_ID
    return _load_runtime(runtime_id)
