"""Session CRUD + send/takeover/release + terminal preview."""

import logging
import os
import shutil
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import sessions as repo
from orchestrator.terminal.manager import (
    capture_output,
    ensure_window,
    kill_window,
    send_keys,
)
from orchestrator.terminal.ssh import is_rdev_host

logger = logging.getLogger(__name__)

router = APIRouter()

WORKER_BASE_DIR = "/tmp/orchestrator/workers"


class SessionCreate(BaseModel):
    name: str
    host: str
    mp_path: str | None = None
    task_id: str | None = None


class SessionUpdate(BaseModel):
    status: str | None = None
    takeover_mode: bool | None = None


class SendMessage(BaseModel):
    message: str


def _serialize_session(s):
    return {
        "id": s.id, "name": s.name, "host": s.host,
        "mp_path": s.mp_path, "tmux_window": s.tmux_window,
        "tunnel_pane": s.tunnel_pane,
        "status": s.status, "takeover_mode": s.takeover_mode,
        "current_task_id": s.current_task_id,
        "created_at": s.created_at, "last_activity": s.last_activity,
    }


@router.get("/sessions")
def list_sessions(status: str | None = None, db=Depends(get_db)):
    sessions = repo.list_sessions(db, status=status)
    return [_serialize_session(s) for s in sessions]


@router.get("/sessions/{session_id}")
def get_session(session_id: str, db=Depends(get_db)):
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    return _serialize_session(s)


@router.post("/sessions", status_code=201)
def create_session(body: SessionCreate, request: Request, db=Depends(get_db)):
    # Create tmux window for the session
    tmux_session_name = "orchestrator"
    tmux_window = None
    try:
        target = ensure_window(tmux_session_name, body.name)
        tmux_window = target
        logger.info("Created tmux window for session %s: %s", body.name, target)
    except Exception:
        logger.warning("Could not create tmux window for session %s", body.name, exc_info=True)

    # Set up worker directory
    worker_dir = os.path.join(WORKER_BASE_DIR, body.name)
    mp_path = body.mp_path or worker_dir
    os.makedirs(worker_dir, exist_ok=True)

    s = repo.create_session(db, body.name, body.host, mp_path, tmux_window=tmux_window)

    if is_rdev_host(body.host):
        # rdev worker — launch full setup in background thread
        # (tunnel, SSH, Claude, prompt delivery takes ~30s)
        config = getattr(request.app.state, "config", {})
        api_port = config.get("server", {}).get("port", 8093)
        db_path = getattr(request.app.state, "db_path", None)

        repo.update_session(db, s.id, status="connecting")

        def _background_setup():
            from orchestrator.state.db import get_connection
            from orchestrator.terminal.session import setup_rdev_worker

            bg_conn = get_connection(db_path) if db_path else db
            try:
                result = setup_rdev_worker(
                    bg_conn, s.id, body.name, body.host,
                    tmux_session_name, api_port,
                    task_id=body.task_id,
                )
                if result["ok"]:
                    tunnel_target = f"{tmux_session_name}:{result['tunnel_window']}"
                    repo.update_session(
                        bg_conn, s.id,
                        status="working",
                        tunnel_pane=tunnel_target,
                    )
                    if body.task_id:
                        from orchestrator.state.repositories import tasks
                        tasks.update_task(bg_conn, body.task_id, assigned_session_id=s.id, status="in_progress")
                        repo.update_session(bg_conn, s.id, current_task_id=body.task_id)
                    logger.info("rdev worker %s setup complete", body.name)
                else:
                    repo.update_session(bg_conn, s.id, status="error")
                    logger.error("rdev worker %s setup failed: %s", body.name, result.get("error"))
            except Exception:
                logger.exception("rdev background setup failed for %s", body.name)
                try:
                    repo.update_session(bg_conn, s.id, status="error")
                except Exception:
                    pass
            finally:
                if db_path and bg_conn is not db:
                    bg_conn.close()

        thread = threading.Thread(target=_background_setup, daemon=True)
        thread.start()

        return {"id": s.id, "name": s.name, "status": "connecting"}

    else:
        # Local worker — write CLAUDE.md template to worker dir
        source_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        template_src = os.path.join(source_root, "prompts", "worker_claude_template.md")
        if os.path.exists(template_src):
            try:
                with open(template_src) as f:
                    template = f.read()
                populated = template.replace("SESSION_ID", s.id)
                with open(os.path.join(worker_dir, "CLAUDE.md"), "w") as f:
                    f.write(populated)
                logger.info("Wrote worker CLAUDE.md for %s in %s", body.name, worker_dir)
            except Exception:
                logger.warning("Could not write worker CLAUDE.md for %s", body.name, exc_info=True)

        return {"id": s.id, "name": s.name, "status": s.status}


@router.patch("/sessions/{session_id}")
def update_session(session_id: str, body: SessionUpdate, db=Depends(get_db)):
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    updated = repo.update_session(
        db, session_id,
        status=body.status,
        takeover_mode=body.takeover_mode,
    )
    return {"id": updated.id, "status": updated.status}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, db=Depends(get_db)):
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    # Kill the tunnel window if it exists (rdev workers)
    if s.tunnel_pane:
        if ":" in s.tunnel_pane:
            t_sess, t_win = s.tunnel_pane.split(":", 1)
        else:
            t_sess, t_win = "orchestrator", s.tunnel_pane
        try:
            kill_window(t_sess, t_win)
            logger.info("Killed tunnel window %s for session %s", s.tunnel_pane, s.name)
        except Exception:
            logger.warning("Could not kill tunnel window for session %s", s.name, exc_info=True)

    # Kill the tmux window if it exists
    if s.tmux_window:
        if ":" in s.tmux_window:
            tmux_sess, tmux_win = s.tmux_window.split(":", 1)
        else:
            tmux_sess, tmux_win = "orchestrator", s.tmux_window
        try:
            kill_window(tmux_sess, tmux_win)
            logger.info("Killed tmux window %s:%s for session %s", tmux_sess, tmux_win, s.name)
        except Exception:
            logger.warning("Could not kill tmux window for session %s", s.name, exc_info=True)

    # Clean up worker tmp directory
    if s.mp_path:
        from pathlib import Path
        worker_dir = Path(s.mp_path)
        if worker_dir.exists() and "tmp" in str(worker_dir):
            try:
                shutil.rmtree(worker_dir)
                logger.info("Removed worker directory %s for session %s", worker_dir, s.name)
            except Exception:
                logger.warning("Could not remove worker directory %s", worker_dir, exc_info=True)

    repo.delete_session(db, session_id)
    return {"ok": True}


@router.post("/sessions/{session_id}/send")
def send_message(session_id: str, body: SendMessage, request: Request, db=Depends(get_db)):
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    from orchestrator.terminal.session import send_to_session
    config = getattr(request.app.state, "config", {})
    tmux_session = config.get("tmux", {}).get("session_name", "orchestrator")

    success = send_to_session(s.name, body.message, tmux_session)
    if not success:
        raise HTTPException(500, "Failed to send message")
    return {"ok": True, "session": s.name}


@router.get("/sessions/{session_id}/preview")
def session_preview(session_id: str, lines: int = 30, db=Depends(get_db)):
    """Return a plain-text terminal snapshot for a worker session."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    if not s.tmux_window:
        return {"content": "", "status": s.status}

    # Parse tmux target from stored window reference
    if ":" in s.tmux_window:
        tmux_sess, tmux_win = s.tmux_window.split(":", 1)
    else:
        tmux_sess, tmux_win = "orchestrator", s.tmux_window

    try:
        content = capture_output(tmux_sess, tmux_win, lines=lines)
    except Exception:
        logger.warning("Could not capture preview for session %s", s.name, exc_info=True)
        content = ""

    return {"content": content, "status": s.status}


@router.post("/sessions/{session_id}/stop")
def stop_session(session_id: str, db=Depends(get_db)):
    """Gracefully stop a worker session (send Ctrl-C x3, mark disconnected)."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    if not s.tmux_window:
        repo.update_session(db, session_id, status="disconnected")
        return {"ok": True, "message": "No tmux window, marked as disconnected"}

    if ":" in s.tmux_window:
        tmux_sess, tmux_win = s.tmux_window.split(":", 1)
    else:
        tmux_sess, tmux_win = "orchestrator", s.tmux_window

    import time
    try:
        for _ in range(3):
            send_keys(tmux_sess, tmux_win, "C-c", enter=False)
            time.sleep(0.3)
    except Exception:
        logger.warning("Could not send Ctrl-C to session %s", s.name, exc_info=True)

    repo.update_session(db, session_id, status="disconnected")
    return {"ok": True, "message": f"Session {s.name} stopped"}
