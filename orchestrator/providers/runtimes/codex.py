"""Codex runtime adapter."""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import threading
import time
from datetime import datetime, timedelta

from orchestrator.agents import get_path_export_command
from orchestrator.agents.deploy import (
    deploy_brain_tmp_contents,
    deploy_worker_tmp_contents,
)
from orchestrator.browser.cdp_worker_proxy import start_cdp_proxy
from orchestrator.providers.config import get_provider_default_effort, get_provider_default_model
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.state.repositories.config import get_config_value
from orchestrator.terminal import manager as tmux
from orchestrator.terminal.session import send_to_session

from orchestrator.providers.runtime import WorkerLaunchRequest

logger = logging.getLogger(__name__)

BRAIN_SESSION_NAME = "brain"
_BRAIN_DIR = "/tmp/orchestrator/brain"
_DEFAULT_CODEX_MODEL = "gpt-5-codex"
_DEFAULT_REASONING_EFFORT = "high"


def _get_brain_session(db):
    return sessions_repo.get_session_by_name(db, BRAIN_SESSION_NAME)


def _build_codex_command(
    workspace_dir: str,
    prompt_path: str,
    model: str = _DEFAULT_CODEX_MODEL,
    effort: str = _DEFAULT_REASONING_EFFORT,
    add_dirs: list[str] | None = None,
) -> str:
    """Build the codex CLI launch command with all necessary flags."""
    args = [
        "run",
        "--no-alt-screen",
        "--headless",
        "--yolo",
        f"--model {shlex.quote(model)}",
        f"--thinking-level {shlex.quote(effort)}",
    ]

    # Inject system instruction from the prompt file
    if os.path.exists(prompt_path):
        args.append(f"--instructions-file {shlex.quote(prompt_path)}")

    # Add any extra directories to context (like the worker's own bin/scripts)
    if add_dirs:
        for d in add_dirs:
            args.append(f"--add-dir {shlex.quote(d)}")

    return f"codex {' '.join(args)}"


class CodexHeartbeatLoop:
    """Manages the autonomous monitoring loop for Codex-based brain."""

    def __init__(self):
        self._thread = None
        self._stop_event = threading.Event()
        self._interval = "off"

    def restart(self, interval: str) -> bool:
        """Update the loop interval and restart the thread if needed."""
        if interval == self._interval:
            return False

        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

        self._interval = interval
        if interval == "off":
            return False

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def _loop(self):
        logger.info("Codex heartbeat loop started (interval: %s)", self._interval)
        while not self._stop_event.is_set():
            # Parse interval (e.g. "30m", "1h")
            match = re.match(r"(\d+)([mh])", self._interval)
            if not match:
                logger.error("Invalid heartbeat interval format: %s", self._interval)
                break

            val, unit = match.groups()
            seconds = int(val) * (60 if unit == "m" else 3600)

            # Wait for next tick
            if self._stop_event.wait(timeout=seconds):
                break

            # Trigger check
            try:
                from orchestrator.api.routes.brain import brain_sync
                from orchestrator.api.deps import get_db_context

                with get_db_context() as db:
                    brain_sync(db)
                logger.info("Codex heartbeat: triggered worker check")
            except Exception:
                logger.exception("Codex heartbeat check failed")

        logger.info("Codex heartbeat loop stopped")


_CODEX_HEARTBEAT_LOOP = CodexHeartbeatLoop()


class CodexRuntime:
    provider_id = "codex"

    def launch_local_worker(self, request: WorkerLaunchRequest) -> dict:
        """Launch a local Codex worker: deploy files, start tmux command."""
        try:
            local_tmp_dir = request.tmp_dir
            workspace_dir = request.work_dir or local_tmp_dir
            prompt_path = os.path.join(local_tmp_dir, "prompt.md")
            cdp_port = request.cdp_port or 9222

            # 1. Deploy tmp dir
            deploy_worker_tmp_contents(
                local_tmp_dir,
                request.session_id,
                api_base=f"http://127.0.0.1:{request.api_port}",
                cdp_port=cdp_port,
                browser_headless=False,
                provider=self.provider_id,
            )

            # 2. Build and send command
            cmd_parts = []
            if request.work_dir:
                cmd_parts.append(f"cd {shlex.quote(request.work_dir)}")

            # Ensure Node 24 for npx
            cmd_parts.append("command -v volta >/dev/null 2>&1 && volta install node@24 || true")

            # CDP Proxy
            try:
                proxy_port = start_cdp_proxy(request.session_id, chrome_port=cdp_port)
            except Exception:
                logger.warning("CDP proxy failed for %s, falling back to direct", request.name)
                proxy_port = cdp_port

            cmd_parts.append(f"export PLAYWRIGHT_MCP_CDP_ENDPOINT=http://localhost:{proxy_port}")
            cmd_parts.append(get_path_export_command(os.path.join(local_tmp_dir, "bin")))

            add_dirs = [local_tmp_dir] if request.work_dir else None
            cmd_parts.append(
                _build_codex_command(
                    workspace_dir=workspace_dir,
                    prompt_path=prompt_path,
                    model=request.model,
                    effort=request.effort,
                    add_dirs=add_dirs,
                )
            )

            tmux.send_keys(request.tmux_session, request.name, " && ".join(cmd_parts), enter=True)
            logger.info(
                "Launched Codex for local worker %s (work_dir=%s)",
                request.name,
                request.work_dir,
            )
            return {"ok": True}
        except Exception as e:
            logger.exception("Failed to set up local Codex worker %s", request.name)
            return {"ok": False, "error": str(e)}

    def launch_remote_worker(self, request: WorkerLaunchRequest) -> dict:
        return {"ok": False, "error": "Remote Codex support is not available in MVP"}

    def start_brain(self, conn) -> dict:
        session = _get_brain_session(conn)

        # Use is_alive method for detection instead of simple command string
        alive, _ = self.is_alive(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, "brain")

        brain_model = str(get_config_value(conn, "codex.default_model", default=_DEFAULT_CODEX_MODEL))
        brain_effort = str(get_config_value(conn, "codex.default_effort", default=_DEFAULT_REASONING_EFFORT))

        deploy_brain_tmp_contents(
            _BRAIN_DIR,
            conn=conn,
            provider=self.provider_id,
            model=brain_model,
            effort=brain_effort,
        )
        prompt_path = os.path.join(_BRAIN_DIR, "prompt.md")
        target = tmux.ensure_window(tmux.TMUX_SESSION, BRAIN_SESSION_NAME)

        if session:
            sessions_repo.update_session(conn, session.id, status="working", provider=self.provider_id)
            session_id = session.id
        else:
            session = sessions_repo.create_session(
                conn,
                BRAIN_SESSION_NAME,
                host="localhost",
                work_dir=os.getcwd(), # Project root
                session_type="brain",
                provider=self.provider_id,
            )
            session_id = session.id
            sessions_repo.update_session(conn, session_id, status="working", provider=self.provider_id)

        if not alive:
            cmd_parts = [
                "cd /", # Break out of any dead directory
                f"cd {shlex.quote(os.getcwd())}", # Go to project root
                get_path_export_command(os.path.join(_BRAIN_DIR, "bin")),
                _build_codex_command(
                    workspace_dir=os.getcwd(),
                    prompt_path=prompt_path,
                    model=brain_model,
                    effort=brain_effort,
                ),
            ]
            tmux.send_keys(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, " && ".join(cmd_parts), enter=True)

        heartbeat_rearmed = _CODEX_HEARTBEAT_LOOP.restart(
            str(get_config_value(conn, "brain.heartbeat", default="off"))
        )

        return {
            "ok": True,
            "session_id": session_id,
            "already_running": alive,
            "heartbeat_rearmed": heartbeat_rearmed,
        }

    def stop_brain(self, conn) -> dict:
        session = _get_brain_session(conn)
        _CODEX_HEARTBEAT_LOOP.restart("off")

        if session:
            sessions_repo.update_session(conn, session.id, status="disconnected")

        tmux.send_keys(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, "C-c", enter=False)
        time.sleep(0.5)
        tmux.send_keys(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, "exit", enter=True)

        if os.path.exists(_BRAIN_DIR):
            for item in os.listdir(_BRAIN_DIR):
                item_path = os.path.join(_BRAIN_DIR, item)
                try:
                    if os.path.isfile(item_path) or os.path.islink(item_path):
                        os.unlink(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                except Exception as e:
                    logger.warning("Could not delete %s: %s", item_path, e)

        return {"ok": True}

    def redeploy_brain(self, conn) -> dict:
        brain = _get_brain_session(conn)
        if brain is None or brain.status in ("disconnected",):
            raise ValueError("Brain is not running")

        deploy_brain_tmp_contents(_BRAIN_DIR, conn=conn, provider=self.provider_id)
        heartbeat_rearmed = _CODEX_HEARTBEAT_LOOP.restart(
            str(get_config_value(conn, "brain.heartbeat", default="off"))
        )
        logger.info("Brain files re-deployed for Codex")
        return {"ok": True, "redeployed": True, "heartbeat_rearmed": heartbeat_rearmed}

    def get_launch_command(
        self,
        session_id: str,
        tmp_dir: str,
        model: str | None = None,
        effort: str | None = None,
        skip_permissions: bool = False,
    ) -> str:
        prompt_path = os.path.join(tmp_dir, "prompt.md")
        return _build_codex_command(
            workspace_dir=tmp_dir,
            prompt_path=prompt_path,
            model=model or _DEFAULT_CODEX_MODEL,
            effort=effort or _DEFAULT_REASONING_EFFORT,
        )

    def is_alive(self, tmux_sess: str, tmux_win: str, session_id: str) -> tuple[bool, str]:
        """Check if Codex is running for a local worker via the tmux pane process tree."""
        from orchestrator.session.health import _get_pane_pid, _has_process_in_tree

        pane_pid = _get_pane_pid(tmux_sess, tmux_win)
        if pane_pid is not None and _has_process_in_tree(pane_pid, "codex"):
            return True, "Codex process running in pane"
        return False, "No Codex process found in pane"

    def check_session_exists(self, host: str, session_id: str) -> bool:
        return False  # Persistence not implemented for Codex yet


CODEX_RUNTIME = CodexRuntime()
