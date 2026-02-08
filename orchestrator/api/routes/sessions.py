"""Session CRUD + send/takeover/release + terminal preview."""

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any

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
from orchestrator.worker.cli_scripts import generate_worker_scripts, get_path_export_command

logger = logging.getLogger(__name__)

router = APIRouter()

WORKER_BASE_DIR = "/tmp/orchestrator/workers"

# Cache for rdev list (1 hour TTL)
_rdev_cache: dict[str, Any] = {"data": [], "timestamp": 0}
RDEV_CACHE_TTL = 3600  # 1 hour in seconds


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


def _fetch_rdev_list() -> list[dict]:
    """Fetch rdev list from CLI command."""
    rdevs = []
    try:
        result = subprocess.run(
            ["rdev", "list"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout
        
        # Parse the table output
        # Format: Name | State | Cluster Name | Created | Last Accessed | Server URL
        lines = output.strip().split('\n')
        for line in lines:
            # Skip header, separator lines, and info messages
            if '|' not in line or line.startswith('-') or 'Name' in line and 'State' in line:
                continue
            
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 2:
                name = parts[0].strip()
                state = parts[1].strip() if len(parts) > 1 else ''
                
                # Skip empty or invalid entries
                if not name or '/' not in name:
                    continue
                
                rdevs.append({
                    "name": name,
                    "state": state,
                    "cluster": parts[2].strip() if len(parts) > 2 else '',
                    "created": parts[3].strip() if len(parts) > 3 else '',
                    "last_accessed": parts[4].strip() if len(parts) > 4 else '',
                })
    except subprocess.TimeoutExpired:
        logger.warning("rdev list command timed out")
    except FileNotFoundError:
        logger.warning("rdev command not found")
    except Exception:
        logger.warning("Failed to run rdev list", exc_info=True)
    
    return rdevs


@router.get("/rdevs")
def list_rdevs(refresh: bool = False, db=Depends(get_db)):
    """List available rdev instances and show which ones have workers assigned.
    
    Uses server-side cache with 1 hour TTL. Pass refresh=true to force refresh.
    """
    global _rdev_cache
    
    now = time.time()
    cache_age = now - _rdev_cache["timestamp"]
    
    # Use cache if valid and not forcing refresh
    if not refresh and cache_age < RDEV_CACHE_TTL and _rdev_cache["data"]:
        rdevs = _rdev_cache["data"]
        logger.debug("Using cached rdev list (age: %.0fs)", cache_age)
    else:
        # Fetch fresh data
        rdevs = _fetch_rdev_list()
        _rdev_cache["data"] = rdevs
        _rdev_cache["timestamp"] = now
        logger.info("Refreshed rdev list cache (%d instances)", len(rdevs))
    
    # Always check current session state for in_use status
    sessions = repo.list_sessions(db)
    used_hosts = {s.host for s in sessions}
    
    # Return copies with in_use status (don't modify cache)
    result = []
    for rdev in rdevs:
        item = dict(rdev)
        item["in_use"] = rdev["name"] in used_hosts
        # Find the worker name if in use
        for s in sessions:
            if s.host == rdev["name"]:
                item["worker_name"] = s.name
                break
        result.append(item)
    
    return result


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
        # Local worker — launch claude with worker instructions via --append-system-prompt
        # Load worker prompt template
        source_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        template_src = os.path.join(source_root, "prompts", "worker_claude_template.md")
        worker_prompt = None
        
        # Generate CLI scripts (task_id fetched dynamically by scripts)
        config = getattr(request.app.state, "config", {})
        api_port = config.get("server", {}).get("port", 8093)
        
        bin_dir = generate_worker_scripts(
            worker_dir=worker_dir,
            worker_name=body.name,
            session_id=s.id,
            api_base=f"http://127.0.0.1:{api_port}",
        )
        logger.info("Generated CLI scripts for local worker %s in %s", body.name, bin_dir)
        
        if os.path.exists(template_src):
            try:
                with open(template_src) as f:
                    worker_prompt = f.read().replace("SESSION_ID", s.id)
            except Exception:
                logger.warning("Could not read worker prompt for %s", body.name, exc_info=True)

        # cd to working directory, export PATH, and launch claude
        if tmux_window:
            try:
                import shlex
                # Build command: cd, export PATH, launch claude
                cmd_parts = [f"cd {mp_path}"]
                path_export = get_path_export_command(os.path.join(worker_dir, "bin"))
                cmd_parts.append(path_export)
                
                if worker_prompt:
                    quoted_prompt = shlex.quote(worker_prompt)
                    cmd_parts.append(f"claude --append-system-prompt {quoted_prompt}")
                else:
                    cmd_parts.append("claude")
                
                cmd = " && ".join(cmd_parts)
                send_keys(tmux_session_name, body.name, cmd, enter=True)
                logger.info("Launched claude in %s for local worker %s", mp_path, body.name)
            except Exception:
                logger.warning("Could not launch claude for local worker %s", body.name, exc_info=True)

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

    worker_scripts_dir = os.path.join(WORKER_BASE_DIR, s.name)
    is_rdev = is_rdev_host(s.host)

    # For rdev workers, clean up remote directory before killing window
    if is_rdev and s.tmux_window:
        if ":" in s.tmux_window:
            tmux_sess, tmux_win = s.tmux_window.split(":", 1)
        else:
            tmux_sess, tmux_win = "orchestrator", s.tmux_window
        try:
            # Send Ctrl+C to interrupt any running process, then clean up
            send_keys(tmux_sess, tmux_win, "", enter=False)  # Clear any pending input
            import subprocess
            subprocess.run(
                ["tmux", "send-keys", "-t", f"{tmux_sess}:{tmux_win}", "C-c"],
                capture_output=True, timeout=2
            )
            time.sleep(0.5)
            # Remove remote worker directory
            send_keys(tmux_sess, tmux_win, f"rm -rf {worker_scripts_dir}", enter=True)
            time.sleep(0.5)
            logger.info("Cleaned up remote worker directory %s for session %s", worker_scripts_dir, s.name)
        except Exception:
            logger.warning("Could not clean up remote worker directory for session %s", s.name, exc_info=True)

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

    # Clean up local worker scripts directory
    if os.path.exists(worker_scripts_dir):
        try:
            shutil.rmtree(worker_scripts_dir)
            logger.info("Removed local worker directory %s for session %s", worker_scripts_dir, s.name)
        except Exception:
            logger.warning("Could not remove local worker directory %s", worker_scripts_dir, exc_info=True)

    # Clean up worker mp_path if different from scripts dir and in tmp
    if s.mp_path and s.mp_path != worker_scripts_dir:
        if os.path.exists(s.mp_path) and "/tmp/" in s.mp_path:
            try:
                shutil.rmtree(s.mp_path)
                logger.info("Removed worker mp_path %s for session %s", s.mp_path, s.name)
            except Exception:
                logger.warning("Could not remove worker mp_path %s", s.mp_path, exc_info=True)

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


@router.post("/sessions/{session_id}/pause")
def pause_session(session_id: str, db=Depends(get_db)):
    """Pause a worker session (send Escape to claude code, mark as paused)."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    if not s.tmux_window:
        return {"ok": False, "error": "No tmux window attached"}

    if ":" in s.tmux_window:
        tmux_sess, tmux_win = s.tmux_window.split(":", 1)
    else:
        tmux_sess, tmux_win = "orchestrator", s.tmux_window

    try:
        # Send Escape to pause claude code
        send_keys(tmux_sess, tmux_win, "Escape", enter=False)
    except Exception:
        logger.warning("Could not send Escape to session %s", s.name, exc_info=True)

    repo.update_session(db, session_id, status="paused")
    return {"ok": True, "message": f"Session {s.name} paused"}


@router.post("/sessions/{session_id}/continue")
def continue_session(session_id: str, db=Depends(get_db)):
    """Continue a paused worker session (send 'continue' message)."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    if not s.tmux_window:
        return {"ok": False, "error": "No tmux window attached"}

    if ":" in s.tmux_window:
        tmux_sess, tmux_win = s.tmux_window.split(":", 1)
    else:
        tmux_sess, tmux_win = "orchestrator", s.tmux_window

    try:
        from orchestrator.terminal.manager import send_keys_literal
        # Send "continue" message to claude code
        send_keys_literal(tmux_sess, tmux_win, "continue")
        send_keys(tmux_sess, tmux_win, "", enter=True)
    except Exception:
        logger.warning("Could not send continue to session %s", s.name, exc_info=True)

    repo.update_session(db, session_id, status="working")
    return {"ok": True, "message": f"Session {s.name} continued"}


@router.post("/sessions/{session_id}/stop")
def stop_session(session_id: str, db=Depends(get_db)):
    """Stop a worker session: send Escape, then /clear, unassign task, go to idle."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    if not s.tmux_window:
        repo.update_session(db, session_id, status="idle", current_task_id=None)
        return {"ok": True, "message": "No tmux window, marked as idle"}

    if ":" in s.tmux_window:
        tmux_sess, tmux_win = s.tmux_window.split(":", 1)
    else:
        tmux_sess, tmux_win = "orchestrator", s.tmux_window

    import time
    try:
        # Send Escape to stop current operation
        send_keys(tmux_sess, tmux_win, "Escape", enter=False)
        time.sleep(0.5)
        # Send /clear to reset context
        from orchestrator.terminal.manager import send_keys_literal
        send_keys_literal(tmux_sess, tmux_win, "/clear")
        send_keys(tmux_sess, tmux_win, "", enter=True)
    except Exception:
        logger.warning("Could not send stop commands to session %s", s.name, exc_info=True)

    # Unassign current task if any
    if s.current_task_id:
        from orchestrator.state.repositories import tasks as tasks_repo
        tasks_repo.update_task(db, s.current_task_id, assigned_session_id=None, status="todo")

    repo.update_session(db, session_id, status="idle", current_task_id=None)
    return {"ok": True, "message": f"Session {s.name} stopped and cleared"}
