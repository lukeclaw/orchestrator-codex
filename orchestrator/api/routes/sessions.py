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
    tmux_target,
)
from orchestrator.terminal.ssh import is_rdev_host
from orchestrator.agents import deploy_worker_scripts, generate_worker_hooks, get_path_export_command, get_worker_prompt
from orchestrator.agents.deploy import get_worker_skills_dir
from orchestrator.session import (
    is_reconnectable,
    get_screen_session_name,
    check_claude_process_local,
    check_screen_and_claude_rdev,
    check_ssh_alive,
    check_screen_exists_via_tmux,
    reconnect_rdev_worker,
    reconnect_local_worker,
)
from orchestrator.api.ws_terminal import is_user_active

logger = logging.getLogger(__name__)

router = APIRouter()

WORKER_BASE_DIR = "/tmp/orchestrator/workers"


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


def _time_ago(iso_timestamp: str | None) -> str | None:
    """Convert ISO timestamp to human-readable duration like '5m ago' or '2h ago'.
    
    All timestamps should be UTC (from utc_now_iso()). Legacy timestamps without
    timezone are assumed to be local time and converted.
    """
    if not iso_timestamp:
        return None
    try:
        from datetime import datetime, timezone
        
        # Parse ISO timestamp
        ts = iso_timestamp.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        
        # If no timezone info, assume it's local time (legacy data) and make aware
        if dt.tzinfo is None:
            dt = dt.astimezone()  # Interpret as local, then make aware
        
        now = datetime.now(timezone.utc)
        delta = now - dt.astimezone(timezone.utc)
        seconds = int(delta.total_seconds())
        
        if seconds < 0:
            return "just now"  # Future timestamps (clock skew)
        elif seconds < 60:
            return f"{seconds}s ago"
        elif seconds < 3600:
            return f"{seconds // 60}m ago"
        elif seconds < 86400:
            return f"{seconds // 3600}h ago"
        else:
            return f"{seconds // 86400}d ago"
    except Exception:
        return None


def _serialize_session(s):
    status_age = _time_ago(s.last_status_changed_at)
    return {
        "id": s.id, "name": s.name, "host": s.host,
        "work_dir": s.work_dir,
        "tunnel_pid": s.tunnel_pid,
        "status": s.status, "takeover_mode": s.takeover_mode,
        "created_at": s.created_at,
        "last_status_changed_at": s.last_status_changed_at,
        "status_age": status_age,  # Human-readable: "5m ago", "2h ago"
        "session_type": s.session_type,
        "last_viewed_at": s.last_viewed_at,
    }


def _capture_preview(s) -> str:
    """Capture terminal preview for a session (plain text, ANSI stripped)."""
    tmux_sess, tmux_win = tmux_target(s.name)
    try:
        content = capture_pane_with_escapes(tmux_sess, tmux_win, lines=0)
        return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', content)
    except Exception:
        return ""


@router.get("/sessions")
def list_sessions(
    status: str | None = None,
    session_type: str | None = None,
    include_preview: bool = False,
    db=Depends(get_db),
):
    """List sessions.

    Args:
        status: Filter by session status (idle, working, etc.)
        session_type: Filter by session type (worker, brain, system)
        include_preview: Include terminal preview content for each session
    """
    sessions = repo.list_sessions(db, status=status, session_type=session_type)
    result = [_serialize_session(s) for s in sessions]
    if include_preview:
        for s, data in zip(sessions, result):
            data["preview"] = _capture_preview(s)
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
    tmux_session_name, _ = tmux_target(sanitized_name)
    try:
        ensure_window(tmux_session_name, sanitized_name)
        logger.info("Created tmux window for session %s", sanitized_name)
    except Exception:
        logger.warning("Could not create tmux window for session %s", sanitized_name, exc_info=True)

    # Set up tmp directory for CLI scripts and configs
    tmp_dir = os.path.join(WORKER_BASE_DIR, sanitized_name)
    os.makedirs(tmp_dir, exist_ok=True)

    # work_dir is where Claude runs - user-specified or defaults
    work_dir = body.work_dir  # Can be None, will be set later based on host

    s = repo.create_session(db, sanitized_name, body.host, work_dir)

    if is_rdev_host(body.host):
        # rdev worker — launch full setup in background thread
        # (tunnel, SSH, Claude, prompt delivery takes ~30s)
        config = getattr(request.app.state, "config", {})
        api_port = config.get("server", {}).get("port", 8093)
        db_path = getattr(request.app.state, "db_path", None)
        tunnel_manager = getattr(request.app.state, "tunnel_manager", None)

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
                    tunnel_manager=tunnel_manager,
                )
                if result["ok"]:
                    repo.update_session(
                        bg_conn, s.id,
                        status="working",
                        tunnel_pid=result.get("tunnel_pid"),
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
        # Local worker — deploy scripts and launch claude.
        # The session record is already persisted, so deploy/launch errors are
        # non-fatal: log them but still return success to the client.
        try:
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

            # Write worker prompt to file in tmp_dir (avoids pasting large content through tmux)
            worker_prompt = get_worker_prompt(s.id)
            prompt_file = os.path.join(tmp_dir, "prompt.md")
            if worker_prompt:
                with open(prompt_file, "w") as f:
                    f.write(worker_prompt)
                logger.info("Wrote worker prompt to %s", prompt_file)

            # cd to working directory, export PATH, and launch claude with --settings
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
            claude_args = [
                "--dangerously-skip-permissions",
                f"--settings {shlex.quote(settings_file)}",
            ]

            if worker_prompt:
                claude_args.append(f'--append-system-prompt "$(cat {shlex.quote(prompt_file)})"')

            cmd_parts.append(f"claude {' '.join(claude_args)}")

            cmd = " && ".join(cmd_parts)
            send_keys(tmux_session_name, sanitized_name, cmd, enter=True)
            logger.info("Launched claude for local worker %s (work_dir=%s)", sanitized_name, work_dir)
        except Exception:
            logger.warning("Could not deploy/launch local worker %s", sanitized_name, exc_info=True)

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
def delete_session(session_id: str, request: Request, db=Depends(get_db)):
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    worker_scripts_dir = os.path.join(WORKER_BASE_DIR, s.name)
    is_rdev = is_rdev_host(s.host)

    tmux_sess, tmux_win = tmux_target(s.name)

    # For rdev workers, clean up screen session and remote directory before killing window
    if is_rdev:
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

    # Stop the reverse tunnel subprocess (replaces old tmux window kill)
    tunnel_manager = getattr(request.app.state, "tunnel_manager", None)
    if tunnel_manager:
        try:
            if tunnel_manager.stop_tunnel(session_id):
                logger.info("Stopped tunnel subprocess for session %s", s.name)
        except Exception:
            logger.warning("Could not stop tunnel for session %s", s.name, exc_info=True)

    # Kill the tmux window if it exists
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


class TypeText(BaseModel):
    text: str


@router.post("/sessions/{session_id}/type")
def type_text(session_id: str, body: TypeText, request: Request, db=Depends(get_db)):
    """Inject text into the terminal without pressing Enter.

    Unlike ``/send`` (which submits a complete message), this simply types
    the text into the terminal buffer — matching the brain's WebSocket-based
    paste behaviour so that images can be inserted mid-message.
    """
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    from orchestrator.terminal.manager import send_keys_literal
    config = getattr(request.app.state, "config", {})
    tmux_session = config.get("tmux", {}).get("session_name", "orchestrator")

    success = send_keys_literal(tmux_session, s.name, body.text)
    if not success:
        raise HTTPException(500, "Failed to type text")
    return {"ok": True, "session": s.name}


class PasteImageBody(BaseModel):
    image_data: str  # base64-encoded image (with or without data URL prefix)


@router.post("/sessions/{session_id}/paste-image")
def paste_image_to_session(session_id: str, body: PasteImageBody, db=Depends(get_db)):
    """Save a clipboard image to the worker's tmp dir and return the file path.

    For rdev workers the file is also scp'd to the remote host so Claude Code
    on the remote machine can read it as a local file.
    """
    import base64
    import uuid
    from datetime import datetime

    from orchestrator.terminal.file_sync import get_worker_tmp_dir, sync_file_to_remote

    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    # --- decode image --------------------------------------------------
    raw_data = body.image_data
    file_ext = "png"

    if raw_data.startswith("data:"):
        try:
            header, raw_data = raw_data.split(",", 1)
            mime_part = header.split(";")[0]
            if "/" in mime_part:
                mime_type = mime_part.split("/")[1]
                ext_map = {"png": "png", "jpeg": "jpg", "jpg": "jpg", "gif": "gif", "webp": "webp"}
                file_ext = ext_map.get(mime_type, "png")
        except ValueError:
            pass

    try:
        image_bytes = base64.b64decode(raw_data, validate=True)
    except Exception as e:
        raise HTTPException(400, f"Invalid base64 image data: {e}")

    # --- save locally --------------------------------------------------
    tmp_dir = get_worker_tmp_dir(s.name)
    os.makedirs(tmp_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:6]
    fname = f"clipboard_{timestamp}_{short_id}.{file_ext}"
    local_path = os.path.join(tmp_dir, fname)

    try:
        with open(local_path, "wb") as f:
            f.write(image_bytes)
        logger.info("Saved worker image to %s (%d bytes)", local_path, len(image_bytes))
    except Exception as e:
        logger.exception("Failed to save worker image")
        raise HTTPException(500, f"Failed to save image: {e}")

    # --- sync to rdev if needed ----------------------------------------
    # The remote path is identical to the local path so Claude Code sees the
    # same absolute path regardless of where it runs.
    if is_rdev_host(s.host):
        remote_path = local_path  # same absolute path on remote
        ok = sync_file_to_remote(local_path, s.host, remote_path)
        if not ok:
            raise HTTPException(502, "Failed to sync image to remote worker")

    return {
        "ok": True,
        "file_path": local_path,
        "filename": fname,
        "size": len(image_bytes),
    }


@router.get("/sessions/{session_id}/preview")
def session_preview(session_id: str, db=Depends(get_db)):
    """Return a plain-text terminal snapshot for a worker session."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    return {"content": _capture_preview(s), "status": s.status}


@router.post("/sessions/{session_id}/pause")
def pause_session(session_id: str, db=Depends(get_db)):
    """Pause a worker session (send Escape to claude code, mark as paused)."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    tmux_sess, tmux_win = tmux_target(s.name)

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

    tmux_sess, tmux_win = tmux_target(s.name)

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

    tmux_sess, tmux_win = tmux_target(s.name)

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


@router.post("/sessions/{session_id}/prepare-for-task")
def prepare_session_for_task(session_id: str, db=Depends(get_db)):
    """Prepare a worker session for a new task assignment.
    
    Sends Escape + Ctrl-C to cancel any running terminal commands,
    then sends /clear to reset the Claude Code context.
    
    This should be called before reassigning a worker to a different task.
    """
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    # Check if session is in a connectable state
    disconnected_statuses = {"disconnected", "screen_detached", "error", "connecting"}
    if s.status in disconnected_statuses:
        raise HTTPException(400, f"Session is not connected (status: {s.status})")

    tmux_sess, tmux_win = tmux_target(s.name)

    import time
    try:
        # 1. Send Escape to exit any mode/stop current action
        send_keys(tmux_sess, tmux_win, "Escape", enter=False)
        time.sleep(0.3)
        
        # 2. Send Ctrl-C to cancel any running terminal command
        import subprocess
        subprocess.run(
            ["tmux", "send-keys", "-t", f"{tmux_sess}:{tmux_win}", "C-c"],
            capture_output=True, timeout=2
        )
        time.sleep(0.5)
        
        # 3. Send /clear to reset Claude Code context
        from orchestrator.terminal.manager import send_keys_literal
        send_keys_literal(tmux_sess, tmux_win, "/clear")
        send_keys(tmux_sess, tmux_win, "", enter=True)
        
        logger.info("Prepared session %s for new task assignment", s.name)
    except Exception:
        logger.warning("Could not fully prepare session %s", s.name, exc_info=True)
        # Don't fail - partial preparation is still useful

    return {"ok": True, "message": f"Session {s.name} prepared for new task"}


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

    tmux_sess, tmux_win = tmux_target(s.name)

    config = getattr(request.app.state, "config", {})
    api_port = config.get("server", {}).get("port", 8093)
    tmp_dir = os.path.join(WORKER_BASE_DIR, s.name)

    if is_rdev_host(s.host):
        # rdev worker — check tunnel and SSH, re-establish if needed, then launch claude -c
        db_path = getattr(request.app.state, "db_path", None)
        tunnel_manager = getattr(request.app.state, "tunnel_manager", None)
        repo.update_session(db, session_id, status="connecting")

        def _background_reconnect():
            from orchestrator.state.db import get_connection
            bg_conn = get_connection(db_path) if db_path else db
            try:
                reconnect_rdev_worker(
                    bg_conn, s, tmux_sess, tmux_win, api_port, tmp_dir, repo,
                    tunnel_manager=tunnel_manager,
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
def health_check_session(session_id: str, request: Request, db=Depends(get_db)):
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

    tmux_sess, tmux_win = tmux_target(s.name)

    # Check if rdev or local worker
    if is_rdev_host(s.host):
        # Use detailed screen check for rdev workers
        screen_status, reason = check_screen_and_claude_rdev(s.host, session_id, tmux_sess, tmux_win)

        # Check tunnel via ReverseTunnelManager (deterministic process check)
        tunnel_manager = getattr(request.app.state, "tunnel_manager", None)
        tunnel_alive = tunnel_manager.is_alive(session_id) if tunnel_manager else False

        if screen_status == "alive":
            # Screen and Claude both running
            if not tunnel_alive:
                # Claude running but tunnel dead - auto-restart tunnel
                logger.info("Health check: %s has Claude running but tunnel dead, restarting tunnel", s.name)

                if tunnel_manager:
                    new_pid = tunnel_manager.restart_tunnel(s.id, s.name, s.host)
                    if new_pid:
                        repo.update_session(db, session_id, tunnel_pid=new_pid)
                        logger.info("Health check: %s tunnel restarted (pid=%d)", s.name, new_pid)
                        if s.status in ("screen_detached", "error", "disconnected"):
                            repo.update_session(db, session_id, status="waiting")
                        return {
                            "alive": True,
                            "status": "waiting",
                            "reason": f"{reason}, tunnel was dead but auto-restarted",
                            "screen_status": screen_status,
                            "tunnel_alive": True,
                            "tunnel_reconnected": True,
                        }

                # Tunnel restart failed
                reason = f"{reason}, but tunnel is dead and restart failed"
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
            
        results["checked"] += 1
        tmux_sess, tmux_win = tmux_target(s.name)

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
