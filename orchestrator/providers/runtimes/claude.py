"""Claude runtime adapter."""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import time

from orchestrator.agents import get_path_export_command
from orchestrator.agents.deploy import deploy_brain_tmp_contents
from orchestrator.state.repositories.config import get_config_value
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.terminal import manager as tmux
from orchestrator.terminal.claude_update import run_claude_update, should_update_before_start
from orchestrator.terminal import session as session_runtime
from orchestrator.terminal.session import send_to_session

from orchestrator.providers.runtime import WorkerLaunchRequest

logger = logging.getLogger(__name__)

BRAIN_SESSION_NAME = "brain"
_BRAIN_DIR = "/tmp/orchestrator/brain"


def _get_brain_session(conn):
    return sessions_repo.get_session_by_name(conn, BRAIN_SESSION_NAME)


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

    def start_brain(self, conn) -> dict:
        session = _get_brain_session(conn)

        shells = {"bash", "zsh", "fish", "sh", "dash"}
        pane_cmd = tmux.pane_foreground_command(tmux.TMUX_SESSION, BRAIN_SESSION_NAME)
        claude_already_running = pane_cmd is not None and pane_cmd not in shells

        brain_model = str(get_config_value(conn, "claude.default_model", default="opus"))
        brain_effort = str(get_config_value(conn, "claude.default_effort", default="high"))
        deploy_brain_tmp_contents(_BRAIN_DIR, conn=conn, model=brain_model, effort=brain_effort)
        logger.info("Deployed brain tmp contents via provider runtime")

        bin_dir = os.path.join(_BRAIN_DIR, "bin")
        path_export = get_path_export_command(bin_dir)
        settings_path = os.path.join(_BRAIN_DIR, ".claude", "settings.json")

        target = tmux.ensure_window(tmux.TMUX_SESSION, BRAIN_SESSION_NAME)

        if session:
            sessions_repo.update_session(conn, session.id, status="working")
            session_id = session.id
        else:
            session = sessions_repo.create_session(
                conn,
                name=BRAIN_SESSION_NAME,
                host="local",
                work_dir=_BRAIN_DIR,
                session_type="brain",
                provider=self.provider_id,
            )
            session_id = session.id
            sessions_repo.update_session(conn, session_id, status="working")

        if claude_already_running:
            logger.info("Brain pane already running '%s'; skipping launch", pane_cmd)
            return {
                "ok": True,
                "session_id": session_id,
                "status": "working",
                "message": "Brain already running (reconnected)",
            }

        tmux.send_keys(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, f"cd {shlex.quote(_BRAIN_DIR)}")
        tmux.send_keys(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, path_export)

        if should_update_before_start(conn):
            time.sleep(0.3)
            run_claude_update(
                tmux.send_keys,
                tmux.capture_output,
                tmux.TMUX_SESSION,
                BRAIN_SESSION_NAME,
            )

        time.sleep(0.5)
        cmd = f"claude --settings {settings_path}"
        if get_config_value(conn, "claude.skip_permissions", default=False):
            cmd = f"claude --dangerously-skip-permissions --settings {settings_path}"
        tmux.send_keys(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, cmd)
        tmux.dismiss_trust_prompt(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, session_id=session_id)

        heartbeat_interval = get_config_value(conn, "brain.heartbeat", default="off")
        if heartbeat_interval and heartbeat_interval != "off":
            time.sleep(2)
            send_to_session(
                BRAIN_SESSION_NAME,
                f"/loop {heartbeat_interval} /heartbeat",
                tmux.TMUX_SESSION,
            )
            logger.info("Brain heartbeat enabled: /loop %s /heartbeat", heartbeat_interval)

        logger.info("Orchestrator brain started in %s", target)
        return {
            "ok": True,
            "session_id": session_id,
            "status": "working",
            "message": "Brain started",
        }

    def stop_brain(self, conn) -> dict:
        session = _get_brain_session(conn)
        if session is None:
            return {"ok": True, "message": "Brain not running"}

        try:
            for _ in range(3):
                tmux.send_keys(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, "C-c", enter=False)
                time.sleep(0.3)
            sessions_repo.update_session(conn, session.id, status="disconnected")
            logger.info("Orchestrator brain stopped")
        except Exception:
            logger.exception("Failed to stop brain")
            sessions_repo.update_session(conn, session.id, status="disconnected")

        try:
            if os.path.exists(_BRAIN_DIR):
                shutil.rmtree(_BRAIN_DIR)
                logger.info("Cleaned up brain directory: %s", _BRAIN_DIR)
        except Exception as exc:
            logger.warning("Could not clean up brain directory %s: %s", _BRAIN_DIR, exc)

        return {"ok": True, "message": "Brain stopped"}

    def redeploy_brain(self, conn) -> dict:
        brain = _get_brain_session(conn)
        if brain is None or brain.status in ("disconnected",):
            raise ValueError("Brain is not running")

        brain_model = str(get_config_value(conn, "claude.default_model", default="opus"))
        brain_effort = str(get_config_value(conn, "claude.default_effort", default="high"))
        deploy_brain_tmp_contents(_BRAIN_DIR, conn=conn, model=brain_model, effort=brain_effort)
        logger.info("Brain files re-deployed (provider runtime)")

        heartbeat_interval = get_config_value(conn, "brain.heartbeat", default="off")
        loop_sent = False
        if heartbeat_interval and heartbeat_interval != "off":
            time.sleep(1)
            loop_sent = send_to_session(
                BRAIN_SESSION_NAME,
                f"/loop {heartbeat_interval} /heartbeat",
                tmux.TMUX_SESSION,
            )
            if loop_sent:
                logger.info("Brain heartbeat re-armed: /loop %s /heartbeat", heartbeat_interval)

        return {"ok": True, "redeployed": True, "heartbeat_rearmed": loop_sent}


CLAUDE_RUNTIME = ClaudeRuntime()
