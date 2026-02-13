"""Session CRUD + send/takeover/release + terminal preview."""

import logging
import os
import random
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
    capture_pane_with_escapes,
    ensure_window,
    kill_window,
    send_keys,
)
from orchestrator.terminal.ssh import is_rdev_host
from orchestrator.agents import deploy_worker_scripts, generate_worker_hooks, get_path_export_command, get_worker_prompt
from orchestrator.agents.deploy import get_worker_skills_dir
from orchestrator.session import (
    is_reconnectable,
    get_screen_session_name,
    check_tunnel_alive,
    check_claude_process_local,
    check_screen_and_claude_rdev,
    check_ssh_alive,
    check_screen_exists_via_tmux,
    build_system_prompt,
    reconnect_rdev_worker,
    reconnect_local_worker,
)
from orchestrator.api.ws_terminal import is_user_active

logger = logging.getLogger(__name__)

router = APIRouter()

WORKER_BASE_DIR = "/tmp/orchestrator/workers"

# Cache for rdev list (1 hour TTL)
_rdev_cache: dict[str, Any] = {"data": [], "timestamp": 0}
RDEV_CACHE_TTL = 3600  # 1 hour in seconds


class SessionCreate(BaseModel):
    name: str
    host: str
    work_dir: str | None = None
    task_id: str | None = None


class SessionUpdate(BaseModel):
    status: str | None = None
    takeover_mode: bool | None = None


class SendMessage(BaseModel):
    message: str


def _serialize_session(s):
    return {
        "id": s.id, "name": s.name, "host": s.host,
        "work_dir": s.work_dir, "tmux_window": s.tmux_window,
        "tunnel_pane": s.tunnel_pane,
        "status": s.status, "takeover_mode": s.takeover_mode,
        "created_at": s.created_at, "last_activity": s.last_activity,
        "session_type": s.session_type,
        "last_viewed_at": s.last_viewed_at,
    }


@router.get("/sessions")
def list_sessions(
    status: str | None = None,
    session_type: str | None = None,
    db=Depends(get_db),
):
    """List sessions.
    
    Args:
        status: Filter by session status (idle, working, etc.)
        session_type: Filter by session type (worker, brain, system)
    """
    sessions = repo.list_sessions(db, status=status, session_type=session_type)
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


@router.post("/sessions/{session_id}/viewed")
def record_session_viewed(session_id: str, db=Depends(get_db)):
    """Record that the user viewed this session's detail page."""
    from datetime import datetime, timezone
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    repo.update_session(db, session_id, last_viewed_at=datetime.now(timezone.utc).isoformat())
    return {"ok": True}


def _sanitize_worker_name(name: str) -> str:
    """Sanitize worker name to avoid folder structure issues.
    
    Replaces / and \ with _ since these affect directory paths.
    """
    return re.sub(r'[/\\]', '_', name.strip())


@router.post("/sessions", status_code=201)
def create_session(body: SessionCreate, request: Request, db=Depends(get_db)):
    # Sanitize name to avoid folder structure issues
    sanitized_name = _sanitize_worker_name(body.name)
    
    # Create tmux window for the session
    tmux_session_name = "orchestrator"
    tmux_window = None
    try:
        target = ensure_window(tmux_session_name, sanitized_name)
        tmux_window = target
        logger.info("Created tmux window for session %s: %s", sanitized_name, target)
    except Exception:
        logger.warning("Could not create tmux window for session %s", sanitized_name, exc_info=True)

    # Set up tmp directory for CLI scripts and configs
    tmp_dir = os.path.join(WORKER_BASE_DIR, sanitized_name)
    os.makedirs(tmp_dir, exist_ok=True)
    
    # work_dir is where Claude runs - user-specified or defaults
    work_dir = body.work_dir  # Can be None, will be set later based on host

    s = repo.create_session(db, sanitized_name, body.host, work_dir, tmux_window=tmux_window)

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
                    bg_conn, s.id, sanitized_name, body.host,
                    tmux_session_name, api_port,
                    work_dir=work_dir,
                    tmp_dir=tmp_dir,
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
                    logger.info("rdev worker %s setup complete", sanitized_name)
                else:
                    repo.update_session(bg_conn, s.id, status="error")
                    logger.error("rdev worker %s setup failed: %s", sanitized_name, result.get("error"))
            except Exception:
                logger.exception("rdev background setup failed for %s", sanitized_name)
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
        config = getattr(request.app.state, "config", {})
        api_port = config.get("server", {}).get("port", 8093)
        
        # Deploy CLI scripts in tmp_dir/bin/
        bin_dir = deploy_worker_scripts(
            worker_dir=tmp_dir,
            session_id=s.id,
            api_base=f"http://127.0.0.1:{api_port}",
        )
        logger.info("Deployed CLI scripts for local worker %s in %s", sanitized_name, bin_dir)
        
        # Generate Claude Code hooks in tmp_dir/configs/
        configs_dir = os.path.join(tmp_dir, "configs")
        os.makedirs(configs_dir, exist_ok=True)
        generate_worker_hooks(
            worker_dir=configs_dir,
            session_id=s.id,
            api_base=f"http://127.0.0.1:{api_port}",
        )
        logger.info("Generated hooks settings for local worker %s", sanitized_name)
        
        # Deploy worker skills to .claude/commands/ in work_dir
        skills_src = get_worker_skills_dir()
        if skills_src and os.path.isdir(skills_src) and work_dir:
            skills_dest = os.path.join(work_dir, ".claude", "commands")
            os.makedirs(skills_dest, exist_ok=True)
            for skill_file in os.listdir(skills_src):
                if skill_file.endswith(".md"):
                    shutil.copy2(
                        os.path.join(skills_src, skill_file),
                        os.path.join(skills_dest, skill_file),
                    )
            logger.info("Deployed %d skills to %s for local worker %s", 
                       len([f for f in os.listdir(skills_dest) if f.endswith(".md")]), 
                       skills_dest, sanitized_name)
        
        # Load worker prompt
        worker_prompt = get_worker_prompt(s.id)

        # cd to working directory, export PATH, and launch claude with --settings
        if tmux_window:
            try:
                import shlex
                cmd_parts = []
                
                # cd to work_dir if specified, otherwise stay in current dir
                if work_dir:
                    cmd_parts.append(f"cd {work_dir}")
                
                # Export PATH to include CLI scripts
                path_export = get_path_export_command(os.path.join(tmp_dir, "bin"))
                cmd_parts.append(path_export)
                
                # Build claude command with --settings for hooks
                settings_file = os.path.join(tmp_dir, "configs", "settings.json")
                claude_args = [f"--settings {shlex.quote(settings_file)}"]
                
                if worker_prompt:
                    quoted_prompt = shlex.quote(worker_prompt)
                    claude_args.append(f"--append-system-prompt {quoted_prompt}")
                
                cmd_parts.append(f"claude {' '.join(claude_args)}")
                
                cmd = " && ".join(cmd_parts)
                send_keys(tmux_session_name, body.name, cmd, enter=True)
                logger.info("Launched claude for local worker %s (work_dir=%s)", body.name, work_dir)
            except Exception:
                logger.warning("Could not launch claude for local worker %s", body.name, exc_info=True)

        return {"id": s.id, "name": s.name, "status": s.status}


@router.patch("/sessions/{session_id}")
def update_session(session_id: str, body: SessionUpdate, db=Depends(get_db)):
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    
    old_status = s.status
    updated = repo.update_session(
        db, session_id,
        status=body.status,
        takeover_mode=body.takeover_mode,
    )
    
    # Publish event for WebSocket broadcast if status changed
    if body.status and body.status != old_status:
        from orchestrator.core.events import Event, publish
        publish(Event(
            type="session.status_changed",
            data={
                "session_id": session_id,
                "session_name": s.name,
                "old_status": old_status,
                "new_status": body.status,
            },
        ))
    
    return {"id": updated.id, "status": updated.status}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, db=Depends(get_db)):
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    worker_scripts_dir = os.path.join(WORKER_BASE_DIR, s.name)
    is_rdev = is_rdev_host(s.host)

    # For rdev workers, clean up screen session and remote directory before killing window
    if is_rdev and s.tmux_window:
        if ":" in s.tmux_window:
            tmux_sess, tmux_win = s.tmux_window.split(":", 1)
        else:
            tmux_sess, tmux_win = "orchestrator", s.tmux_window
        try:
            import subprocess
            # Send Escape to stop Claude Code if running
            subprocess.run(
                ["tmux", "send-keys", "-t", f"{tmux_sess}:{tmux_win}", "Escape"],
                capture_output=True, timeout=2
            )
            time.sleep(0.3)
            
            # First "exit" exits Claude Code
            send_keys(tmux_sess, tmux_win, "exit", enter=True)
            time.sleep(0.5)
            
            # Second "exit" exits the screen session (terminates it)
            send_keys(tmux_sess, tmux_win, "exit", enter=True)
            time.sleep(0.5)
            logger.info("Exited Claude and screen session for worker %s", s.name)
            
            # Remove remote worker directory
            send_keys(tmux_sess, tmux_win, f"rm -rf {worker_scripts_dir}", enter=True)
            time.sleep(0.5)
            logger.info("Cleaned up remote worker directory %s for session %s", worker_scripts_dir, s.name)
        except Exception:
            logger.warning("Could not clean up remote resources for session %s", s.name, exc_info=True)

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

    # Clean up any SSH port-forward tunnels for this rdev host
    if is_rdev:
        from orchestrator.session.tunnel import cleanup_tunnels_for_host
        try:
            closed = cleanup_tunnels_for_host(s.host)
            if closed > 0:
                logger.info("Cleaned up %d tunnel(s) for session %s", closed, s.name)
        except Exception:
            logger.warning("Could not clean up tunnels for session %s", s.name, exc_info=True)

    # Note: work_dir is NOT cleaned up - it's the user's working directory
    # Only tmp_dir (worker_scripts_dir) is cleaned up above

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
def session_preview(session_id: str, db=Depends(get_db)):
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
        # Capture the current visible pane (not scrollback history) so the
        # preview matches what the live terminal shows.
        content = capture_pane_with_escapes(tmux_sess, tmux_win, lines=0)
        # Strip ANSI escape sequences — the preview renders in a <pre> tag,
        # not a terminal emulator, so it can't interpret them.
        content = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', content)
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
        repo.update_session(db, session_id, status="idle")
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

    # Unassign any tasks assigned to this session
    from orchestrator.state.repositories import tasks as tasks_repo
    assigned_tasks = tasks_repo.list_tasks(db, assigned_session_id=session_id)
    for task in assigned_tasks:
        # Only reset status to todo if task is not already done
        new_status = None if task.status == "done" else "todo"
        tasks_repo.update_task(db, task.id, assigned_session_id=None, status=new_status)

    repo.update_session(db, session_id, status="idle")
    return {"ok": True, "message": f"Session {s.name} stopped and cleared"}


@router.post("/sessions/{session_id}/reconnect")
def reconnect_session(session_id: str, request: Request, db=Depends(get_db)):
    """Reconnect a disconnected or screen_detached worker session.
    
    For rdev workers with screen_detached status:
    - Re-establish SSH/tunnel, then reattach to existing screen session
    - If screen has Claude running, just reattach (fast recovery!)
    
    For rdev workers with disconnected status:
    - Re-establish SSH/tunnel, create new screen, launch Claude
    
    For local workers: just relaunch Claude with -r flag.
    
    Reconnect is always a manual action triggered by user clicking a button,
    so it should never be skipped due to user activity.
    """
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    # Allow reconnect from disconnected, screen_detached, or error states
    if not is_reconnectable(s.status):
        return {"ok": False, "error": f"Session is not in reconnectable state (status: {s.status})"}

    if not s.tmux_window:
        return {"ok": False, "error": "No tmux window attached"}

    if ":" in s.tmux_window:
        tmux_sess, tmux_win = s.tmux_window.split(":", 1)
    else:
        tmux_sess, tmux_win = "orchestrator", s.tmux_window

    config = getattr(request.app.state, "config", {})
    api_port = config.get("server", {}).get("port", 8093)
    tmp_dir = os.path.join(WORKER_BASE_DIR, s.name)

    if is_rdev_host(s.host):
        # rdev worker — check tunnel and SSH, re-establish if needed, then launch claude -c
        db_path = getattr(request.app.state, "db_path", None)
        repo.update_session(db, session_id, status="connecting")

        def _background_reconnect():
            from orchestrator.state.db import get_connection
            bg_conn = get_connection(db_path) if db_path else db
            try:
                reconnect_rdev_worker(
                    bg_conn, s, tmux_sess, tmux_win, api_port, tmp_dir, repo
                )
                logger.info("rdev worker %s reconnected", s.name)
            except Exception as e:
                logger.exception("rdev reconnect failed for %s", s.name)
                try:
                    repo.update_session(bg_conn, s.id, status="disconnected")
                except Exception:
                    pass
            finally:
                if db_path and bg_conn is not db:
                    bg_conn.close()

        thread = threading.Thread(target=_background_reconnect, daemon=True)
        thread.start()
        return {"ok": True, "message": f"Reconnecting rdev worker {s.name}..."}

    else:
        # Local worker — just relaunch claude
        repo.update_session(db, session_id, status="connecting")
        try:
            reconnect_local_worker(s, tmux_sess, tmux_win, api_port, tmp_dir)
            repo.update_session(db, session_id, status="waiting")
            return {"ok": True, "message": f"Session {s.name} reconnected"}
        except Exception as e:
            logger.exception("Local reconnect failed for %s", s.name)
            repo.update_session(db, session_id, status="disconnected")
            return {"ok": False, "error": str(e)}


@router.post("/sessions/{session_id}/health-check")
def health_check_session(session_id: str, db=Depends(get_db)):
    """Check if a worker's Claude Code process is still running.
    
    For rdev workers with screen sessions:
    - Checks both screen session and Claude process
    - Returns screen_detached if SSH fails but screen may be running
    - Returns error if screen exists but Claude is not running
    
    For local workers:
    - Uses ps | grep to check Claude process
    
    Status checks don't lock user input - they just check status without sending commands
    to the worker terminal.
    
    Updates status accordingly.
    
    Returns:
        {"alive": bool, "status": str, "reason": str, "screen_status": str (rdev only)}
    """
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    if not s.tmux_window:
        return {"alive": False, "status": s.status, "reason": "No tmux window"}

    # Parse tmux session and window from stored tmux_window
    if ":" in s.tmux_window:
        tmux_sess, tmux_win = s.tmux_window.split(":", 1)
    else:
        tmux_sess, tmux_win = "orchestrator", s.tmux_window

    # Check if rdev or local worker
    if is_rdev_host(s.host):
        # Use detailed screen check for rdev workers
        screen_status, reason = check_screen_and_claude_rdev(s.host, session_id, tmux_sess, tmux_win)
        
        # Also check tunnel status for rdev workers
        tunnel_alive = False
        if s.tunnel_pane:
            if ":" in s.tunnel_pane:
                t_sess, t_win = s.tunnel_pane.split(":", 1)
            else:
                t_sess, t_win = tmux_sess, s.tunnel_pane
            tunnel_alive = check_tunnel_alive(t_sess, t_win)
        
        if screen_status == "alive":
            # Screen and Claude both running
            if not tunnel_alive:
                # Claude running but tunnel dead - auto-reconnect tunnel without touching main window
                logger.info("Health check: %s has Claude running but tunnel dead, auto-reconnecting tunnel", s.name)
                from orchestrator.session.reconnect import reconnect_tunnel_only
                
                api_port = 8093  # TODO: get from config
                tunnel_reconnected = reconnect_tunnel_only(db, s, tmux_sess, api_port, repo)
                
                if tunnel_reconnected:
                    logger.info("Health check: %s tunnel auto-reconnected successfully", s.name)
                    # Update status back to waiting if it was in error state
                    if s.status in ("screen_detached", "error", "disconnected"):
                        repo.update_session(db, session_id, status="waiting")
                    return {
                        "alive": True,
                        "status": "waiting",
                        "reason": f"{reason}, tunnel was dead but auto-reconnected",
                        "screen_status": screen_status,
                        "tunnel_alive": True,
                        "tunnel_reconnected": True,
                    }
                else:
                    # Tunnel reconnect failed - mark as needing manual reconnect
                    reason = f"{reason}, but tunnel is dead and auto-reconnect failed"
                    if s.status not in ("screen_detached", "connecting"):
                        repo.update_session(db, session_id, status="screen_detached")
                    return {
                        "alive": False,
                        "status": "screen_detached",
                        "reason": reason,
                        "screen_status": screen_status,
                        "tunnel_alive": False,
                        "needs_reconnect": True,
                    }
            # All good - screen, Claude, and tunnel alive
            # If status was screen_detached/error/disconnected, update to waiting (Claude is running)
            if s.status in ("screen_detached", "error", "disconnected"):
                repo.update_session(db, session_id, status="waiting")
                logger.info("Health check: %s recovered from %s to waiting", s.name, s.status)
                return {"alive": True, "status": "waiting", "reason": reason, "screen_status": screen_status, "tunnel_alive": True}
            return {"alive": True, "status": s.status, "reason": reason, "screen_status": screen_status, "tunnel_alive": True}
        elif screen_status == "screen_detached":
            # SSH failed but screen might still be running - this needs reconnect to resume work
            if s.status not in ("screen_detached", "connecting"):
                repo.update_session(db, session_id, status="screen_detached")
                logger.info("Health check: %s marked as screen_detached (%s)", s.name, reason)
            return {
                "alive": False,  # Not usable without reconnect
                "status": "screen_detached", 
                "reason": reason, 
                "screen_status": screen_status,
                "needs_reconnect": True,  # Signal that reconnect can restore this worker
            }
        elif screen_status == "screen_only":
            # Screen exists but Claude crashed - can restart Claude in screen
            if s.status != "error":
                repo.update_session(db, session_id, status="error")
                logger.info("Health check: %s marked as error - Claude crashed in screen (%s)", s.name, reason)
            return {
                "alive": False, 
                "status": "error", 
                "reason": reason, 
                "screen_status": screen_status,
                "needs_reconnect": True,  # Can restart Claude in existing screen
            }
        else:  # dead
            if s.status != "disconnected":
                repo.update_session(db, session_id, status="disconnected")
                logger.info("Health check: %s marked as disconnected (%s)", s.name, reason)
            return {
                "alive": False, 
                "status": "disconnected", 
                "reason": reason, 
                "screen_status": screen_status,
                "needs_reconnect": True,  # Full restart needed
            }
    else:
        alive, reason = check_claude_process_local(session_id)
        
        if not alive:
            if s.status != "disconnected":
                repo.update_session(db, session_id, status="disconnected")
                logger.info("Health check: %s marked as disconnected (%s)", s.name, reason)
            return {"alive": False, "status": "disconnected", "reason": reason, "needs_reconnect": True}
        
        return {"alive": True, "status": s.status, "reason": reason}


@router.post("/sessions/health-check-all")
def health_check_all_sessions(db=Depends(get_db)):
    """Run health check on all active worker sessions.
    
    For rdev workers with screen sessions:
    - Checks both screen session and Claude process
    - Sets screen_detached if SSH fails but screen may be running
    - Sets error if screen exists but Claude crashed
    
    For local workers:
    - Uses ps | grep to check Claude process
    
    Updates worker status automatically.
    
    Returns:
        {"checked": int, "disconnected": list[str], "screen_detached": list[str], 
         "error": list[str], "alive": list[str]}
    """
    sessions = repo.list_sessions(db, session_type="worker")
    
    results = {"checked": 0, "disconnected": [], "screen_detached": [], "error": [], "alive": [], "skipped_active": []}
    
    for s in sessions:
        if s.status == "disconnected":
            continue  # Skip already disconnected workers
        if s.status == "connecting":
            continue  # Skip workers currently connecting (setup in progress)
            
        if not s.tmux_window:
            continue
            
        results["checked"] += 1

        # Parse tmux session and window
        if ":" in s.tmux_window:
            tmux_sess, tmux_win = s.tmux_window.split(":", 1)
        else:
            tmux_sess, tmux_win = "orchestrator", s.tmux_window

        try:
            if is_rdev_host(s.host):
                # Use detailed screen check for rdev workers - pass tmux info to check worker SSH
                screen_status, reason = check_screen_and_claude_rdev(s.host, s.id, tmux_sess, tmux_win)
                
                if screen_status == "alive":
                    results["alive"].append(s.name)
                elif screen_status == "screen_detached":
                    if s.status not in ("screen_detached", "connecting"):
                        repo.update_session(db, s.id, status="screen_detached")
                        logger.info("Health check: %s marked as screen_detached (%s)", s.name, reason)
                    results["screen_detached"].append(s.name)
                elif screen_status == "screen_only":
                    if s.status != "error":
                        repo.update_session(db, s.id, status="error")
                        logger.info("Health check: %s marked as error - Claude crashed (%s)", s.name, reason)
                    results["error"].append(s.name)
                else:  # dead
                    repo.update_session(db, s.id, status="disconnected")
                    results["disconnected"].append(s.name)
                    logger.info("Health check: %s marked as disconnected (%s)", s.name, reason)
            else:
                alive, reason = check_claude_process_local(s.id)
                
                if not alive:
                    repo.update_session(db, s.id, status="disconnected")
                    results["disconnected"].append(s.name)
                    logger.info("Health check: %s marked as disconnected (%s)", s.name, reason)
                else:
                    results["alive"].append(s.name)
        except Exception as e:
            # Can't check - assume alive for now
            logger.warning("Health check failed for %s: %s", s.name, e)
            results["alive"].append(s.name)
    
    return results


# =============================================================================
# Tunnel Management Endpoints
# =============================================================================

class TunnelRequest(BaseModel):
    port: int
    local_port: int | None = None  # Optional: use different local port


@router.post("/sessions/{session_id}/tunnel")
def create_session_tunnel(session_id: str, body: TunnelRequest, db=Depends(get_db)):
    """Create SSH port forward from local machine to rdev worker.
    
    This spawns an SSH tunnel process that forwards a local port to the remote
    rdev host's port, allowing local browser/tools to access services on rdev.
    """
    from orchestrator.session.tunnel import create_tunnel
    
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    if not is_rdev_host(s.host):
        raise HTTPException(400, "Tunnel only supported for rdev workers")
    
    local_port = body.local_port or body.port
    remote_port = body.port
    
    success, result = create_tunnel(s.host, remote_port, local_port)
    
    if not success:
        error_msg = result.get("error", "Unknown error")
        if "already tunneled" in error_msg:
            raise HTTPException(409, error_msg)
        raise HTTPException(500, error_msg)
    
    return {"ok": True, **result}


@router.delete("/sessions/{session_id}/tunnel/{port}")
def close_session_tunnel(session_id: str, port: int, db=Depends(get_db)):
    """Close a specific port tunnel for this session."""
    from orchestrator.session.tunnel import close_tunnel
    
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    
    # Only allow closing tunnels that belong to this session's host
    success, message = close_tunnel(port, host=s.host)
    
    return {"ok": success, "message": message}


@router.get("/sessions/{session_id}/tunnels")
def list_session_tunnels(session_id: str, db=Depends(get_db)):
    """List active tunnels for a session (real-time via process scan)."""
    from orchestrator.session.tunnel import get_tunnels_for_host
    
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    
    if not is_rdev_host(s.host):
        return {"tunnels": {}}
    
    tunnels = get_tunnels_for_host(s.host)
    return {
        "tunnels": {
            str(port): {
                "remote_port": info["remote_port"],
                "pid": info["pid"],
                "host": info["host"],
            }
            for port, info in tunnels.items()
        }
    }


@router.get("/tunnels")
def list_all_tunnels(db=Depends(get_db)):
    """List all active SSH port-forward tunnels (for brain/admin)."""
    from orchestrator.session.tunnel import discover_active_tunnels
    
    tunnels = discover_active_tunnels(force_refresh=True)
    return {"tunnels": tunnels}
