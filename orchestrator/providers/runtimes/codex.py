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
    deploy_codex_brain_tmp_contents,
    deploy_codex_worker_tmp_contents,
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
_SUPPORTED_REASONING_EFFORTS = {"low", "medium", "high"}
_CLAUDE_MODELS = {"haiku", "sonnet", "opus"}
_HEARTBEAT_WEEKDAY_RE = re.compile(
    r"^weekdays at (?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)$",
    re.IGNORECASE,
)
_HEARTBEAT_INTERVAL_RE = re.compile(
    r"^every (?:(?P<count>\d+)\s+)?(?P<unit>minute|minutes|hour|hours|day|days)$",
    re.IGNORECASE,
)


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


def _build_codex_heartbeat_prompt() -> str:
    return (
        "Heartbeat cycle: review active workers now using the orchestration CLI tools. "
        "Check blocked and waiting workers first, investigate visible failures, send "
        '\"continue\" only when a worker is idle at a prompt, stop and mark done only when '
        "completion is verified, and notify the user for anything risky or ambiguous. "
        "Keep the output brief and operational."
    )


def _parse_heartbeat_schedule(value: str | None):
    normalized = (value or "").strip()
    if not normalized or normalized.lower() == "off":
        return None

    match = _HEARTBEAT_INTERVAL_RE.match(normalized)
    if match:
        count = int(match.group("count") or "1")
        unit = match.group("unit").lower()
        seconds_per_unit = {
            "minute": 60,
            "minutes": 60,
            "hour": 3600,
            "hours": 3600,
            "day": 86400,
            "days": 86400,
        }
        return ("interval", count * seconds_per_unit[unit])

    match = _HEARTBEAT_WEEKDAY_RE.match(normalized)
    if match:
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or "0")
        ampm = match.group("ampm").lower()
        if hour == 12:
            hour = 0
        if ampm == "pm":
            hour += 12
        return ("weekday_time", (hour, minute))

    return None


def _next_heartbeat_delay(schedule) -> float:
    kind, payload = schedule
    if kind == "interval":
        return float(payload)

    hour, minute = payload
    now = datetime.now()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if now.weekday() >= 5:
        days_until = 7 - now.weekday()
        candidate = candidate + timedelta(days=days_until)
    elif candidate <= now:
        candidate = candidate + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate = candidate + timedelta(days=1)

    while candidate.weekday() >= 5:
        candidate = candidate + timedelta(days=1)

    return max((candidate - now).total_seconds(), 1.0)


class _CodexHeartbeatLoop:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._schedule = None

    def restart(self, interval_text: str | None) -> bool:
        schedule = _parse_heartbeat_schedule(interval_text)
        self.stop()
        if schedule is None:
            return False

        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._run,
            args=(stop_event, schedule),
            daemon=True,
            name="codex-brain-heartbeat",
        )
        with self._lock:
            self._stop_event = stop_event
            self._thread = thread
            self._schedule = schedule
        thread.start()
        logger.info("Started Codex brain heartbeat loop for schedule %r", interval_text)
        return True

    def stop(self) -> None:
        thread = None
        stop_event = None
        with self._lock:
            thread = self._thread
            stop_event = self._stop_event
            self._thread = None
            self._stop_event = None
            self._schedule = None
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def _run(self, stop_event: threading.Event, schedule) -> None:
        while not stop_event.wait(_next_heartbeat_delay(schedule)):
            try:
                pane_cmd = tmux.pane_foreground_command(tmux.TMUX_SESSION, BRAIN_SESSION_NAME)
                if not pane_cmd or "codex" not in pane_cmd.lower():
                    logger.info("Stopping Codex heartbeat loop because brain pane is not running Codex")
                    return
                sent = send_to_session(
                    BRAIN_SESSION_NAME,
                    _build_codex_heartbeat_prompt(),
                    tmux.TMUX_SESSION,
                )
                if sent:
                    logger.info("Codex brain heartbeat prompt sent")
                else:
                    logger.warning("Codex brain heartbeat prompt could not be sent")
            except Exception:
                logger.exception("Codex brain heartbeat loop error")


_CODEX_HEARTBEAT_LOOP = _CodexHeartbeatLoop()


class CodexRuntime:
    """Codex provider runtime."""

    provider_id = "codex"

    def launch_local_worker(self, request: WorkerLaunchRequest) -> dict:
        local_tmp_dir = request.tmp_dir or f"/tmp/orchestrator/workers/{request.name}"
        api_base = f"http://127.0.0.1:{request.api_port}"
        cdp_port = 9222

        try:
            deploy_codex_worker_tmp_contents(
                local_tmp_dir,
                request.session_id,
                api_base=api_base,
                cdp_port=cdp_port,
                browser_headless=False,
            )
            prompt_path = os.path.join(local_tmp_dir, "prompt.md")

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

        deploy_codex_brain_tmp_contents(_BRAIN_DIR, conn=conn, provider=self.provider_id)
        prompt_path = os.path.join(_BRAIN_DIR, "prompt.md")
        target = tmux.ensure_window(tmux.TMUX_SESSION, BRAIN_SESSION_NAME)

        if session:
            sessions_repo.update_session(conn, session.id, status="working", provider=self.provider_id)
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
            sessions_repo.update_session(conn, session_id, status="working", provider=self.provider_id)

        if codex_already_running:
            _CODEX_HEARTBEAT_LOOP.restart(get_config_value(conn, "brain.heartbeat", default="off"))
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
                model=get_provider_default_model(conn, self.provider_id),
                effort=get_provider_default_effort(conn, self.provider_id),
            ),
        ]
        tmux.send_keys(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, " && ".join(cmd_parts))
        heartbeat_rearmed = _CODEX_HEARTBEAT_LOOP.restart(
            str(get_config_value(conn, "brain.heartbeat", default="off"))
        )
        logger.info("Orchestrator brain started in %s via Codex", target)
        return {
            "ok": True,
            "session_id": session_id,
            "status": "working",
            "message": "Brain started",
            "heartbeat_rearmed": heartbeat_rearmed,
        }

    def stop_brain(self, conn) -> dict:
        _CODEX_HEARTBEAT_LOOP.stop()
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

        deploy_codex_brain_tmp_contents(_BRAIN_DIR, conn=conn, provider=self.provider_id)
        heartbeat_rearmed = _CODEX_HEARTBEAT_LOOP.restart(
            str(get_config_value(conn, "brain.heartbeat", default="off"))
        )
        logger.info("Brain files re-deployed for Codex")
        return {"ok": True, "redeployed": True, "heartbeat_rearmed": heartbeat_rearmed}


CODEX_RUNTIME = CodexRuntime()
