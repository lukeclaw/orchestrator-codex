"""Orchestrator brain — manages the Claude Code process that acts as the central intelligence."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.terminal import manager as tmux

logger = logging.getLogger(__name__)

router = APIRouter()

BRAIN_SESSION_NAME = "brain"
TMUX_SESSION = "orchestrator"


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
    claude_md_src = os.path.join(source_root, "brain", "CLAUDE.md")
    if os.path.exists(claude_md_src):
        shutil.copy2(claude_md_src, os.path.join(brain_dir, "CLAUDE.md"))

    try:
        # Create tmux window for the brain
        target = tmux.ensure_window(TMUX_SESSION, BRAIN_SESSION_NAME)

        # cd to the brain working directory so Claude Code picks up CLAUDE.md
        tmux.send_keys(TMUX_SESSION, BRAIN_SESSION_NAME, f"cd {brain_dir}")

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
                mp_path=brain_dir,
                tmux_window=target,
            )
            session_id = s.id

        # Launch Claude Code
        import time
        time.sleep(0.5)
        tmux.send_keys(TMUX_SESSION, BRAIN_SESSION_NAME, "claude")
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
