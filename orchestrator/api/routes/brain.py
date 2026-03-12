"""Orchestrator brain — manages the Claude Code process that acts as the central intelligence."""

import base64
import logging
import os
import shutil
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator.agents import get_path_export_command
from orchestrator.api.deps import get_db
from orchestrator.api.websocket import (
    get_current_focus,
    request_focus_from_frontend,
    set_current_focus,
)
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


@router.get("/brain/status")
def brain_status(db=Depends(get_db)):
    """Get the orchestrator brain status."""
    session = _get_brain_session(db)
    if session is None:
        return {"running": False, "session_id": None, "status": None}
    return {
        "running": session.status not in ("disconnected",),
        "session_id": session.id,
        "status": session.status,
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
    """Start the orchestrator brain — a Claude Code process with project management tools.

    Idempotent: safe to call regardless of current state.  Uses the tmux
    pane as the single source of truth (the DB record is reconciled to
    match).

    Decision matrix based on pane_foreground_command():
      - None          → pane doesn't exist yet → create & launch
      - shell name    → pane exists, Claude not running → launch
      - anything else → Claude (or another process) is running → skip
    """
    session = _get_brain_session(db)

    # ── Check tmux pane state (single source of truth) ──────────────
    shells = {"bash", "zsh", "fish", "sh", "dash"}
    pane_cmd = tmux.pane_foreground_command(tmux.TMUX_SESSION, BRAIN_SESSION_NAME)
    claude_already_running = pane_cmd is not None and pane_cmd not in shells

    # ── Deploy brain files (always, so hooks/skills stay current) ───
    brain_dir = "/tmp/orchestrator/brain"

    from orchestrator.agents.deploy import deploy_brain_tmp_contents

    deploy_brain_tmp_contents(brain_dir, conn=db)
    logger.info("Deployed brain tmp contents via SOT")

    bin_dir = os.path.join(brain_dir, "bin")
    path_export = get_path_export_command(bin_dir)
    settings_path = os.path.join(brain_dir, ".claude", "settings.json")

    try:
        # ensure_window is itself idempotent (no-op when window exists)
        target = tmux.ensure_window(tmux.TMUX_SESSION, BRAIN_SESSION_NAME)

        # ── Reconcile DB record ─────────────────────────────────────
        if session:
            sessions_repo.update_session(db, session.id, status="working")
            session_id = session.id
        else:
            s = sessions_repo.create_session(
                db,
                name=BRAIN_SESSION_NAME,
                host="local",
                work_dir=brain_dir,
                session_type="brain",
            )
            session_id = s.id
            sessions_repo.update_session(db, session_id, status="working")

        # ── If Claude is already running, we're done ────────────────
        if claude_already_running:
            logger.info("Brain pane already running '%s'; skipping launch", pane_cmd)
            return {
                "ok": True,
                "session_id": session_id,
                "status": "working",
                "message": "Brain already running (reconnected)",
            }

        # ── Pane is at a shell prompt (new or leftover) — launch ────
        tmux.send_keys(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, f"cd {brain_dir}")
        tmux.send_keys(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, path_export)

        # Optionally update Claude Code before launching
        from orchestrator.terminal.claude_update import (
            run_claude_update,
            should_update_before_start,
        )

        if should_update_before_start(db):
            time.sleep(0.3)
            run_claude_update(
                tmux.send_keys, tmux.capture_output, tmux.TMUX_SESSION, BRAIN_SESSION_NAME
            )

        time.sleep(0.5)
        tmux.send_keys(
            tmux.TMUX_SESSION,
            BRAIN_SESSION_NAME,
            f"claude --dangerously-skip-permissions --settings {settings_path}",
        )

        # Dismiss any "trust this folder" prompt that may appear after launch
        tmux.dismiss_trust_prompt(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, session_id=session_id)

        logger.info("Orchestrator brain started in %s", target)
        return {
            "ok": True,
            "session_id": session_id,
            "status": "working",
            "message": "Brain started",
        }

    except Exception as e:
        logger.exception("Failed to start orchestrator brain")
        raise HTTPException(500, f"Failed to start brain: {e}")


@router.post("/brain/stop", status_code=200)
def stop_brain(db=Depends(get_db)):
    """Stop the orchestrator brain and clean up tmp directory."""
    session = _get_brain_session(db)
    if session is None:
        return {"ok": True, "message": "Brain not running"}

    brain_dir = "/tmp/orchestrator/brain"

    try:
        # Send Ctrl-C three times to force-exit Claude Code
        for _ in range(3):
            tmux.send_keys(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, "C-c", enter=False)
            time.sleep(0.3)
        sessions_repo.update_session(db, session.id, status="disconnected")
        logger.info("Orchestrator brain stopped")
    except Exception:
        logger.exception("Failed to stop brain")
        # Force-update status even if tmux command failed
        sessions_repo.update_session(db, session.id, status="disconnected")

    # Clean up brain tmp directory so next start is clean
    try:
        if os.path.exists(brain_dir):
            shutil.rmtree(brain_dir)
            logger.info("Cleaned up brain directory: %s", brain_dir)
    except Exception as e:
        logger.warning("Could not clean up brain directory %s: %s", brain_dir, e)

    return {"ok": True, "message": "Brain stopped"}


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
    parts.append("   - Stop it: curl -s -X POST http://127.0.0.1:8093/api/sessions/{id}/stop")
    parts.append("   - Then delete it: curl -s -X DELETE http://127.0.0.1:8093/api/sessions/{id}")
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
        tmux.send_keys_literal(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, req.command)

        # 5. Optionally press Enter
        if req.enter:
            time.sleep(0.1)
            tmux.send_keys(tmux.TMUX_SESSION, BRAIN_SESSION_NAME, "", enter=True)

        return {"ok": True, "command": req.command, "entered": req.enter}

    except Exception as e:
        logger.exception("Failed to send command to brain")
        raise HTTPException(500, f"Failed to send command: {e}")
