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
from orchestrator.worker.cli_scripts import generate_worker_scripts, generate_hooks_settings, get_path_export_command

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
                    task_id=body.task_id,
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
        # Load worker prompt template
        source_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        template_src = os.path.join(source_root, "prompts", "worker_claude_template.md")
        worker_prompt = None
        config = getattr(request.app.state, "config", {})
        api_port = config.get("server", {}).get("port", 8093)
        
        # Generate CLI scripts in tmp_dir/bin/
        bin_dir = generate_worker_scripts(
            worker_dir=tmp_dir,
            worker_name=sanitized_name,
            session_id=s.id,
            api_base=f"http://127.0.0.1:{api_port}",
        )
        logger.info("Generated CLI scripts for local worker %s in %s", sanitized_name, bin_dir)
        
        # Generate Claude Code hooks in tmp_dir/configs/
        configs_dir = os.path.join(tmp_dir, "configs")
        os.makedirs(configs_dir, exist_ok=True)
        settings_path = generate_hooks_settings(
            worker_dir=configs_dir,
            session_id=s.id,
            api_base=f"http://127.0.0.1:{api_port}",
        )
        logger.info("Generated hooks settings for local worker %s in %s", sanitized_name, settings_path)
        
        if os.path.exists(template_src):
            try:
                with open(template_src) as f:
                    worker_prompt = f.read().replace("SESSION_ID", s.id)
            except Exception:
                logger.warning("Could not read worker prompt for %s", sanitized_name, exc_info=True)

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
        tasks_repo.update_task(db, task.id, assigned_session_id=None, status="todo")

    repo.update_session(db, session_id, status="idle")
    return {"ok": True, "message": f"Session {s.name} stopped and cleared"}


@router.post("/sessions/{session_id}/reconnect")
def reconnect_session(session_id: str, request: Request, db=Depends(get_db)):
    """Reconnect a disconnected worker session.
    
    For rdev workers: re-establish SSH/tunnel if needed, then launch Claude with -c flag.
    For local workers: just relaunch Claude with -c flag.
    Sets status to paused after reconnecting, ready for continue.
    """
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    if s.status != "disconnected":
        return {"ok": False, "error": f"Session is not disconnected (status: {s.status})"}

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
                _reconnect_rdev_worker(
                    bg_conn, s, tmux_sess, tmux_win, api_port, tmp_dir
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
        try:
            _reconnect_local_worker(s, tmux_sess, tmux_win, api_port, tmp_dir)
            repo.update_session(db, session_id, status="paused")
            return {"ok": True, "message": f"Session {s.name} reconnected and paused"}
        except Exception as e:
            logger.exception("Local reconnect failed for %s", s.name)
            return {"ok": False, "error": str(e)}


def _check_tunnel_alive(tmux_sess: str, tunnel_win: str) -> bool:
    """Check if the tunnel window has an active SSH tunnel running.
    
    Sends a test command to check if the SSH process is still alive.
    A dead tunnel will show error messages or no response.
    """
    try:
        # First check if window exists and has content
        output = capture_output(tmux_sess, tunnel_win, lines=10)
        if not output:
            logger.info("Tunnel check: no output from window")
            return False
        
        # Check for common SSH failure indicators
        error_indicators = [
            "Connection closed",
            "Connection refused", 
            "Connection timed out",
            "Connection reset",
            "broken pipe",
            "Host key verification failed",
            "Permission denied",
        ]
        output_lower = output.lower()
        for indicator in error_indicators:
            if indicator.lower() in output_lower:
                logger.info("Tunnel check: found error indicator '%s'", indicator)
                return False
        
        # If the window shows ssh command running (no shell prompt), tunnel is likely alive
        # A dead tunnel would typically show an error or return to shell prompt
        logger.info("Tunnel check: appears alive")
        return True
    except Exception as e:
        logger.warning("Tunnel check failed: %s", e)
        return False


def _parse_hostname_from_output(output: str, start_marker: str, end_marker: str) -> str | None:
    """Extract hostname from captured terminal output between markers.
    
    The output includes the command line itself, so we need to find markers
    that appear at the START of a line (the actual echo output), not within
    the command line.
    
    Returns the hostname string or None if parsing failed.
    """
    # Split into lines and find markers at line start
    lines = output.split('\n')
    start_line_idx = None
    end_line_idx = None
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == start_marker:
            start_line_idx = i
        elif stripped == end_marker and start_line_idx is not None:
            end_line_idx = i
            break
    
    if start_line_idx is None or end_line_idx is None:
        return None
    
    # Extract lines between markers
    hostname_lines = [l.strip() for l in lines[start_line_idx + 1:end_line_idx] if l.strip()]
    if hostname_lines:
        return hostname_lines[0]
    return None


def _check_ssh_alive(tmux_sess: str, worker_win: str, host: str) -> bool:
    """Check if the SSH session in worker window is still alive by testing hostname.
    
    Sends 'hostname' command and checks if the HOSTNAME LINE (not other output) 
    contains 'rdev-' prefix indicating we're connected to an rdev VM.
    """
    try:
        import random
        marker_id = random.randint(10000, 99999)
        start_marker = f"SSH_START_{marker_id}"
        end_marker = f"SSH_END_{marker_id}"
        
        # Send command with markers around hostname
        cmd = f"echo {start_marker} && hostname && echo {end_marker}"
        send_keys(tmux_sess, worker_win, cmd, enter=True)
        time.sleep(1.5)
        
        output = capture_output(tmux_sess, worker_win, lines=15)
        logger.debug("SSH alive check output: %s", output)
        
        # Parse hostname from output (handles command line being included)
        hostname = _parse_hostname_from_output(output, start_marker, end_marker)
        
        if hostname is None:
            logger.info("SSH alive check: couldn't parse hostname from output")
            return False
        
        logger.info("SSH alive check: hostname='%s'", hostname)
        
        # rdev hostnames have "rdev-" prefix
        if hostname.lower().startswith("rdev-"):
            return True
        else:
            logger.info("SSH alive check: hostname doesn't start with 'rdev-', not connected")
            return False
    except Exception as e:
        logger.warning("SSH alive check failed: %s", e)
        return False


def _build_system_prompt(session_id: str) -> str | None:
    """Build the system prompt from template, same as new worker setup."""
    import shlex
    
    # Path: sessions.py -> routes -> api -> orchestrator -> orchestrator (project root)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    template_path = os.path.join(project_root, "prompts", "worker_claude_template.md")
    
    if not os.path.exists(template_path):
        logger.warning("Worker template not found at %s", template_path)
        return None
    
    with open(template_path) as f:
        template = f.read()
    
    return shlex.quote(template.replace("SESSION_ID", session_id))


def _reconnect_rdev_worker(conn, session, tmux_sess: str, tmux_win: str, api_port: int, tmp_dir: str):
    """Reconnect an rdev worker: check/restore tunnel and SSH, then launch claude.
    
    Uses same claude command as new workers (--session-id auto-resumes existing sessions).
    """
    from orchestrator.terminal import ssh
    from orchestrator.worker.cli_scripts import get_path_export_command
    
    tunnel_name = f"{session.name}-tunnel"
    remote_tmp_dir = f"/tmp/orchestrator/workers/{session.name}"
    
    # 1. Check/restore tunnel
    tunnel_alive = False
    logger.info("Reconnect %s: checking tunnel (tunnel_pane=%s)", session.name, session.tunnel_pane)
    if session.tunnel_pane:
        if ":" in session.tunnel_pane:
            t_sess, t_win = session.tunnel_pane.split(":", 1)
        else:
            t_sess, t_win = tmux_sess, session.tunnel_pane
        tunnel_alive = _check_tunnel_alive(t_sess, t_win)
        logger.info("Reconnect %s: tunnel alive=%s", session.name, tunnel_alive)
    else:
        logger.info("Reconnect %s: no tunnel_pane stored, will create new tunnel", session.name)
    
    if not tunnel_alive:
        logger.info("Reconnect %s: re-establishing tunnel", session.name)
        # Kill old tunnel window if it exists
        if session.tunnel_pane:
            try:
                if ":" in session.tunnel_pane:
                    t_sess, t_win = session.tunnel_pane.split(":", 1)
                else:
                    t_sess, t_win = tmux_sess, session.tunnel_pane
                kill_window(t_sess, t_win)
                logger.info("Reconnect %s: killed old tunnel window %s", session.name, session.tunnel_pane)
            except Exception as e:
                logger.info("Reconnect %s: failed to kill old tunnel window: %s", session.name, e)
        
        # Create new tunnel
        from orchestrator.terminal.manager import create_window
        logger.info("Reconnect %s: creating tunnel window %s", session.name, tunnel_name)
        create_window(tmux_sess, tunnel_name)
        logger.info("Reconnect %s: setting up SSH tunnel to %s", session.name, session.host)
        ssh.setup_rdev_tunnel(tmux_sess, tunnel_name, session.host, api_port, api_port)
        time.sleep(3)
        repo.update_session(conn, session.id, tunnel_pane=f"{tmux_sess}:{tunnel_name}")
        logger.info("Reconnect %s: tunnel created and saved", session.name)
    
    # 2. Check/restore SSH connection
    ssh_alive = _check_ssh_alive(tmux_sess, tmux_win, session.host)
    logger.info("SSH for %s alive: %s", session.name, ssh_alive)
    
    if not ssh_alive:
        logger.info("Re-establishing SSH for %s", session.name)
        ssh.rdev_connect(tmux_sess, tmux_win, session.host)
        if not ssh.wait_for_prompt(tmux_sess, tmux_win, timeout=30):
            raise RuntimeError(f"Timed out waiting for shell prompt on {session.host}")
    
    # 3. Export PATH and cd to work_dir
    path_export = get_path_export_command(f"{remote_tmp_dir}/bin")
    send_keys(tmux_sess, tmux_win, path_export, enter=True)
    time.sleep(0.3)
    
    if session.work_dir:
        send_keys(tmux_sess, tmux_win, f"cd {session.work_dir}", enter=True)
        time.sleep(0.3)
    
    # 4. Launch Claude with -r to resume existing session
    settings_file = f"{remote_tmp_dir}/configs/settings.json"
    claude_args = [
        f"-r {session.id}",  # Resume existing session
        f"--settings {settings_file}",
        "--dangerously-skip-permissions",
    ]
    
    system_prompt = _build_system_prompt(session.id)
    if system_prompt:
        claude_args.append(f"--append-system-prompt {system_prompt}")
    
    claude_cmd = f"claude {' '.join(claude_args)}"
    send_keys(tmux_sess, tmux_win, claude_cmd, enter=True)
    logger.info("Launched Claude Code for rdev worker %s (session_id=%s)", session.name, session.id)
    
    repo.update_session(conn, session.id, status="paused")


def _reconnect_local_worker(session, tmux_sess: str, tmux_win: str, api_port: int, tmp_dir: str):
    """Reconnect a local worker: cd to work_dir and relaunch claude.
    
    Uses same claude command as new workers (--session-id auto-resumes existing sessions).
    """
    import shlex
    from orchestrator.worker.cli_scripts import get_path_export_command
    
    # 1. Export PATH and cd to work_dir
    path_export = get_path_export_command(os.path.join(tmp_dir, "bin"))
    send_keys(tmux_sess, tmux_win, path_export, enter=True)
    time.sleep(0.3)
    
    if session.work_dir:
        send_keys(tmux_sess, tmux_win, f"cd {shlex.quote(session.work_dir)}", enter=True)
        time.sleep(0.3)
    
    # 2. Launch Claude with -r to resume existing session
    settings_file = os.path.join(tmp_dir, "configs", "settings.json")
    claude_args = [
        f"-r {session.id}",  # Resume existing session
        f"--settings {shlex.quote(settings_file)}",
    ]
    
    system_prompt = _build_system_prompt(session.id)
    if system_prompt:
        claude_args.append(f"--append-system-prompt {system_prompt}")
    
    claude_cmd = f"claude {' '.join(claude_args)}"
    send_keys(tmux_sess, tmux_win, claude_cmd, enter=True)
    logger.info("Launched Claude Code for local worker %s (session_id=%s)", session.name, session.id)


def _check_claude_process_local(session_id: str) -> tuple[bool, str]:
    """Check if Claude Code with given session_id is running locally via ps | grep.
    
    Returns (alive: bool, reason: str)
    """
    import subprocess
    
    try:
        # Run ps | grep directly on local machine
        # Just check for the unique session ID in any claude process
        result = subprocess.run(
            ["bash", "-c", f"ps aux | grep claude | grep -v grep | grep '{session_id}'"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0 and result.stdout.strip():
            return True, "Claude process found via ps"
        else:
            return False, "Claude process not found via ps"
    except subprocess.TimeoutExpired:
        return True, "Health check timed out"
    except Exception as e:
        logger.warning("Health check ps command failed: %s", e)
        return True, f"Health check error: {e}"


def _check_claude_process_rdev(host: str, session_id: str) -> tuple[bool, str]:
    """Check if Claude Code with given session_id is running on rdev host via SSH.
    
    Returns (alive: bool, reason: str)
    """
    import subprocess
    
    try:
        # Run ps aux on remote host via regular SSH
        # Host format is like "subs-mt/sleepy-franklin" which SSH understands
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "ps aux"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            # SSH failed - could mean VM is down or network issue
            stderr = result.stderr.strip()
            if "Connection refused" in stderr or "Connection closed" in stderr or "Connection timed out" in stderr:
                return False, f"SSH connection failed: {stderr}"
            if "Permission denied" in stderr:
                return True, f"SSH auth issue (worker may still be alive): {stderr}"
            return True, f"SSH check inconclusive: {stderr}"
        
        # Check if our session ID is in the process list
        if session_id in result.stdout and "claude" in result.stdout.lower():
            return True, "Claude process found via SSH"
        else:
            return False, "Claude process not found via SSH"
    except subprocess.TimeoutExpired:
        return False, "SSH connection timed out - host may be unreachable"
    except Exception as e:
        logger.warning("Health check SSH command failed: %s", e)
        return True, f"Health check error: {e}"


@router.post("/sessions/{session_id}/health-check")
def health_check_session(session_id: str, db=Depends(get_db)):
    """Check if a worker's Claude Code process is still running.
    
    Uses `ps | grep --session-id <session_id>` to reliably detect if the
    Claude Code process is running:
    - For local workers: runs ps directly on local machine
    - For rdev workers: runs ps via `rdev ssh` on the remote host
    
    Updates status to 'disconnected' if not running.
    
    Returns:
        {"alive": bool, "status": str, "reason": str}
    """
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    if not s.tmux_window:
        return {"alive": False, "status": s.status, "reason": "No tmux window"}

    # Check if rdev or local worker
    if is_rdev_host(s.host):
        alive, reason = _check_claude_process_rdev(s.host, session_id)
    else:
        alive, reason = _check_claude_process_local(session_id)
    
    if not alive:
        if s.status != "disconnected":
            repo.update_session(db, session_id, status="disconnected")
            logger.info("Health check: %s marked as disconnected (%s)", s.name, reason)
        return {"alive": False, "status": "disconnected", "reason": reason}
    
    return {"alive": True, "status": s.status, "reason": reason}


@router.post("/sessions/health-check-all")
def health_check_all_sessions(db=Depends(get_db)):
    """Run health check on all active worker sessions.
    
    Uses `ps | grep --session-id` to reliably check if Claude Code is running:
    - For local workers: runs ps directly on local machine
    - For rdev workers: runs ps via `rdev ssh` on the remote host
    
    Updates disconnected workers automatically.
    
    Returns:
        {"checked": int, "disconnected": list[str], "alive": list[str]}
    """
    sessions = repo.list_sessions(db, session_type="worker")
    
    results = {"checked": 0, "disconnected": [], "alive": []}
    
    for s in sessions:
        if s.status == "disconnected":
            continue  # Skip already disconnected workers
            
        if not s.tmux_window:
            continue
            
        results["checked"] += 1

        try:
            # Check if rdev or local worker
            if is_rdev_host(s.host):
                alive, reason = _check_claude_process_rdev(s.host, s.id)
            else:
                alive, reason = _check_claude_process_local(s.id)
            
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
