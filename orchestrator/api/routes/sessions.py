"""Session CRUD + send/takeover/release + terminal preview."""

import logging
import os
import re
import shutil
import threading
import time
from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.session import (
    WORKER_BASE_DIR,
    check_all_workers_health,
    check_and_update_worker_health,
    cleanup_reconnect_lock,
    is_reconnectable,
    trigger_reconnect,
)
from orchestrator.state.repositories import sessions as repo
from orchestrator.terminal.manager import (
    capture_pane_with_escapes,
    ensure_window,
    kill_window,
    send_keys,
    tmux_target,
)
from orchestrator.terminal.ssh import is_remote_host

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_session(db, id_or_name: str):
    """Look up a session by ID first, then fall back to name.

    This allows CLI commands like ``orch-workers show api-worker``
    to work with human-readable names instead of UUIDs.
    """
    s = repo.get_session(db, id_or_name)
    if s is not None:
        return s
    return repo.get_session_by_name(db, id_or_name)


class SessionCreate(BaseModel):
    name: str
    host: str = "localhost"
    work_dir: str | None = None
    task_id: str | None = None


class SessionUpdate(BaseModel):
    status: str | None = None
    takeover_mode: bool | None = None
    claude_session_id: str | None = None


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
        from datetime import datetime

        # Parse ISO timestamp
        ts = iso_timestamp.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)

        # If no timezone info, assume it's local time (legacy data) and make aware
        if dt.tzinfo is None:
            dt = dt.astimezone()  # Interpret as local, then make aware

        now = datetime.now(UTC)
        delta = now - dt.astimezone(UTC)
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
        "id": s.id,
        "name": s.name,
        "host": s.host,
        "work_dir": s.work_dir,
        "tunnel_pid": s.tunnel_pid,
        "status": s.status,
        "takeover_mode": s.takeover_mode,
        "created_at": s.created_at,
        "last_status_changed_at": s.last_status_changed_at,
        "status_age": status_age,  # Human-readable: "5m ago", "2h ago"
        "session_type": s.session_type,
        "last_viewed_at": s.last_viewed_at,
        "auto_reconnect": s.auto_reconnect,
        "rws_pty_id": s.rws_pty_id,
    }


def _write_to_rws_pty(session, data: str) -> bool:
    """Write data to a remote session's RWS PTY. Returns True on success."""
    from orchestrator.terminal.remote_worker_server import get_remote_worker_server

    try:
        rws = get_remote_worker_server(session.host)
        rws.write_to_pty(session.rws_pty_id, data)
        return True
    except RuntimeError:
        logger.warning(
            "Could not write to RWS PTY for session %s",
            session.name,
            exc_info=True,
        )
        return False


def _capture_rws_pty(session, lines: int = 30) -> str:
    """Capture terminal output from a remote session's RWS PTY."""
    from orchestrator.terminal.remote_worker_server import get_remote_worker_server

    try:
        rws = get_remote_worker_server(session.host)
        return rws.capture_pty(session.rws_pty_id, lines=lines)
    except RuntimeError:
        return ""


def _capture_preview(s) -> str:
    """Capture terminal preview for a session (plain text, ANSI stripped)."""
    if is_remote_host(s.host) and s.rws_pty_id:
        return _capture_rws_pty(s)

    tmux_sess, tmux_win = tmux_target(s.name)
    try:
        content = capture_pane_with_escapes(tmux_sess, tmux_win, lines=0)
        return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", content)
    except Exception:
        return ""


# Preview cache: avoids tmux contention during active typing.
# Key: session name, Value: (preview_text, timestamp)
_preview_cache: dict[str, tuple[str, float]] = {}
_PREVIEW_CACHE_TTL = 3.0  # seconds — stale previews are fine during typing


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
        from orchestrator.api.ws_terminal import is_any_session_active

        typing_active = is_any_session_active()
        now = time.time()
        for s, data in zip(sessions, result):
            cached = _preview_cache.get(s.name)
            if typing_active and cached and (now - cached[1]) < _PREVIEW_CACHE_TTL:
                data["preview"] = cached[0]
            else:
                preview = _capture_preview(s)
                _preview_cache[s.name] = (preview, now)
                data["preview"] = preview
    return result


@router.get("/sessions/{session_id}")
def get_session(session_id: str, db=Depends(get_db)):
    s = _resolve_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    # Pre-start RWS daemon for remote sessions so it's ready for file ops / terminal
    if is_remote_host(s.host):
        from orchestrator.terminal.remote_worker_server import ensure_rws_starting

        ensure_rws_starting(s.host)

    return _serialize_session(s)


@router.post("/sessions/{session_id}/viewed")
def record_session_viewed(session_id: str, db=Depends(get_db)):
    """Record that the user viewed this session's detail page."""
    from datetime import datetime

    s = _resolve_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    session_id = s.id
    repo.update_session(db, session_id, last_viewed_at=datetime.now(UTC).isoformat())

    # Pre-start RWS daemon for remote sessions so it's ready when user clicks Terminal
    if is_remote_host(s.host):
        from orchestrator.terminal.remote_worker_server import ensure_rws_starting

        ensure_rws_starting(s.host)

    return {"ok": True}


@router.post("/sessions/{session_id}/auto-reconnect")
def toggle_auto_reconnect(session_id: str, request: Request, db=Depends(get_db)):
    """Toggle auto-reconnect for a worker session.

    When enabled, immediately triggers reconnect if the worker is currently
    disconnected. Future disconnects are handled by the periodic health check.
    """
    s = _resolve_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    session_id = s.id
    new_value = not s.auto_reconnect
    repo.update_session(db, session_id, auto_reconnect=new_value)

    # If enabling and worker is currently in a reconnectable state, reconnect now
    if new_value and is_reconnectable(s.status):
        config = getattr(request.app.state, "config", {})
        api_port = config.get("server", {}).get("port", 8093)
        db_path = getattr(request.app.state, "db_path", None)
        tunnel_manager = getattr(request.app.state, "tunnel_manager", None)

        trigger_reconnect(
            s,
            db,
            db_path=db_path,
            api_port=api_port,
            tunnel_manager=tunnel_manager,
        )

    return {"ok": True, "auto_reconnect": new_value}


def _sanitize_worker_name(name: str) -> str:
    r"""Sanitize worker name to avoid folder structure issues.

    Replaces / and \ with _ since these affect directory paths.
    """
    return re.sub(r"[/\\]", "_", name.strip())


@router.post("/sessions", status_code=201)
def create_session(body: SessionCreate, request: Request, db=Depends(get_db)):
    # Sanitize name to avoid folder structure issues
    sanitized_name = _sanitize_worker_name(body.name)

    # Set up tmp directory for CLI scripts and configs (before tmux window so we can use it as cwd)
    tmp_dir = os.path.join(WORKER_BASE_DIR, sanitized_name)
    os.makedirs(tmp_dir, exist_ok=True)

    # Create tmux window for the session, starting in the worker's tmp dir
    tmux_session_name, _ = tmux_target(sanitized_name)
    try:
        ensure_window(tmux_session_name, sanitized_name, cwd=tmp_dir)
        logger.info("Created tmux window for session %s (cwd=%s)", sanitized_name, tmp_dir)
    except Exception:
        logger.warning("Could not create tmux window for session %s", sanitized_name, exc_info=True)

    # work_dir is where Claude runs - user-specified or defaults
    work_dir = body.work_dir  # Can be None, will be set later based on host

    s = repo.create_session(db, sanitized_name, body.host, work_dir)

    if is_remote_host(body.host):
        # Remote worker — launch full setup in background thread
        # (tunnel, SSH, Claude, prompt delivery takes ~30s)
        config = getattr(request.app.state, "config", {})
        api_port = config.get("server", {}).get("port", 8093)
        db_path = getattr(request.app.state, "db_path", None)
        tunnel_manager = getattr(request.app.state, "tunnel_manager", None)

        repo.update_session(db, s.id, status="connecting")

        # Read custom skills before spawning background thread (DB access from main thread)
        from orchestrator.state.repositories import skills as skills_repo

        remote_custom_skills = skills_repo.list_skills(db, target="worker", enabled_only=True)
        remote_custom_skills_dicts = [
            {"name": sk.name, "description": sk.description, "content": sk.content}
            for sk in remote_custom_skills
        ]
        # Get disabled built-in skill names for filtering during remote setup
        remote_disabled_builtins = {
            name for name, _ in skills_repo.list_disabled_builtin_skills(db, "worker")
        }

        # Read claude update setting before spawning background thread
        from orchestrator.terminal.claude_update import should_update_before_start

        remote_update_before_start = should_update_before_start(db)

        def _background_setup():
            from orchestrator.state.db import get_connection
            from orchestrator.terminal.session import setup_remote_worker

            bg_conn = get_connection(db_path) if db_path else db
            try:
                result = setup_remote_worker(
                    bg_conn,
                    s.id,
                    sanitized_name,
                    body.host,
                    tmux_session_name,
                    api_port,
                    work_dir=work_dir,
                    tmp_dir=tmp_dir,
                    tunnel_manager=tunnel_manager,
                    custom_skills=remote_custom_skills_dicts,
                    disabled_builtin_names=remote_disabled_builtins,
                    update_before_start=remote_update_before_start,
                )
                if result["ok"]:
                    # Detect work_dir if not provided at creation
                    detected_work_dir = work_dir
                    if not detected_work_dir:
                        from orchestrator.api.routes.files import _detect_remote_work_dir

                        time.sleep(3)  # Give Claude a moment to start
                        detected = _detect_remote_work_dir(body.host, s.id)
                        if detected:
                            detected_work_dir = detected
                            logger.info("Detected work_dir for %s: %s", sanitized_name, detected)

                    repo.update_session(
                        bg_conn,
                        s.id,
                        status="working",
                        tunnel_pid=result.get("tunnel_pid"),
                        work_dir=detected_work_dir,
                    )
                    if body.task_id:
                        from orchestrator.state.repositories import tasks

                        tasks.update_task(
                            bg_conn, body.task_id, assigned_session_id=s.id, status="in_progress"
                        )
                    logger.info("Remote worker %s setup complete", sanitized_name)
                else:
                    repo.update_session(bg_conn, s.id, status="error")
                    logger.error(
                        "Remote worker %s setup failed: %s", sanitized_name, result.get("error")
                    )
            except Exception:
                logger.exception("Remote background setup failed for %s", sanitized_name)
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
            from orchestrator.state.repositories import skills as skills_repo
            from orchestrator.terminal.session import setup_local_worker

            config = getattr(request.app.state, "config", {})
            api_port = config.get("server", {}).get("port", 8093)

            custom_skills = skills_repo.list_skills(db, target="worker", enabled_only=True)
            custom_skills_dicts = [
                {"name": sk.name, "description": sk.description, "content": sk.content}
                for sk in custom_skills
            ]
            disabled_builtins = {
                name for name, _ in skills_repo.list_disabled_builtin_skills(db, "worker")
            }

            from orchestrator.terminal.claude_update import should_update_before_start

            local_update_before_start = should_update_before_start(db)

            setup_local_worker(
                db,
                s.id,
                sanitized_name,
                tmux_session=tmux_session_name,
                api_port=api_port,
                work_dir=work_dir,
                tmp_dir=tmp_dir,
                custom_skills=custom_skills_dicts,
                disabled_builtin_names=disabled_builtins,
                update_before_start=local_update_before_start,
            )
            if body.task_id:
                from orchestrator.state.repositories import tasks

                tasks.update_task(db, body.task_id, assigned_session_id=s.id, status="in_progress")
        except Exception:
            logger.warning("Could not deploy/launch local worker %s", sanitized_name, exc_info=True)

        return {"id": s.id, "name": s.name, "status": s.status}


@router.patch("/sessions/{session_id}")
def update_session(session_id: str, body: SessionUpdate, db=Depends(get_db)):
    s = _resolve_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    session_id = s.id

    old_status = s.status
    updated = repo.update_session(
        db,
        session_id,
        status=body.status,
        takeover_mode=body.takeover_mode,
        claude_session_id=body.claude_session_id,
    )

    # Publish event for WebSocket broadcast if status changed
    if body.status and body.status != old_status:
        from orchestrator.core.events import Event, publish

        publish(
            Event(
                type="session.status_changed",
                data={
                    "session_id": session_id,
                    "session_name": s.name,
                    "old_status": old_status,
                    "new_status": body.status,
                },
            )
        )

    return {"id": updated.id, "status": updated.status}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, request: Request, db=Depends(get_db)):
    from orchestrator.session.reconnect import get_reconnect_lock

    s = _resolve_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    session_id = s.id

    # Acquire reconnect lock first to prevent concurrent reconnect from
    # recreating resources we're about to tear down (RC-03).
    lock = get_reconnect_lock(session_id)
    if not lock.acquire(timeout=10):
        logger.warning("delete_session %s: reconnect in progress, waiting timed out", s.name)

    try:
        return _delete_session_inner(s, session_id, request, db)
    finally:
        try:
            lock.release()
        except RuntimeError:
            pass  # Lock wasn't acquired (timeout above)
        cleanup_reconnect_lock(session_id)


def _delete_session_inner(s, session_id: str, request: Request, db):
    worker_scripts_dir = os.path.join(WORKER_BASE_DIR, s.name)
    is_remote = is_remote_host(s.host)

    tmux_sess, tmux_win = tmux_target(s.name)

    # For remote workers, clean up RWS PTY or legacy screen session
    if is_remote and s.rws_pty_id:
        # RWS PTY architecture — destroy PTY via daemon
        try:
            from orchestrator.terminal.remote_worker_server import get_remote_worker_server

            rws = get_remote_worker_server(s.host)
            rws.execute({"action": "pty_destroy", "pty_id": s.rws_pty_id})
            logger.info("Destroyed RWS PTY %s for session %s", s.rws_pty_id, s.name)
        except Exception:
            logger.debug("Could not destroy RWS PTY for session %s", s.name)
    elif is_remote:
        # Legacy screen cleanup
        try:
            import subprocess

            # Send Escape to stop Claude Code if running
            subprocess.run(
                ["tmux", "send-keys", "-t", f"{tmux_sess}:{tmux_win}", "Escape"],
                capture_output=True,
                timeout=2,
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
            logger.info(
                "Cleaned up remote worker directory %s for session %s", worker_scripts_dir, s.name
            )
        except Exception:
            logger.warning(
                "Could not clean up remote resources for session %s", s.name, exc_info=True
            )

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
            logger.info(
                "Removed local worker directory %s for session %s", worker_scripts_dir, s.name
            )
        except Exception:
            logger.warning(
                "Could not remove local worker directory %s", worker_scripts_dir, exc_info=True
            )

    # Close interactive CLI if active (before tunnel cleanup)
    try:
        from orchestrator.terminal.interactive import close_interactive_cli

        close_interactive_cli(session_id, tmux_sess)
    except Exception:
        logger.warning("Could not close interactive CLI for session %s", s.name, exc_info=True)

    # Close browser view if active (before tunnel cleanup)
    try:
        from orchestrator.browser.cdp_proxy import stop_browser_view_sync

        stop_browser_view_sync(session_id, close_tab=True)
    except Exception:
        logger.warning("Could not close browser view for session %s", s.name, exc_info=True)

    # Stop per-worker CDP proxy if running
    try:
        from orchestrator.browser.cdp_worker_proxy import stop_cdp_proxy

        stop_cdp_proxy(session_id)
    except Exception:
        logger.warning("Could not stop CDP proxy for %s", s.name, exc_info=True)

    # Stop remote browser process via RWS daemon (before tunnel cleanup,
    # while the RWS command tunnel is still alive)
    if is_remote:
        try:
            from orchestrator.terminal.remote_worker_server import get_remote_worker_server

            rws = get_remote_worker_server(s.host)
            rws.stop_browser(session_id)
            logger.info("Stopped remote browser for session %s", s.name)
        except Exception:
            logger.debug(
                "Could not stop remote browser for session %s (daemon may be unavailable)",
                s.name,
            )

    # Clean up any SSH port-forward tunnels for this remote host
    if is_remote:
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
    s = _resolve_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    if is_remote_host(s.host) and s.rws_pty_id:
        success = _write_to_rws_pty(s, body.message + "\n")
    else:
        from orchestrator.terminal.manager import TMUX_SESSION
        from orchestrator.terminal.session import send_to_session

        success = send_to_session(s.name, body.message, TMUX_SESSION)
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
    s = _resolve_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    if is_remote_host(s.host) and s.rws_pty_id:
        success = _write_to_rws_pty(s, body.text)
    else:
        from orchestrator.terminal.manager import TMUX_SESSION, send_keys_literal

        success = send_keys_literal(TMUX_SESSION, s.name, body.text)
    if not success:
        raise HTTPException(500, "Failed to type text")
    return {"ok": True, "session": s.name}


@router.post("/sessions/{session_id}/paste-to-pane")
def paste_to_pane_endpoint(session_id: str, body: TypeText, request: Request, db=Depends(get_db)):
    """Paste text into the terminal using bracketed paste mode.

    Uses tmux ``paste-buffer -p`` which wraps text in ``ESC[200~`` …
    ``ESC[201~`` sequences.  TUI apps like Claude Code detect this and
    display the pasted content compactly (e.g. ``[42 lines of text]``)
    instead of echoing every line.
    """
    s = _resolve_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    if is_remote_host(s.host) and s.rws_pty_id:
        bracketed = f"\x1b[200~{body.text}\x1b[201~"
        success = _write_to_rws_pty(s, bracketed)
    else:
        from orchestrator.terminal.manager import TMUX_SESSION, paste_to_pane

        success = paste_to_pane(TMUX_SESSION, s.name, body.text)
    if not success:
        raise HTTPException(500, "Failed to paste text")
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

    s = _resolve_session(db, session_id)
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

    # --- sync to remote if needed ----------------------------------------
    # The remote path is identical to the local path so Claude Code sees the
    # same absolute path regardless of where it runs.
    if is_remote_host(s.host):
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
    s = _resolve_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    return {"content": _capture_preview(s), "status": s.status}


@router.post("/sessions/{session_id}/pause")
def pause_session(session_id: str, db=Depends(get_db)):
    """Pause a worker session (send Escape to claude code, mark as paused)."""
    s = _resolve_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    session_id = s.id

    if is_remote_host(s.host) and s.rws_pty_id:
        _write_to_rws_pty(s, "\x1b")
    else:
        tmux_sess, tmux_win = tmux_target(s.name)
        try:
            send_keys(tmux_sess, tmux_win, "Escape", enter=False)
        except Exception:
            logger.warning("Could not send Escape to session %s", s.name, exc_info=True)

    repo.update_session(db, session_id, status="paused")
    return {"ok": True, "message": f"Session {s.name} paused"}


@router.post("/sessions/{session_id}/continue")
def continue_session(session_id: str, db=Depends(get_db)):
    """Continue a paused worker session (send 'continue' message)."""
    s = _resolve_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    session_id = s.id

    if is_remote_host(s.host) and s.rws_pty_id:
        _write_to_rws_pty(s, "continue\n")
    else:
        tmux_sess, tmux_win = tmux_target(s.name)
        try:
            from orchestrator.terminal.manager import send_keys_literal

            send_keys_literal(tmux_sess, tmux_win, "continue")
            send_keys(tmux_sess, tmux_win, "", enter=True)
        except Exception:
            logger.warning("Could not send continue to session %s", s.name, exc_info=True)

    repo.update_session(db, session_id, status="working")
    return {"ok": True, "message": f"Session {s.name} continued"}


@router.post("/sessions/{session_id}/stop")
def stop_session(session_id: str, db=Depends(get_db)):
    """Stop a worker session: send Escape, then /clear, unassign task, go to idle."""
    s = _resolve_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    session_id = s.id

    if is_remote_host(s.host) and s.rws_pty_id:
        _write_to_rws_pty(s, "\x1b")
        time.sleep(0.5)
        _write_to_rws_pty(s, "/clear\n")
    else:
        tmux_sess, tmux_win = tmux_target(s.name)
        try:
            send_keys(tmux_sess, tmux_win, "Escape", enter=False)
            time.sleep(0.5)
            from orchestrator.terminal.manager import send_keys_literal

            send_keys_literal(tmux_sess, tmux_win, "/clear")
            send_keys(tmux_sess, tmux_win, "", enter=True)
        except Exception:
            logger.warning(
                "Could not send stop commands to session %s",
                s.name,
                exc_info=True,
            )

    # Unassign any tasks assigned to this session
    from orchestrator.state.repositories import tasks as tasks_repo

    assigned_tasks = tasks_repo.list_tasks(db, assigned_session_id=session_id)
    for task in assigned_tasks:
        tasks_repo.update_task(db, task.id, assigned_session_id=None)

    # Close interactive CLI if active
    try:
        from orchestrator.terminal.interactive import close_interactive_cli

        close_interactive_cli(session_id)
    except Exception:
        logger.warning("Could not close interactive CLI for session %s", s.name, exc_info=True)

    repo.update_session(db, session_id, status="idle")
    return {"ok": True, "message": f"Session {s.name} stopped and cleared"}


@router.post("/sessions/{session_id}/prepare-for-task")
def prepare_session_for_task(session_id: str, db=Depends(get_db)):
    """Prepare a worker session for a new task assignment.

    Sends Escape + Ctrl-C to cancel any running terminal commands,
    then sends /clear to reset the Claude Code context.

    This should be called before reassigning a worker to a different task.
    """
    s = _resolve_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    # Check if session is in a connectable state
    disconnected_statuses = {"disconnected", "screen_detached", "error", "connecting"}
    if s.status in disconnected_statuses:
        raise HTTPException(400, f"Session is not connected (status: {s.status})")

    if is_remote_host(s.host) and s.rws_pty_id:
        _write_to_rws_pty(s, "\x1b")
        time.sleep(0.3)
        _write_to_rws_pty(s, "\x03")
        time.sleep(0.5)
        _write_to_rws_pty(s, "/clear\n")
        logger.info("Prepared session %s for new task assignment", s.name)
    else:
        import subprocess

        tmux_sess, tmux_win = tmux_target(s.name)
        try:
            send_keys(tmux_sess, tmux_win, "Escape", enter=False)
            time.sleep(0.3)
            subprocess.run(
                ["tmux", "send-keys", "-t", f"{tmux_sess}:{tmux_win}", "C-c"],
                capture_output=True,
                timeout=2,
            )
            time.sleep(0.5)
            from orchestrator.terminal.manager import send_keys_literal

            send_keys_literal(tmux_sess, tmux_win, "/clear")
            send_keys(tmux_sess, tmux_win, "", enter=True)
            logger.info("Prepared session %s for new task assignment", s.name)
        except Exception:
            logger.warning("Could not fully prepare session %s", s.name, exc_info=True)

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
    s = _resolve_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    # Allow reconnect from disconnected, screen_detached, or error states
    if not is_reconnectable(s.status):
        return {"ok": False, "error": f"Session is not in reconnectable state (status: {s.status})"}

    config = getattr(request.app.state, "config", {})
    api_port = config.get("server", {}).get("port", 8093)
    db_path = getattr(request.app.state, "db_path", None)
    tunnel_manager = getattr(request.app.state, "tunnel_manager", None)

    result = trigger_reconnect(
        s,
        db,
        db_path=db_path,
        api_port=api_port,
        tunnel_manager=tunnel_manager,
    )
    if result.get("ok"):
        if result.get("async"):
            return {"ok": True, "message": f"Reconnecting worker {s.name}..."}
        return {"ok": True, "message": f"Worker {s.name} reconnected"}
    return result


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
    s = _resolve_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    tunnel_manager = getattr(request.app.state, "tunnel_manager", None)
    return check_and_update_worker_health(db, s, tunnel_manager)


@router.post("/sessions/health-check-all")
def health_check_all_sessions(request: Request, db=Depends(get_db)):
    """Run health check on all active worker sessions.

    For rdev workers with screen sessions:
    - Checks both screen session and Claude process
    - Sets screen_detached if SSH fails but screen may be running
    - Sets error if screen exists but Claude crashed

    For local workers:
    - Uses ps | grep to check Claude process

    Updates worker status automatically.
    If a worker has auto_reconnect enabled and is found disconnected,
    automatically triggers reconnection.

    Returns:
        {"checked": int, "disconnected": list[str], "screen_detached": list[str],
         "error": list[str], "alive": list[str], "auto_reconnected": list[str]}
    """
    sessions = repo.list_sessions(db, session_type="worker")

    config = getattr(request.app.state, "config", {})
    api_port = config.get("server", {}).get("port", 8093)
    db_path = getattr(request.app.state, "db_path", None)
    tunnel_manager = getattr(request.app.state, "tunnel_manager", None)

    result = check_all_workers_health(
        db,
        sessions,
        db_path=db_path,
        api_port=api_port,
        tunnel_manager=tunnel_manager,
    )

    # --- Brain tmp dir health check ---
    try:
        from orchestrator.session.health import ensure_brain_tmp_health

        brain_session = repo.get_session_by_name(db, "brain")
        if brain_session and brain_session.status not in ("disconnected",):
            brain_dir = "/tmp/orchestrator/brain"
            api_base = f"http://127.0.0.1:{api_port}"
            brain_health = ensure_brain_tmp_health(brain_dir, api_base=api_base, conn=db)
            result["brain_tmp_health"] = brain_health
            if brain_health.get("regenerated"):
                logger.warning("Health check: brain tmp dir regenerated")
    except Exception:
        logger.debug("Health check: brain tmp check failed", exc_info=True)

    return result


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

    s = _resolve_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    if not is_remote_host(s.host):
        raise HTTPException(400, "Tunnel only supported for remote workers")

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

    s = _resolve_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    # Only allow closing tunnels that belong to this session's host
    success, message = close_tunnel(port, host=s.host)

    return {"ok": success, "message": message}


@router.get("/sessions/{session_id}/tunnels")
def list_session_tunnels(session_id: str, db=Depends(get_db)):
    """List active tunnels for a session (real-time via process scan)."""
    from orchestrator.session.tunnel import get_tunnels_for_host

    s = _resolve_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")

    if not is_remote_host(s.host):
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
