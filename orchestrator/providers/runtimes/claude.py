"""Claude runtime adapter."""

from __future__ import annotations

from orchestrator.terminal import session as session_runtime

from orchestrator.providers.runtime import WorkerLaunchRequest


class ClaudeRuntime:
    """Claude provider runtime."""

    provider_id = "claude"

    def launch_local_worker(self, request: WorkerLaunchRequest) -> dict:
        return session_runtime.setup_local_worker(
            request.conn,
            request.session_id,
            request.name,
            tmux_session=request.tmux_session,
            api_port=request.api_port,
            work_dir=request.work_dir,
            tmp_dir=request.tmp_dir,
            custom_skills=request.custom_skills,
            disabled_builtin_names=request.disabled_builtin_names,
            update_before_start=request.update_before_start,
            skip_permissions=request.skip_permissions,
            model=request.model,
            effort=request.effort,
        )

    def launch_remote_worker(self, request: WorkerLaunchRequest) -> dict:
        return session_runtime.setup_remote_worker(
            request.conn,
            request.session_id,
            request.name,
            request.host,
            tmux_session=request.tmux_session,
            api_port=request.api_port,
            work_dir=request.work_dir,
            tmp_dir=request.tmp_dir,
            tunnel_manager=request.tunnel_manager,
            custom_skills=request.custom_skills,
            disabled_builtin_names=request.disabled_builtin_names,
            update_before_start=request.update_before_start,
            skip_permissions=request.skip_permissions,
            model=request.model,
            effort=request.effort,
        )


CLAUDE_RUNTIME = ClaudeRuntime()
