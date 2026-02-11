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
    ensure_window,
    kill_window,
    send_keys,
)
from orchestrator.terminal.ssh import is_rdev_host
from orchestrator.agents import deploy_worker_scripts, generate_worker_hooks, get_path_export_command, get_worker_prompt

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
    """
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    # Allow reconnect from disconnected, screen_detached, or error states
    reconnectable_states = ("disconnected", "screen_detached", "error")
    if s.status not in reconnectable_states:
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
    
    A dead tunnel will show error messages OR return to shell prompt.
    An alive tunnel shows no output (SSH is blocking, waiting for connection).
    """
    try:
        # Capture tunnel window output
        output = capture_output(tmux_sess, tunnel_win, lines=10)
        if not output:
            logger.info("Tunnel check: no output from window - assuming dead")
            return False
        
        output_lower = output.lower()
        logger.debug("Tunnel check output: %s", output[:200])
        
        # Check for common SSH failure indicators
        error_indicators = [
            "Connection closed",
            "Connection refused", 
            "Connection timed out",
            "Connection reset",
            "broken pipe",
            "Host key verification failed",
            "Permission denied",
            "Could not resolve hostname",
            "Network is unreachable",
        ]
        for indicator in error_indicators:
            if indicator.lower() in output_lower:
                logger.info("Tunnel check: found error indicator '%s'", indicator)
                return False
        
        # Check for shell prompt - indicates tunnel command has exited
        # Common prompts: $, %, >, bash-x.x$, [user@host]$
        lines = output.strip().split('\n')
        last_line = lines[-1].strip() if lines else ""
        
        # Shell prompt patterns (tunnel exited, back to shell)
        shell_prompt_indicators = ['$ ', '% ', '> ', 'bash-', '# ']
        for prompt in shell_prompt_indicators:
            if last_line.endswith(prompt.strip()) or prompt in last_line:
                # Check if it's just a shell prompt (tunnel exited)
                # vs ssh command still running (which wouldn't show prompt)
                if not ('ssh' in output_lower and '-L' in output):
                    logger.info("Tunnel check: shell prompt detected, tunnel likely dead: '%s'", last_line)
                    return False
        
        # If output contains active SSH tunnel command and no errors, likely alive
        if 'ssh' in output_lower and ('-L' in output or '-R' in output):
            logger.info("Tunnel check: SSH tunnel command visible, appears alive")
            return True
        
        # Fallback: if we can't determine, check if there's any recent activity
        # A hanging SSH tunnel shows minimal output
        logger.info("Tunnel check: uncertain status, assuming alive (output: %s)", last_line[:50])
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


def _check_ssh_alive(tmux_sess: str, worker_win: str, host: str, retries: int = 2) -> bool:
    """Check if the SSH session in worker window is still alive by testing hostname.
    
    Sends 'hostname' command and checks if the HOSTNAME LINE (not other output) 
    contains 'rdev-' prefix indicating we're connected to an rdev VM.
    
    Retries a few times in case the shell is still loading.
    """
    import random
    
    for attempt in range(retries):
        try:
            marker_id = random.randint(10000, 99999)
            start_marker = f"SSH_START_{marker_id}"
            end_marker = f"SSH_END_{marker_id}"
            
            # Send command with markers around hostname
            cmd = f"echo {start_marker} && hostname && echo {end_marker}"
            send_keys(tmux_sess, worker_win, cmd, enter=True)
            time.sleep(2)  # Increased from 1.5s
            
            output = capture_output(tmux_sess, worker_win, lines=15)
            logger.debug("SSH alive check output (attempt %d): %s", attempt + 1, output)
            
            # Parse hostname from output (handles command line being included)
            hostname = _parse_hostname_from_output(output, start_marker, end_marker)
            
            if hostname is None:
                logger.info("SSH alive check: couldn't parse hostname from output (attempt %d)", attempt + 1)
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                return False
            
            logger.info("SSH alive check: hostname='%s'", hostname)
            
            # rdev hostnames have "rdev-" prefix
            if hostname.lower().startswith("rdev-"):
                return True
            else:
                logger.info("SSH alive check: hostname doesn't start with 'rdev-', not connected")
                return False
        except Exception as e:
            logger.warning("SSH alive check failed (attempt %d): %s", attempt + 1, e)
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return False
    
    return False


def _build_system_prompt(session_id: str) -> str | None:
    """Build the system prompt from template, same as new worker setup."""
    import shlex
    
    prompt = get_worker_prompt(session_id)
    if prompt is None:
        logger.warning("Worker prompt template not found")
        return None
    
    return shlex.quote(prompt)


def _check_screen_exists_via_tmux(tmux_sess: str, tmux_win: str, screen_name: str, session_id: str) -> tuple[bool, bool]:
    """Check if screen session exists and if Claude is running inside it.
    
    Sends commands via tmux to check screen status on the remote host.
    Uses unique markers to parse only the actual output, not the command itself.
    
    Args:
        screen_name: The screen session name (e.g., "claude-{session_id}")
        session_id: The orchestrator session ID (used to find Claude process)
    
    Returns (screen_exists: bool, claude_running: bool)
    """
    import random
    marker_id = random.randint(10000, 99999)
    start_marker = f"__SCRCHK_START_{marker_id}__"
    end_marker = f"__SCRCHK_END_{marker_id}__"
    
    # Use unique markers to identify output section
    # The markers won't appear in the command echo because they're dynamically generated
    check_cmd = (
        f"echo {start_marker} && "
        f"(screen -ls 2>/dev/null | grep -q '{screen_name}' && echo SCREEN_EXISTS || echo SCREEN_MISSING) && "
        f"(ps aux | grep -v grep | grep '{session_id}' | grep -i claude > /dev/null && echo CLAUDE_RUNNING || echo CLAUDE_MISSING) && "
        f"echo {end_marker}"
    )
    
    send_keys(tmux_sess, tmux_win, check_cmd, enter=True)
    time.sleep(1.5)
    
    output = capture_output(tmux_sess, tmux_win, lines=20)
    
    # Parse output between markers to avoid matching command text
    screen_exists = False
    claude_running = False
    
    lines = output.split('\n')
    in_result_section = False
    for line in lines:
        stripped = line.strip()
        if start_marker in stripped:
            in_result_section = True
            continue
        if end_marker in stripped:
            break
        if in_result_section:
            if stripped == "SCREEN_EXISTS":
                screen_exists = True
            elif stripped == "CLAUDE_RUNNING":
                claude_running = True
    
    logger.info("Screen check via tmux: screen_exists=%s, claude_running=%s (output section found: %s)", 
                screen_exists, claude_running, in_result_section)
    return screen_exists, claude_running


def _reconnect_rdev_worker(conn, session, tmux_sess: str, tmux_win: str, api_port: int, tmp_dir: str):
    """Reconnect an rdev worker: check/restore tunnel and SSH, then reattach to screen or launch claude.
    
    Screen-aware reconnection:
    1. Check/restore tunnel
    2. Check/restore SSH connection  
    3. Check if screen session exists with Claude running
       - If screen exists with Claude: reattach with `screen -r`
       - If screen exists but Claude dead: restart Claude in screen
       - If no screen: create new screen and launch Claude
    """
    from orchestrator.terminal import ssh
    
    tunnel_name = f"{session.name}-tunnel"
    remote_tmp_dir = f"/tmp/orchestrator/workers/{session.name}"
    screen_name = _get_screen_session_name(session.id)
    
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
    
    # 2. Check screen/claude status via subprocess SSH first (don't use send_keys - Claude might be running!)
    # This avoids typing commands into Claude if it's still running inside screen
    screen_status, reason = _check_screen_and_claude_rdev(session.host, session.id)
    logger.info("Reconnect %s: screen status via subprocess SSH: %s (%s)", session.name, screen_status, reason)
    
    if screen_status == "alive":
        # Screen exists with Claude running - just need to reattach!
        # But first, check if we're already inside screen (send Ctrl-A d to detach if so)
        logger.info("Reconnect %s: screen session '%s' found with Claude running, preparing to reattach", 
                    session.name, screen_name)
        
        # Send Ctrl-A d to detach from any screen session we might be in
        # This is safe even if we're not in screen (just does nothing)
        send_keys(tmux_sess, tmux_win, "C-a d", enter=False)
        time.sleep(0.5)
        
        # Now reattach to the screen session
        sync_marker = f"__SYNC_BEFORE_REATTACH_{random.randint(10000, 99999)}__"
        send_keys(tmux_sess, tmux_win, f"echo {sync_marker}", enter=True)
        time.sleep(1)
        
        send_keys(tmux_sess, tmux_win, f"screen -r {screen_name}", enter=True)
        repo.update_session(conn, session.id, status="waiting")
        logger.info("Reconnect %s: reattached to screen session - Claude still running!", session.name)
        return
    
    # Screen doesn't exist or Claude not running - need to re-establish SSH and restart
    # First, make sure we're not inside a broken screen session
    send_keys(tmux_sess, tmux_win, "C-a d", enter=False)  # Detach if in screen
    time.sleep(0.5)
    
    # Check if we need to re-establish SSH (try a simple command)
    try:
        test_result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", session.host, "echo SSH_OK"],
            capture_output=True, text=True, timeout=10
        )
        ssh_ok = "SSH_OK" in test_result.stdout
    except Exception:
        ssh_ok = False
    
    if not ssh_ok:
        logger.info("Re-establishing SSH for %s", session.name)
        ssh.rdev_connect(tmux_sess, tmux_win, session.host)
        # Wait for shell prompt with longer timeout (SSH can take a while)
        if not ssh.wait_for_prompt(tmux_sess, tmux_win, timeout=60):
            raise RuntimeError(f"Timed out waiting for shell prompt on {session.host}")
        # Extra wait for shell to be fully ready (bashrc loading, etc.)
        time.sleep(2)
        logger.info("Reconnect %s: SSH connection re-established", session.name)
    
    # 3. Ensure screen is installed (same as setup flow)
    from orchestrator.terminal.session import _install_screen_if_needed, _wait_for_command_completion
    if not _install_screen_if_needed(tmux_sess, tmux_win):
        logger.warning("Reconnect %s: screen not available", session.name)
    
    # 4. Now we can safely check screen status via tmux (we're at bash prompt, not inside Claude)
    screen_exists, claude_running = _check_screen_exists_via_tmux(tmux_sess, tmux_win, screen_name, session.id)
    
    if screen_exists and claude_running:
        # Best case: screen session exists with Claude running - just reattach!
        logger.info("Reconnect %s: screen session '%s' found with Claude running, reattaching", 
                    session.name, screen_name)
        
        # IMPORTANT: Wait and sync before reattaching to avoid race condition
        # Previous commands (SSH check, screen check) may still be buffered/processing
        # If we screen -r too fast, those commands get typed into Claude
        sync_marker = f"__SYNC_BEFORE_REATTACH_{random.randint(10000, 99999)}__"
        send_keys(tmux_sess, tmux_win, f"echo {sync_marker}", enter=True)
        time.sleep(1)
        
        # Verify sync marker appeared (confirms previous commands completed)
        sync_output = capture_output(tmux_sess, tmux_win, lines=10)
        if sync_marker not in sync_output:
            logger.warning("Reconnect %s: sync marker not found, waiting longer", session.name)
            time.sleep(2)
        
        send_keys(tmux_sess, tmux_win, f"screen -r {screen_name}", enter=True)
        repo.update_session(conn, session.id, status="waiting")
        logger.info("Reconnect %s: reattached to screen session - Claude still running!", session.name)
        return
    
    if screen_exists and not claude_running:
        # Screen exists but Claude crashed - kill screen and restart
        logger.info("Reconnect %s: screen session exists but Claude not running, restarting", session.name)
        send_keys(tmux_sess, tmux_win, f"screen -X -S {screen_name} quit 2>/dev/null", enter=True)
        time.sleep(0.5)
    
    # 4. No screen or killed old screen - create new screen and launch Claude
    logger.info("Reconnect %s: creating new screen session and launching Claude", session.name)
    
    # Enter screen session (same as setup flow)
    send_keys(tmux_sess, tmux_win, f"screen -S {screen_name}", enter=True)
    time.sleep(1)
    
    # Export PATH inside screen
    path_export = get_path_export_command(f"{remote_tmp_dir}/bin")
    send_keys(tmux_sess, tmux_win, path_export, enter=True)
    time.sleep(0.3)
    
    # cd to work_dir if specified
    if session.work_dir:
        send_keys(tmux_sess, tmux_win, f"cd {session.work_dir}", enter=True)
        time.sleep(0.3)
    
    # Build Claude command with -r to resume existing Claude session
    settings_file = f"{remote_tmp_dir}/configs/settings.json"
    claude_args = [
        f"-r {session.id}",  # Resume existing Claude session
        f"--settings {settings_file}",
        "--dangerously-skip-permissions",
    ]
    
    system_prompt = _build_system_prompt(session.id)
    if system_prompt:
        claude_args.append(f"--append-system-prompt {system_prompt}")
    
    claude_cmd = f"claude {' '.join(claude_args)}"
    send_keys(tmux_sess, tmux_win, claude_cmd, enter=True)
    logger.info("Reconnect %s: launched Claude in new screen session '%s'", session.name, screen_name)
    
    repo.update_session(conn, session.id, status="paused")


def _reconnect_local_worker(session, tmux_sess: str, tmux_win: str, api_port: int, tmp_dir: str):
    """Reconnect a local worker: cd to work_dir and relaunch claude.
    
    Uses same claude command as new workers (--session-id auto-resumes existing sessions).
    """
    import shlex
    
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


def _get_screen_session_name(session_id: str) -> str:
    """Get the screen session name for a worker session."""
    return f"claude-{session_id}"


def _check_screen_and_claude_rdev(host: str, session_id: str, tmux_sess: str = None, tmux_win: str = None) -> tuple[str, str]:
    """Check screen session and Claude process status on rdev host.
    
    Uses subprocess SSH (fresh connection) to check status. Does NOT use tmux send-keys
    because that would type commands into Claude if it's running.
    
    The tmux_sess and tmux_win parameters are kept for API compatibility but not used.
    
    Returns (status: str, reason: str) where status is one of:
    - "alive": Screen exists and Claude is running
    - "screen_only": Screen exists but Claude not running
    - "screen_detached": SSH connection failed but screen may still be running
    - "dead": No screen session found
    """
    screen_name = _get_screen_session_name(session_id)
    
    # Always check via subprocess SSH - never use send-keys to worker window
    # because Claude might be running there and would receive the commands as input
    try:
        check_cmd = f"screen -ls 2>/dev/null | grep -q '{screen_name}' && echo 'SCREEN_EXISTS' || echo 'NO_SCREEN'; ps aux | grep -v grep | grep '{session_id}' | grep -i claude && echo 'CLAUDE_RUNNING' || echo 'NO_CLAUDE'"
        
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, check_cmd],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0 and "Permission denied" in result.stderr:
            return "screen_detached", f"SSH auth failed - screen may still be running: {result.stderr.strip()}"
        
        if result.returncode != 0 and ("Connection refused" in result.stderr or "Connection timed out" in result.stderr):
            return "screen_detached", f"SSH connection failed - screen may still be running: {result.stderr.strip()}"
        
        output = result.stdout
        screen_exists = "SCREEN_EXISTS" in output
        claude_running = "CLAUDE_RUNNING" in output
        
        if screen_exists and claude_running:
            return "alive", "Screen session exists and Claude is running"
        elif screen_exists and not claude_running:
            return "screen_only", "Screen session exists but Claude not running"
        else:
            return "dead", "No screen session found"
            
    except subprocess.TimeoutExpired:
        return "screen_detached", "SSH connection timed out - screen may still be running"
    except Exception as e:
        logger.warning("Health check SSH command failed: %s", e)
        return "screen_detached", f"Health check error: {e}"


def _check_claude_process_rdev(host: str, session_id: str) -> tuple[bool, str]:
    """Check if Claude Code with given session_id is running on rdev host via SSH.
    
    Returns (alive: bool, reason: str)
    
    Note: This now checks both screen session and Claude process.
    For more detailed status, use _check_screen_and_claude_rdev().
    """
    status, reason = _check_screen_and_claude_rdev(host, session_id)
    
    if status == "alive":
        return True, reason
    elif status == "screen_detached":
        # SSH failed but screen might be running - report as alive to avoid false positives
        return True, reason
    else:
        return False, reason


@router.post("/sessions/{session_id}/health-check")
def health_check_session(session_id: str, db=Depends(get_db)):
    """Check if a worker's Claude Code process is still running.
    
    For rdev workers with screen sessions:
    - Checks both screen session and Claude process
    - Returns screen_detached if SSH fails but screen may be running
    - Returns error if screen exists but Claude is not running
    
    For local workers:
    - Uses ps | grep to check Claude process
    
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
        screen_status, reason = _check_screen_and_claude_rdev(s.host, session_id, tmux_sess, tmux_win)
        
        # Also check tunnel status for rdev workers
        tunnel_alive = False
        if s.tunnel_pane:
            if ":" in s.tunnel_pane:
                t_sess, t_win = s.tunnel_pane.split(":", 1)
            else:
                t_sess, t_win = tmux_sess, s.tunnel_pane
            tunnel_alive = _check_tunnel_alive(t_sess, t_win)
        
        if screen_status == "alive":
            # Screen and Claude both running
            if not tunnel_alive:
                # Claude running but tunnel dead - needs reconnect to restore API connectivity
                reason = f"{reason}, but tunnel is dead (API calls won't work)"
                if s.status not in ("screen_detached", "connecting"):
                    repo.update_session(db, session_id, status="screen_detached")
                    logger.info("Health check: %s has Claude running but tunnel dead, marked screen_detached", s.name)
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
        alive, reason = _check_claude_process_local(session_id)
        
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
    
    results = {"checked": 0, "disconnected": [], "screen_detached": [], "error": [], "alive": []}
    
    for s in sessions:
        if s.status == "disconnected":
            continue  # Skip already disconnected workers
            
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
                screen_status, reason = _check_screen_and_claude_rdev(s.host, s.id, tmux_sess, tmux_win)
                
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
