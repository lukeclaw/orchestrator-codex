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

from orchestrator.agents import deploy_brain_scripts, generate_brain_hooks, get_path_export_command
from orchestrator.agents.deploy import (
    deploy_custom_skills,
    format_custom_skills_for_prompt,
    get_brain_prompt,
    get_brain_skills_dir,
)
from orchestrator.api.deps import get_db
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.terminal import manager as tmux
from orchestrator.terminal.session import send_to_session

logger = logging.getLogger(__name__)

router = APIRouter()

BRAIN_SESSION_NAME = "brain"
TMUX_SESSION = "orchestrator"

from orchestrator.api.websocket import (
    get_current_focus,
    request_focus_from_frontend,
    set_current_focus,
)


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
    """Start the orchestrator brain — a Claude Code process with project management tools."""
    import os

    session = _get_brain_session(db)
    if session and session.status not in ("disconnected",):
        return {
            "ok": True,
            "session_id": session.id,
            "status": session.status,
            "message": "Brain already running",
        }

    # Brain runs in /tmp/orchestrator/brain, decoupled from the git repo
    brain_dir = "/tmp/orchestrator/brain"
    os.makedirs(brain_dir, exist_ok=True)

    # Fetch custom brain skills from DB (enabled only)
    from orchestrator.state.repositories import skills as skills_repo
    custom_skills = skills_repo.list_skills(db, target="brain", enabled_only=True)
    custom_skills_dicts = [{"name": s.name, "description": s.description, "content": s.content} for s in custom_skills]
    custom_skills_section = format_custom_skills_for_prompt(custom_skills_dicts)

    # Copy brain prompt as CLAUDE.md into the working directory
    brain_prompt = get_brain_prompt(custom_skills_section=custom_skills_section)
    if brain_prompt:
        with open(os.path.join(brain_dir, "CLAUDE.md"), "w") as f:
            f.write(brain_prompt)

    # Deploy pre-built skills to .claude/commands/ (skip disabled)
    disabled_builtins = skills_repo.list_disabled_builtin_skills(db, "brain")
    skills_src = get_brain_skills_dir()
    skills_dest = os.path.join(brain_dir, ".claude", "commands")
    # Clear stale skill files before repopulating
    if os.path.isdir(skills_dest):
        for f in os.listdir(skills_dest):
            if f.endswith(".md"):
                os.remove(os.path.join(skills_dest, f))
    if skills_src and os.path.isdir(skills_src):
        os.makedirs(skills_dest, exist_ok=True)
        for skill_file in os.listdir(skills_src):
            if skill_file.endswith(".md"):
                skill_name = os.path.splitext(skill_file)[0]
                if (skill_name, "brain") in disabled_builtins:
                    continue
                shutil.copy2(
                    os.path.join(skills_src, skill_file),
                    os.path.join(skills_dest, skill_file),
                )
        logger.info("Deployed %d built-in skills to %s", len(os.listdir(skills_dest)), skills_dest)

    # Deploy custom brain skills from DB
    if custom_skills_dicts:
        deploy_custom_skills(skills_dest, custom_skills_dicts)
        logger.info("Deployed %d custom brain skills to %s", len(custom_skills_dicts), skills_dest)

    # Deploy brain CLI scripts
    bin_dir = deploy_brain_scripts(brain_dir)
    path_export = get_path_export_command(bin_dir)

    # Generate brain hooks (injects dashboard focus context into prompts)
    settings_path = generate_brain_hooks(brain_dir)

    try:
        # Create tmux window for the brain
        target = tmux.ensure_window(TMUX_SESSION, BRAIN_SESSION_NAME)

        # cd to the brain working directory so Claude Code picks up CLAUDE.md
        tmux.send_keys(TMUX_SESSION, BRAIN_SESSION_NAME, f"cd {brain_dir}")

        # Add brain CLI tools to PATH
        tmux.send_keys(TMUX_SESSION, BRAIN_SESSION_NAME, path_export)

        if session:
            # Reuse existing DB record
            sessions_repo.update_session(
                db, session.id,
                status="idle",
            )
            session_id = session.id
        else:
            # Create new session record
            s = sessions_repo.create_session(
                db,
                name=BRAIN_SESSION_NAME,
                host="local",
                work_dir=brain_dir,
                session_type="brain",
            )
            session_id = s.id

        # Launch Claude Code with hooks settings
        import time
        time.sleep(0.5)
        tmux.send_keys(TMUX_SESSION, BRAIN_SESSION_NAME, f"claude --dangerously-skip-permissions --settings {settings_path}")
        sessions_repo.update_session(db, session_id, status="working")

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
            tmux.send_keys(TMUX_SESSION, BRAIN_SESSION_NAME, "C-c", enter=False)
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
    active_workers = [
        s for s in worker_sessions
        if s.status not in ("idle", "disconnected")
    ]

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
    parts.append("1. Assess each worker: has it COMPLETED its task or is it still actively working?")
    parts.append("2. If a worker has finished (idle prompt, completion message, task done):")
    parts.append("   - Stop it: curl -s -X POST http://127.0.0.1:8093/api/sessions/{id}/stop")
    parts.append("   - Then delete it: curl -s -X DELETE http://127.0.0.1:8093/api/sessions/{id}")
    parts.append("3. If a worker is waiting for input or stuck, try to unblock it by sending instructions.")
    parts.append("4. If a worker is actively working and making progress, skip it.")
    parts.append("5. Summarize your findings and actions taken.")

    prompt = "\n".join(parts)

    success = send_to_session(BRAIN_SESSION_NAME, prompt, TMUX_SESSION)
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
    """Request body for pasting long text from clipboard."""
    text: str


@router.post("/brain/paste-text")
def paste_text(req: PasteTextRequest, db=Depends(get_db)):
    """Save long clipboard text to the brain's tmp folder and return the file path."""
    session = _get_brain_session(db)
    if session is None or session.status in ("disconnected",):
        raise HTTPException(400, "Brain is not running")

    text_bytes = req.text.encode("utf-8")

    brain_tmp_dir = "/tmp/orchestrator/brain/tmp"
    os.makedirs(brain_tmp_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:6]
    filename = f"clipboard_{timestamp}_{short_id}.txt"
    file_path = os.path.join(brain_tmp_dir, filename)

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(req.text)
        logger.info("Saved clipboard text to %s (%d bytes)", file_path, len(text_bytes))
    except Exception as e:
        logger.exception("Failed to save clipboard text")
        raise HTTPException(500, f"Failed to save text: {e}")

    return {
        "ok": True,
        "file_path": file_path,
        "filename": filename,
        "size": len(text_bytes),
    }
