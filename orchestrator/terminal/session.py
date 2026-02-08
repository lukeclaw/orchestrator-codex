"""Full session lifecycle: create, start Claude Code, remove."""

from __future__ import annotations

import logging
import os
import shlex
import sqlite3
import time

from orchestrator.state.models import Session
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.terminal import manager as tmux
from orchestrator.terminal import ssh
from orchestrator.worker.cli_scripts import generate_worker_scripts, get_path_export_command

logger = logging.getLogger(__name__)

_SOURCE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def create_session(
    conn: sqlite3.Connection,
    name: str,
    host: str,
    mp_path: str | None = None,
    tmux_session: str = "orchestrator",
) -> Session:
    """Create a new session: tmux window, SSH, cd to path, persist to DB."""
    # Create tmux window
    target = tmux.create_window(tmux_session, name)

    # If remote, SSH into host
    if host != "local":
        ssh.connect(tmux_session, name, host)
        # Wait a moment for SSH to establish
        time.sleep(2)

    # cd to working directory if specified
    if mp_path:
        tmux.send_keys(tmux_session, name, f"cd {mp_path}")
        time.sleep(0.5)

    # Persist to DB
    session = sessions_repo.create_session(
        conn, name=name, host=host, mp_path=mp_path, tmux_window=target
    )
    logger.info("Created session: %s (host=%s, path=%s)", name, host, mp_path)
    return session


def start_claude_code(
    conn: sqlite3.Connection,
    name: str,
    tmux_session: str = "orchestrator",
) -> bool:
    """Start Claude Code in a session's tmux window."""
    session = sessions_repo.get_session_by_name(conn, name)
    if session is None:
        logger.error("Session not found: %s", name)
        return False

    tmux.send_keys(tmux_session, name, "claude")
    sessions_repo.update_session(conn, session.id, status="working")
    logger.info("Started Claude Code in session: %s", name)
    return True


def remove_session(
    conn: sqlite3.Connection,
    name: str,
    tmux_session: str = "orchestrator",
    kill_window: bool = True,
) -> bool:
    """Remove a session: update DB, optionally kill tmux window."""
    session = sessions_repo.get_session_by_name(conn, name)
    if session is None:
        logger.error("Session not found: %s", name)
        return False

    if kill_window:
        tmux.kill_window(tmux_session, name)

    sessions_repo.delete_session(conn, session.id)
    logger.info("Removed session: %s", name)
    return True


def get_session_output(
    name: str,
    tmux_session: str = "orchestrator",
    lines: int = 50,
) -> str:
    """Get recent terminal output from a session."""
    return tmux.capture_output(tmux_session, name, lines=lines)


def send_to_session(
    name: str,
    message: str,
    tmux_session: str = "orchestrator",
) -> bool:
    """Send a message to a session's Claude Code instance.
    
    Uses literal mode to send text (avoiding tmux special key interpretation),
    then sends Enter separately to submit the message.
    """
    # Send message content in literal mode (no special key interpretation)
    if not tmux.send_keys_literal(tmux_session, name, message):
        return False
    # Send Enter separately to submit
    return tmux.send_keys(tmux_session, name, "", enter=True)


def setup_rdev_worker(
    conn: sqlite3.Connection,
    session_id: str,
    name: str,
    host: str,
    tmux_session: str = "orchestrator",
    api_port: int = 8093,
    task_id: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Set up a full rdev worker: tunnel, SSH, Claude, prompt.

    Returns {"ok": True, "tunnel_window": ...} on success,
    or {"ok": False, "error": "..."} on failure.
    """
    tunnel_name = f"{name}-tunnel"
    worker_dir = f"/tmp/orchestrator/workers/{name}"

    try:
        # 1. Create tunnel window and start reverse SSH tunnel
        tmux.create_window(tmux_session, tunnel_name)
        ssh.setup_rdev_tunnel(tmux_session, tunnel_name, host, api_port, api_port)
        logger.info("Started reverse tunnel for %s -> %s", name, host)
        time.sleep(3)

        # 2. Connect to rdev VM
        ssh.rdev_connect(tmux_session, name, host)
        logger.info("Connecting to rdev VM for %s: %s", name, host)

        # 3. Wait for shell prompt
        if not ssh.wait_for_prompt(tmux_session, name, timeout=30):
            raise RuntimeError(f"Timed out waiting for shell prompt on {host}")

        # 4. Generate CLI scripts (task_id fetched dynamically by scripts)
        # Generate CLI scripts locally first
        os.makedirs(worker_dir, exist_ok=True)
        bin_dir = generate_worker_scripts(
            worker_dir=worker_dir,
            worker_name=name,
            session_id=session_id,
            api_base=f"http://127.0.0.1:{api_port}",
        )
        logger.info("Generated CLI scripts in %s", bin_dir)
        
        # Copy scripts to remote via SSH
        # Create remote directory and copy scripts
        tmux.send_keys(tmux_session, name, f"mkdir -p {worker_dir}/bin", enter=True)
        time.sleep(0.5)
        
        # Copy each script file to remote
        for script_name in ["orch-task", "orch-subtask", "orch-worker", "orch-context"]:
            local_path = os.path.join(bin_dir, script_name)
            if os.path.exists(local_path):
                with open(local_path) as f:
                    script_content = f.read()
                # Use heredoc to write script content
                tmux.send_keys(tmux_session, name, 
                    f"cat > {worker_dir}/bin/{script_name} << 'ORCHEOF'\n{script_content}\nORCHEOF", 
                    enter=True)
                time.sleep(0.3)
                tmux.send_keys(tmux_session, name, f"chmod +x {worker_dir}/bin/{script_name}", enter=True)
                time.sleep(0.2)
        
        # Export PATH
        path_export = get_path_export_command(f"{worker_dir}/bin")
        tmux.send_keys(tmux_session, name, path_export, enter=True)
        time.sleep(0.5)
        logger.info("Copied CLI scripts to remote and updated PATH")

        # 5. Launch Claude with worker prompt via --append-system-prompt
        template_path = os.path.join(_SOURCE_ROOT, "prompts", "worker_claude_template.md")
        
        if os.path.exists(template_path):
            with open(template_path) as f:
                template = f.read()
            rendered = template.replace("SESSION_ID", session_id)
            if task_id:
                rendered = rendered.replace("TASK_ID", task_id)
            if project_id:
                rendered = rendered.replace("PROJECT_ID", project_id)
            
            # Use shlex.quote for safe shell escaping - no tmp files needed
            quoted_prompt = shlex.quote(rendered)
            tmux.send_keys(tmux_session, name, f"claude --dangerously-skip-permissions --append-system-prompt {quoted_prompt}")
            logger.info("Launched Claude Code with worker prompt in %s", name)
        else:
            logger.warning("Worker template not found at %s", template_path)
            tmux.send_keys(tmux_session, name, "claude --dangerously-skip-permissions")
            logger.info("Launched Claude Code (no worker prompt) in %s", name)

        return {"ok": True, "tunnel_window": tunnel_name}

    except Exception as e:
        logger.exception("Failed to set up rdev worker %s", name)
        # Clean up tunnel window on failure
        try:
            tmux.kill_window(tmux_session, tunnel_name)
        except Exception:
            pass
        return {"ok": False, "error": str(e)}
