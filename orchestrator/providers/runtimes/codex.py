"""Codex runtime adapter."""

from __future__ import annotations

import logging
import os
import shlex
import shutil

from orchestrator import paths
from orchestrator.agents import deploy_brain_scripts, deploy_worker_scripts, get_path_export_command
from orchestrator.browser.cdp_worker_proxy import start_cdp_proxy
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.terminal import manager as tmux

from orchestrator.providers.runtime import WorkerLaunchRequest

logger = logging.getLogger(__name__)

BRAIN_SESSION_NAME = "brain"
_BRAIN_DIR = "/tmp/orchestrator/brain"
_DEFAULT_CODEX_MODEL = "gpt-5-codex"
_DEFAULT_REASONING_EFFORT = "high"
_SUPPORTED_REASONING_EFFORTS = {"low", "medium", "high"}
_CLAUDE_MODELS = {"haiku", "sonnet", "opus"}


def _read_prompt_template(*parts: str) -> str:
    prompt_path = paths.agents_dir().joinpath("codex", *parts)
    return prompt_path.read_text()


def _write_prompt_file(tmp_dir: str, prompt_text: str) -> str:
    os.makedirs(tmp_dir, exist_ok=True)
    prompt_path = os.path.join(tmp_dir, "prompt.md")
    with open(prompt_path, "w") as f:
        f.write(prompt_text)
    return prompt_path


def _resolve_model(model: str | None) -> str:
    if not model:
        return _DEFAULT_CODEX_MODEL
    normalized = model.strip()
    if not normalized or normalized.lower() in _CLAUDE_MODELS:
        return _DEFAULT_CODEX_MODEL
    return normalized


def _resolve_reasoning_effort(effort: str | None) -> str:
    normalized = (effort or "").strip().lower()
    if normalized in _SUPPORTED_REASONING_EFFORTS:
        return normalized
    return _DEFAULT_REASONING_EFFORT


def _quote_config(key: str, value: str) -> str:
    return f"-c {shlex.quote(f'{key}={value!r}')}"


def _build_codex_command(
    *,
    workspace_dir: str,
    prompt_path: str,
    model: str,
    effort: str,
    add_dirs: list[str] | None = None,
) -> str:
    parts = [
        "codex",
        "--no-alt-screen",
        "-a on-request",
        "-s workspace-write",
        f"-C {shlex.quote(workspace_dir)}",
        f"-m {shlex.quote(_resolve_model(model))}",
        _quote_config("model_reasoning_effort", _resolve_reasoning_effort(effort)),
        _quote_config("model_instructions_file", prompt_path),
    ]

    for add_dir in add_dirs or []:
        parts.append(f"--add-dir {shlex.quote(add_dir)}")

    return " ".join(parts)


def _get_brain_session(conn):
    return sessions_repo.get_session_by_name(conn, BRAIN_SESSION_NAME)


class CodexRuntime:
    """Codex provider runtime."""

    provider_id = "codex"

    def launch_local_worker(self, request: WorkerLaunchRequest) -> dict:
        local_tmp_dir = request.tmp_dir or f"/tmp/orchestrator/workers/{request.name}"
        api_base = f"http://127.0.0.1:{request.api_port}"
        cdp_port = 9222

        try:
            deploy_worker_scripts(
                local_tmp_dir,
                request.session_id,
                api_base=api_base,
                cdp_port=cdp_port,
                browser_headless=False,
            )

            prompt_text = _read_prompt_template("worker", "prompt.md")
            prompt_path = _write_prompt_file(local_tmp_dir, prompt_text)

            cmd_parts: list[str] = []
            workspace_dir = request.work_dir or local_tmp_dir
            if request.work_dir:
                cmd_parts.append(f"cd {shlex.quote(request.work_dir)}")

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

        shells = {"bash", "zsh", "fish", "sh", "dash"}
        pane_cmd = tmux.pane_foreground_command(tmux.TMUX_SESSION, BRAIN_SESSION_NAME)
        codex_already_running = pane_cmd is not None and pane_cmd not in shells

        deploy_brain_scripts(_BRAIN_DIR)
        prompt_text = _read_prompt_template("brain", "prompt.md")
        prompt_path = _write_prompt_file(_BRAIN_DIR, prompt_text)
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

        if codex_already_running:
            logger.info("Brain pane already running '%s'; skipping Codex launch", pane_cmd)
            return {
                "ok": True,
                "session_id": session_id,
                "status": "working",
                "message": "Brain already running (reconnected)",
            }

        cmd_parts = [
            f"cd {shlex.quote(_BRAIN_DIR)}",
            get_path_export_command(os.path.join(_BRAIN_DIR, "bin")),
            _build_codex_command(
                workspace_dir=_BRAIN_DIR,
                prompt_path=prompt_path,
                model=_DEFAULT_CODEX_MODEL,
                effort=_DEFAULT_REASONING_EFFORT,
            ),
        ]
        tmux.send_keys(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, " && ".join(cmd_parts))
        logger.info("Orchestrator brain started in %s via Codex", target)
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
            sessions_repo.update_session(conn, session.id, status="disconnected")
            logger.info("Orchestrator brain stopped")
        except Exception:
            logger.exception("Failed to stop Codex brain")
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

        deploy_brain_scripts(_BRAIN_DIR)
        prompt_text = _read_prompt_template("brain", "prompt.md")
        _write_prompt_file(_BRAIN_DIR, prompt_text)
        logger.info("Brain files re-deployed for Codex")
        return {"ok": True, "redeployed": True, "heartbeat_rearmed": False}


CODEX_RUNTIME = CodexRuntime()
