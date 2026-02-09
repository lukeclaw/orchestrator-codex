"""Orchestrator brain — manages the Claude Code process that acts as the central intelligence."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.terminal import manager as tmux
from orchestrator.terminal.session import send_to_session
from orchestrator.brain.cli_scripts import generate_brain_scripts, get_brain_path_export, generate_brain_hooks

logger = logging.getLogger(__name__)

router = APIRouter()

BRAIN_SESSION_NAME = "brain"
TMUX_SESSION = "orchestrator"

# In-memory storage for current dashboard URL
_current_url: str | None = None


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
        "tmux_window": session.tmux_window,
    }


class FocusUpdate(BaseModel):
    """Model for updating the current dashboard URL."""
    url: str


@router.get("/brain/focus")
def get_focus():
    """Get the current dashboard URL."""
    return {"url": _current_url}


@router.post("/brain/focus")
def set_focus(focus: FocusUpdate):
    """Set the current dashboard URL. Called by frontend on navigation."""
    global _current_url
    _current_url = focus.url
    return {"ok": True, "url": _current_url}


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

    # Copy CLAUDE.md from the source tree into the working directory
    import shutil
    source_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    claude_md_src = os.path.join(source_root, "prompts", "brain_claude.md")
    if os.path.exists(claude_md_src):
        shutil.copy2(claude_md_src, os.path.join(brain_dir, "CLAUDE.md"))

    # Deploy brain CLI scripts
    bin_dir = generate_brain_scripts(brain_dir)
    path_export = get_brain_path_export(bin_dir)

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
                tmux_window=target,
            )
            session_id = session.id
        else:
            # Create new session record
            s = sessions_repo.create_session(
                db,
                name=BRAIN_SESSION_NAME,
                host="local",
                work_dir=brain_dir,
                tmux_window=target,
                session_type="brain",
            )
            session_id = s.id

        # Launch Claude Code with hooks settings
        import time
        time.sleep(0.5)
        tmux.send_keys(TMUX_SESSION, BRAIN_SESSION_NAME, f"claude --settings {settings_path}")
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
    """Stop the orchestrator brain."""
    session = _get_brain_session(db)
    if session is None:
        return {"ok": True, "message": "Brain not running"}

    try:
        # Send Ctrl-C three times to force-exit Claude Code
        import time
        for _ in range(3):
            tmux.send_keys(TMUX_SESSION, BRAIN_SESSION_NAME, "C-c", enter=False)
            time.sleep(0.3)
        sessions_repo.update_session(db, session.id, status="disconnected")
        logger.info("Orchestrator brain stopped")
        return {"ok": True, "message": "Brain stopped"}
    except Exception as e:
        logger.exception("Failed to stop brain")
        # Force-update status even if tmux command failed
        sessions_repo.update_session(db, session.id, status="disconnected")
        return {"ok": True, "message": f"Brain marked as stopped (tmux error: {e})"}


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
        preview = "(no tmux window)"
        if s.tmux_window:
            if ":" in s.tmux_window:
                ts, tw = s.tmux_window.split(":", 1)
            else:
                ts, tw = "orchestrator", s.tmux_window
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
