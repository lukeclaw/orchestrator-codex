"""Orchestrator brain — manages the Claude Code process that acts as the central intelligence."""

import base64
import logging
import os
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.api.websocket import (
    get_current_focus,
    request_focus_from_frontend,
    set_current_focus,
)
from orchestrator.providers import DEFAULT_PROVIDER_ID, get_provider_runtime
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.terminal import manager as tmux
from orchestrator.terminal.remote_worker_server import get_remote_worker_server
from orchestrator.terminal.session import send_to_session
from orchestrator.terminal.ssh import is_remote_host

logger = logging.getLogger(__name__)

router = APIRouter()

BRAIN_SESSION_NAME = "brain"


def _get_brain_session(db):
    """Get the brain session from DB, or None."""
    return sessions_repo.get_session_by_name(db, BRAIN_SESSION_NAME)


def _get_brain_provider(db):
    from orchestrator.state.repositories.config import get_config_value

    return str(get_config_value(db, "brain.default_provider", default=DEFAULT_PROVIDER_ID))


def _get_effective_brain_provider(db):
    session = _get_brain_session(db)
    if session and session.status not in ("disconnected",) and session.provider:
        return session.provider
    return _get_brain_provider(db)


def _translate_brain_command(provider: str, command: str) -> str:
    if provider != "codex":
        return command

    stripped = command.strip()
    if stripped == "/clear":
        return (
            "Reset your coordination context for a fresh turn. Drop prior task-specific "
            "assumptions and wait for the next instruction."
        )
    if stripped == "/check_worker":
        return (
            "Review all active workers now. Use the orchestration CLI tools to identify completed, "
            "blocked, or stalled work, take the necessary coordination actions, and summarize the result."
        )
    if stripped.startswith("/create"):
        details = stripped[len("/create") :].strip()
        if details:
            return f"Create a new worker for this request: {details}"
        return "Create a new worker for the next requested task."
    return command


@router.get("/brain/status")
def brain_status(db=Depends(get_db)):
    """Get the orchestrator brain status."""
    session = _get_brain_session(db)
    provider = _get_effective_brain_provider(db)
    if session is None:
        return {"running": False, "session_id": None, "status": None, "provider": provider}
    return {
        "running": session.status not in ("disconnected",),
        "session_id": session.id,
        "status": session.status,
        "provider": provider,
    }


class FocusUpdate(BaseModel):
    """Model for updating the current dashboard URL."""

    url: str


@router.get("/brain/focus")
async def get_focus(realtime: bool = True):
    """Get the current dashboard URL.

    Args:
        realtime: If True (default), request fresh URL from frontend via WebSocket.
                  If False, return cached value immediately.
    """
    if realtime:
        url = await request_focus_from_frontend(timeout=0.5)
    else:
        url = get_current_focus()
    return {"url": url}


@router.post("/brain/focus")
def set_focus(focus: FocusUpdate):
    """Set the current dashboard URL. Called by frontend on navigation."""
    set_current_focus(focus.url)
    return {"ok": True, "url": focus.url}


@router.post("/brain/start", status_code=200)
def start_brain(db=Depends(get_db)):
    """Start the orchestrator brain via the selected provider runtime."""
    provider = _get_brain_provider(db)
    session = _get_brain_session(db)
    if session is not None and session.status in ("disconnected",) and session.provider != provider:
        sessions_repo.update_session(db, session.id, provider=provider)
    runtime = get_provider_runtime(provider)
    try:
        return runtime.start_brain(db)
    except Exception as e:
        logger.exception("Failed to start orchestrator brain")
        raise HTTPException(500, f"Failed to start brain: {e}")


@router.post("/brain/stop", status_code=200)
def stop_brain(db=Depends(get_db)):
    """Stop the orchestrator brain."""
    runtime = get_provider_runtime(_get_effective_brain_provider(db))
    return runtime.stop_brain(db)


@router.post("/brain/redeploy", status_code=200)
def brain_redeploy(db=Depends(get_db)):
    """Re-deploy brain files and re-arm heartbeat loop."""
    runtime = get_provider_runtime(_get_effective_brain_provider(db))
    try:
        return runtime.redeploy_brain(db)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/brain/sync", status_code=200)
def brain_sync(db=Depends(get_db)):
    """Trigger monitoring: compose a status report of active workers and send it to the brain."""
    brain = _get_brain_session(db)
    if brain is None or brain.status in ("disconnected",):
        raise HTTPException(400, "Brain is not running")

    # Gather non-brain sessions that are actively working/waiting/error
    # Get only worker sessions (excludes brain)
    worker_sessions = sessions_repo.list_sessions(db, session_type="worker")
    active_workers = [s for s in worker_sessions if s.status not in ("idle", "disconnected")]

    if not active_workers:
        return {"ok": True, "message": "No active workers to check", "workers_checked": 0}

    # Build monitoring prompt with terminal previews
    parts = [
        "Review the following active workers and take action:",
        "",
    ]
    for s in active_workers:
        parts.append(f"## Worker: {s.name} (status: {s.status}, id: {s.id})")
        # Capture terminal preview
        if is_remote_host(s.host) and s.rws_pty_id:
            try:
                rws = get_remote_worker_server(s.host)
                preview = rws.capture_pty(s.rws_pty_id, lines=30)
            except Exception:
                preview = "(could not capture terminal)"
        else:
            ts, tw = tmux.tmux_target(s.name)
            try:
                preview = tmux.capture_output(ts, tw, lines=30)
            except Exception:
                preview = "(could not capture terminal)"
        parts.append("```")
        parts.append(preview.rstrip())
        parts.append("```")
        parts.append("")

    parts.append("---")
    parts.append("Instructions:")
    parts.append(
        "1. Assess each worker: has it COMPLETED its task or is it still actively working?"
    )
    parts.append("2. If a worker has finished (idle prompt, completion message, task done):")
    parts.append("   - Stop it: orch-workers stop {s.name}")
    parts.append("   - Then delete it: orch-workers delete {s.name}")
    parts.append(
        "3. If a worker is waiting for input or stuck, try to unblock it by sending instructions."
    )
    parts.append("4. If a worker is actively working and making progress, skip it.")
    parts.append("5. Summarize your findings and actions taken.")

    prompt = "\n".join(parts)

    success = send_to_session(BRAIN_SESSION_NAME, prompt, tmux.TMUX_SESSION)
    if not success:
        raise HTTPException(500, "Failed to send monitoring prompt to brain")

    return {
        "ok": True,
        "message": f"Monitoring prompt sent, checking {len(active_workers)} workers",
        "workers_checked": len(active_workers),
    }


class PasteImageRequest(BaseModel):
    """Request body for pasting an image from clipboard."""

    image_data: str  # Base64-encoded image data (with or without data URL prefix)
    filename: str | None = None  # Optional custom filename


@router.post("/brain/paste-image")
def paste_image(req: PasteImageRequest, db=Depends(get_db)):
    """Save a clipboard image to the brain's tmp folder and return the file path.

    The image is saved to /tmp/orchestrator/brain/tmp/ with a timestamped filename.
    Returns the absolute path that can be used in Claude Code prompts.
    """
    session = _get_brain_session(db)
    if session is None or session.status in ("disconnected",):
        raise HTTPException(400, "Brain is not running")

    # Parse the base64 data (handle data URL prefix if present)
    image_data = req.image_data
    file_ext = "png"  # Default extension

    if image_data.startswith("data:"):
        # Extract mime type and base64 data from data URL
        # Format: data:image/png;base64,<base64data>
        try:
            header, image_data = image_data.split(",", 1)
            mime_part = header.split(";")[0]  # data:image/png
            if "/" in mime_part:
                mime_type = mime_part.split("/")[1]
                # Map common mime types to extensions
                ext_map = {"png": "png", "jpeg": "jpg", "jpg": "jpg", "gif": "gif", "webp": "webp"}
                file_ext = ext_map.get(mime_type, "png")
        except ValueError:
            pass  # Keep defaults if parsing fails

    # Decode base64 data
    try:
        image_bytes = base64.b64decode(image_data)
    except Exception as e:
        raise HTTPException(400, f"Invalid base64 image data: {e}")

    # Create tmp directory inside brain working directory
    brain_tmp_dir = "/tmp/orchestrator/brain/tmp"
    os.makedirs(brain_tmp_dir, exist_ok=True)

    # Generate filename with timestamp for uniqueness
    if req.filename:
        # Sanitize custom filename
        safe_name = "".join(c for c in req.filename if c.isalnum() or c in ".-_")
        if not safe_name:
            safe_name = "image"
        filename = f"{safe_name}.{file_ext}"
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_id = uuid.uuid4().hex[:6]
        filename = f"clipboard_{timestamp}_{short_id}.{file_ext}"

    file_path = os.path.join(brain_tmp_dir, filename)

    # Handle filename collision by appending counter
    counter = 1
    base_path = file_path
    while os.path.exists(file_path):
        name, ext = os.path.splitext(base_path)
        file_path = f"{name}_{counter}{ext}"
        counter += 1

    # Write the image file
    try:
        with open(file_path, "wb") as f:
            f.write(image_bytes)
        logger.info("Saved clipboard image to %s (%d bytes)", file_path, len(image_bytes))
    except Exception as e:
        logger.exception("Failed to save clipboard image")
        raise HTTPException(500, f"Failed to save image: {e}")

    return {
        "ok": True,
        "file_path": file_path,
        "filename": os.path.basename(file_path),
        "size": len(image_bytes),
    }


class UploadFileRequest(BaseModel):
    """Request body for uploading a file via drag-and-drop."""

    file_data: str  # base64-encoded file content
    filename: str  # original filename


@router.post("/brain/upload-file")
def upload_file_to_brain(req: UploadFileRequest, db=Depends(get_db)):
    """Upload a file to the brain's tmp dir and return the file path.

    Used by drag-and-drop in the brain panel. Same pattern as the session
    upload endpoint but saves to the brain's working directory.
    """
    from orchestrator.api.upload_utils import MAX_FILE_SIZE, is_supported_file, save_uploaded_file

    session = _get_brain_session(db)
    if session is None or session.status in ("disconnected",):
        raise HTTPException(400, "Brain is not running")

    if not is_supported_file(req.filename):
        raise HTTPException(415, f"Unsupported file type: {req.filename}")

    try:
        file_bytes = base64.b64decode(req.file_data, validate=True)
    except Exception as e:
        raise HTTPException(400, f"Invalid base64 data: {e}")

    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(413, f"File too large ({len(file_bytes)} bytes, max {MAX_FILE_SIZE})")

    brain_tmp_dir = "/tmp/orchestrator/brain/tmp"
    file_path = save_uploaded_file(file_bytes, req.filename, brain_tmp_dir)

    return {
        "ok": True,
        "file_path": file_path,
        "filename": os.path.basename(file_path),
        "size": len(file_bytes),
    }


class PasteTextRequest(BaseModel):
    """Request body for pasting text via bracketed paste."""

    text: str


@router.post("/brain/paste-to-pane")
def brain_paste_to_pane(req: PasteTextRequest, db=Depends(get_db)):
    """Paste text into the brain terminal using bracketed paste mode.

    Uses tmux ``paste-buffer -p`` which wraps text in ``ESC[200~`` …
    ``ESC[201~`` sequences so Claude Code displays it compactly
    (e.g. ``[42 lines of text]``).
    """
    session = _get_brain_session(db)
    if session is None or session.status in ("disconnected",):
        raise HTTPException(400, "Brain is not running")

    success = tmux.paste_to_pane(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, req.text)
    if not success:
        raise HTTPException(500, "Failed to paste text to brain")
    return {"ok": True}


class BrainCommandRequest(BaseModel):
    """Request body for sending a command to the brain terminal."""

    command: str  # The command text to type (e.g. "/clear", "/check_worker")
    enter: bool = True  # Whether to press Enter after typing the command


@router.post("/brain/command")
def brain_command(req: BrainCommandRequest, db=Depends(get_db)):
    """Interrupt the brain terminal, then type a command.

    Sequence: Ctrl-C → Escape → type command → optionally Enter.
    Used by the UI quick-action buttons (Clear, Check Workers, Create).
    """
    session = _get_brain_session(db)
    if session is None or session.status in ("disconnected",):
        raise HTTPException(400, "Brain is not running")
    translated_command = _translate_brain_command(session.provider or DEFAULT_PROVIDER_ID, req.command)

    try:
        # 1. Send Ctrl-C to cancel any running operation
        tmux.send_keys(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, "C-c", enter=False)
        time.sleep(0.15)

        # 2. Send Escape to exit any mode / dismiss prompts
        tmux.send_keys(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, "Escape", enter=False)
        time.sleep(0.15)

        # 3. Clear any leftover input on the line (Ctrl-U)
        tmux.send_keys(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, "C-u", enter=False)
        time.sleep(0.1)

        # 4. Type the command using literal mode (safe for special chars)
        tmux.send_keys_literal(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, translated_command)

        # 5. Optionally press Enter
        if req.enter:
            time.sleep(0.1)
            tmux.send_keys(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, "", enter=True)

        return {
            "ok": True,
            "command": translated_command,
            "requested_command": req.command,
            "entered": req.enter,
        }

    except Exception as e:
        logger.exception("Failed to send command to brain")
        raise HTTPException(500, f"Failed to send command: {e}")
