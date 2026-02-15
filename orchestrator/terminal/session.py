"""Full session lifecycle: create, start Claude Code, remove."""

from __future__ import annotations

import base64
import logging
import os
import shlex
import sqlite3
import subprocess
import time

from orchestrator.state.models import Session
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.terminal import manager as tmux
from orchestrator.terminal import ssh
from orchestrator.agents import deploy_worker_scripts, generate_worker_hooks, get_path_export_command, get_worker_prompt, WORKER_SCRIPT_NAMES
from orchestrator.agents.deploy import get_worker_skills_dir

logger = logging.getLogger(__name__)

_SOURCE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def create_session(
    conn: sqlite3.Connection,
    name: str,
    host: str,
    work_dir: str | None = None,
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
    if work_dir:
        tmux.send_keys(tmux_session, name, f"cd {work_dir}")
        time.sleep(0.5)

    # Persist to DB
    session = sessions_repo.create_session(
        conn, name=name, host=host, work_dir=work_dir, tmux_window=target
    )
    logger.info("Created session: %s (host=%s, path=%s)", name, host, work_dir)
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


def _verify_message_sent(
    tmux_session: str,
    window_name: str,
    message: str,
) -> bool:
    """Check if a message was successfully submitted (no longer in input line).
    
    After pressing Enter, if the message was sent successfully:
    - Claude Code will start processing (showing status/thinking)
    - The input line will be cleared
    
    If the message is stuck:
    - The terminal will still show the message text on the input line
    
    Returns True if the message appears to have been sent.
    """
    # Give Claude Code a moment to process the Enter
    time.sleep(0.3)
    
    # Capture recent output
    output = tmux.capture_output(tmux_session, window_name, lines=10)
    
    # Get the last few lines to check for stuck input
    lines = output.strip().split('\n')
    if not lines:
        return True  # Empty output, assume sent
    
    # Check the last line - if it contains a substantial portion of the message,
    # it's likely stuck in the input buffer
    last_line = lines[-1].strip()
    
    # For long messages, check if the last line contains a significant chunk of the message
    # (input line would show the end of the pasted message)
    if len(message) > 50:
        # Check if last portion of message is in the last line (message stuck in input)
        message_tail = message[-100:] if len(message) > 100 else message
        # Normalize whitespace for comparison
        message_tail_normalized = ' '.join(message_tail.split())
        last_line_normalized = ' '.join(last_line.split())
        
        if len(last_line_normalized) > 20 and message_tail_normalized[-50:] in last_line_normalized:
            logger.debug("Message appears stuck in input - last line matches message tail")
            return False
    
    # Also check if the cursor line appears to have unsubmitted content
    # Claude Code shows ">" prompt when waiting for input
    # If we see significant text after the prompt, message may be stuck
    if last_line.startswith('>') and len(last_line) > 20:
        # There's substantial text after the prompt - likely stuck
        logger.debug("Message appears stuck - text after prompt: %s...", last_line[:50])
        return False
    
    return True


def send_to_session(
    name: str,
    message: str,
    tmux_session: str = "orchestrator",
    max_enter_retries: int = 3,
    retry_delay: float = 2.0,
) -> bool:
    """Send a message to a session's Claude Code instance.
    
    Uses literal mode to send text (avoiding tmux special key interpretation),
    then sends Enter separately to submit the message.
    
    For long messages, the Enter key might be pressed before text is fully pasted.
    This function verifies the message was sent and retries Enter if needed.
    
    Args:
        name: Session/window name
        message: Message content to send
        tmux_session: Tmux session name
        max_enter_retries: Max attempts to press Enter if message appears stuck (default: 3)
        retry_delay: Seconds between Enter retries (default: 2.0)
    """
    # Send message content in literal mode (no special key interpretation)
    if not tmux.send_keys_literal(tmux_session, name, message):
        return False
    
    # Send Enter and verify it was submitted
    for attempt in range(max_enter_retries):
        if not tmux.send_keys(tmux_session, name, "", enter=True):
            return False
        
        # Verify the message was sent
        if _verify_message_sent(tmux_session, name, message):
            if attempt > 0:
                logger.info("Message sent successfully after %d Enter retries", attempt + 1)
            return True
        
        # Message appears stuck, wait and retry Enter
        if attempt < max_enter_retries - 1:
            logger.warning(
                "Message may be stuck in input, retrying Enter (attempt %d/%d)",
                attempt + 1, max_enter_retries
            )
            time.sleep(retry_delay)
    
    # All retries exhausted
    logger.error("Failed to send message after %d Enter attempts", max_enter_retries)
    return False


def _get_screen_session_name(session_id: str) -> str:
    """Get the screen session name for a worker session."""
    return f"claude-{session_id}"


def _wait_for_command_completion(tmux_session: str, window_name: str, timeout: int = 60, poll_interval: float = 2.0) -> bool:
    """Wait for a command to complete by checking for shell prompt return.
    
    Uses the markers module for safe marker-based detection.
    Returns True if command completed within timeout, False otherwise.
    """
    from orchestrator.terminal.markers import wait_for_completion
    return wait_for_completion(
        tmux.send_keys, tmux.capture_output,
        tmux_session, window_name,
        timeout=timeout, poll_interval=poll_interval
    )


def _install_screen_if_needed(tmux_session: str, window_name: str) -> bool:
    """Install screen on rdev if not already installed.
    
    Returns True if screen is available (already installed or successfully installed).
    """
    from orchestrator.terminal.markers import check_yes_no, wait_for_completion
    
    # Check if screen is installed
    result = check_yes_no(
        tmux.send_keys, tmux.capture_output,
        tmux_session, window_name,
        check_command="which screen",
        prefix="SCREEN_CHK"
    )
    
    if result is True:
        logger.info("Screen already installed")
        return True
    
    if result is False:
        logger.info("Screen not found, installing...")
    else:
        logger.warning("Could not determine screen status, attempting install")
    
    # Install screen
    tmux.send_keys(tmux_session, window_name, "sudo yum install screen -y", enter=True)
    
    # Wait for installation to complete (poll for up to 60 seconds)
    if not wait_for_completion(
        tmux.send_keys, tmux.capture_output,
        tmux_session, window_name,
        timeout=60
    ):
        logger.warning("Screen installation may not have completed")
    
    # Verify installation
    verify_result = check_yes_no(
        tmux.send_keys, tmux.capture_output,
        tmux_session, window_name,
        check_command="which screen",
        prefix="SCREEN_VFY"
    )
    logger.debug("Screen verify result: %r", verify_result)
    
    if verify_result is True:
        logger.info("Screen installed successfully")
        return True
    
    logger.warning("Failed to install screen (verify result: %r)", verify_result)
    return False


def _kill_orphaned_screen(tmux_session: str, window_name: str, screen_name: str):
    """Kill any orphaned screen session with the given name."""
    tmux.send_keys(tmux_session, window_name, f"screen -X -S {screen_name} quit 2>/dev/null", enter=True)
    time.sleep(0.5)


def _copy_dir_to_rdev_ssh(local_dir: str, host: str, remote_dir: str) -> bool:
    """Copy a local directory to rdev host using direct SSH subprocess.
    
    This bypasses tmux/screen entirely by piping tar directly through SSH stdin.
    Much more reliable than sending large commands through tmux send-keys.
    
    Args:
        local_dir: Local directory to copy
        host: rdev host (e.g., "user/rdev-vm")
        remote_dir: Remote directory to extract to
        
    Returns:
        True if copy succeeded, False otherwise
    """
    try:
        # First create remote directory via SSH
        mkdir_result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host,
             f"mkdir -p {remote_dir}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if mkdir_result.returncode != 0:
            logger.error("Failed to create remote dir %s: %s", remote_dir, mkdir_result.stderr)
            return False
        
        # Pipe tar directly through SSH - no base64, no tmux, no screen
        # tar on local | ssh host "tar extract on remote"
        tar_proc = subprocess.Popen(
            ["tar", "czf", "-", "-C", local_dir, "."],
            stdout=subprocess.PIPE,
        )
        
        ssh_proc = subprocess.Popen(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host,
             f"tar xzf - -C {remote_dir}"],
            stdin=tar_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        
        # Allow tar_proc to receive SIGPIPE if ssh_proc exits
        tar_proc.stdout.close()
        
        stdout, stderr = ssh_proc.communicate(timeout=60)
        tar_proc.wait()
        
        if ssh_proc.returncode != 0:
            logger.error("SSH tar extract failed: %s", stderr.decode())
            return False
        
        logger.info("Copied %s to %s:%s via direct SSH", local_dir, host, remote_dir)
        return True
        
    except subprocess.TimeoutExpired:
        logger.error("SSH copy timed out for %s -> %s:%s", local_dir, host, remote_dir)
        return False
    except Exception as e:
        logger.error("SSH copy failed: %s", e)
        return False


def _copy_dir_to_remote(
    tmux_session: str,
    window_name: str,
    local_dir: str,
    remote_dir: str,
) -> None:
    """Copy a local directory to remote using tar + base64 via tmux.
    
    DEPRECATED: Use _copy_dir_to_rdev_ssh() for rdev hosts instead.
    This function sends commands through tmux which can fail with large payloads.
    Kept for backwards compatibility with non-rdev hosts.
    """
    # Create remote directory
    tmux.send_keys(tmux_session, window_name, f"mkdir -p {remote_dir}", enter=True)
    time.sleep(0.3)
    
    # Pack and send via heredoc (more reliable than echo for large content)
    result = subprocess.run(
        ["tar", "czf", "-", "-C", local_dir, "."],
        capture_output=True,
        check=True,
    )
    encoded = base64.b64encode(result.stdout).decode("ascii")
    
    # Always use chunked file approach for reliability
    chunk_size = 4000  # Much smaller chunks for tmux reliability
    chunks = [encoded[i:i+chunk_size] for i in range(0, len(encoded), chunk_size)]
    
    tmux.send_keys(tmux_session, window_name, f"rm -f /tmp/_orch_transfer.b64", enter=True)
    time.sleep(0.1)
    
    for chunk in chunks:
        tmux.send_keys(tmux_session, window_name, 
            f"echo -n '{chunk}' >> /tmp/_orch_transfer.b64", enter=True)
        time.sleep(0.05)
    
    tmux.send_keys(tmux_session, window_name,
        f"base64 -d /tmp/_orch_transfer.b64 | tar xzf - -C {remote_dir} && rm -f /tmp/_orch_transfer.b64",
        enter=True)
    time.sleep(0.5)
    
    logger.info("Copied %s to remote %s via tar+base64 (tmux)", local_dir, remote_dir)


def setup_rdev_worker(
    conn: sqlite3.Connection,
    session_id: str,
    name: str,
    host: str,
    tmux_session: str = "orchestrator",
    api_port: int = 8093,
    work_dir: str | None = None,
    tmp_dir: str | None = None,
    tunnel_manager=None,
) -> dict:
    """Set up a full rdev worker: tunnel, SSH, screen, Claude, prompt.

    Returns {"ok": True, "tunnel_pid": ...} on success,
    or {"ok": False, "error": "..."} on failure.

    Args:
        work_dir: Where Claude Code runs (user's codebase). If None, uses rdev home.
        tmp_dir: Local tmp directory for generating scripts/configs before copying to remote.
        tunnel_manager: ReverseTunnelManager for subprocess-based tunnel management.

    Claude Code runs inside a GNU Screen session to survive SSH disconnections.
    Screen session name: claude-{session_id}
    """
    remote_tmp_dir = f"/tmp/orchestrator/workers/{name}"
    local_tmp_dir = tmp_dir or f"/tmp/orchestrator/workers/{name}"
    screen_name = _get_screen_session_name(session_id)

    try:
        # 1. Start reverse SSH tunnel via subprocess (no tmux window needed)
        tunnel_pid = None
        if tunnel_manager:
            tunnel_pid = tunnel_manager.start_tunnel(session_id, name, host)
            if tunnel_pid:
                logger.info("Started reverse tunnel subprocess for %s -> %s (pid=%d)", name, host, tunnel_pid)
            else:
                logger.warning("Failed to start tunnel subprocess for %s, continuing setup", name)
        else:
            logger.warning("No tunnel_manager provided, skipping tunnel setup for %s", name)
        time.sleep(2)  # Give tunnel a moment to establish

        # 2. Connect to rdev VM
        ssh.rdev_connect(tmux_session, name, host)
        logger.info("Connecting to rdev VM for %s: %s", name, host)

        # 3. Wait for shell prompt
        if not ssh.wait_for_prompt(tmux_session, name, timeout=30):
            raise RuntimeError(f"Timed out waiting for shell prompt on {host}")

        # 3b. Source bashrc first to pick up correct claude binary, then update
        tmux.send_keys(tmux_session, name, 
            'echo \'export PATH="$HOME/.local/bin:$PATH"\' >> ~/.bashrc && source ~/.bashrc', 
            enter=True)
        time.sleep(1)
        tmux.send_keys(tmux_session, name, "claude update", enter=True)
        time.sleep(5)  # Wait for update to complete
        logger.info("Configured PATH and updated Claude for %s", name)
        
        # 3c. Install screen if needed
        if not _install_screen_if_needed(tmux_session, name):
            logger.warning("Screen not available, falling back to direct execution")
            # Continue without screen - less resilient but still functional
        
        # 3d. Kill any orphaned screen session from previous runs
        _kill_orphaned_screen(tmux_session, name, screen_name)
        
        # 4. Enter screen session early - all remaining commands run inside screen
        # This protects the entire setup process from SSH disconnections
        tmux.send_keys(tmux_session, name, f"screen -S {screen_name}", enter=True)
        time.sleep(1)  # Wait for screen to start
        logger.info("Entered screen session '%s' for worker %s", screen_name, name)

        # 5. Deploy all files locally (scripts, configs, prompt)
        os.makedirs(local_tmp_dir, exist_ok=True)
        bin_dir = deploy_worker_scripts(
            worker_dir=local_tmp_dir,
            session_id=session_id,
            api_base=f"http://127.0.0.1:{api_port}",
        )
        logger.info("Deployed CLI scripts in %s", bin_dir)
        
        # Generate hooks/settings in local tmp dir
        local_configs_dir = os.path.join(local_tmp_dir, "configs")
        os.makedirs(local_configs_dir, exist_ok=True)
        generate_worker_hooks(
            worker_dir=local_configs_dir,
            session_id=session_id,
            api_base=f"http://127.0.0.1:{api_port}",
        )
        logger.info("Generated hooks settings in %s", local_configs_dir)
        
        # Write prompt.md to local tmp dir
        worker_prompt = get_worker_prompt(session_id)
        remote_prompt_path = f"{remote_tmp_dir}/prompt.md"
        if worker_prompt:
            with open(os.path.join(local_tmp_dir, "prompt.md"), "w") as f:
                f.write(worker_prompt)
        
        # Copy worker skills to local tmp dir for transfer (inside .claude/commands/)
        skills_src = get_worker_skills_dir()
        local_skills_dir = os.path.join(local_tmp_dir, ".claude", "commands")
        if skills_src and os.path.isdir(skills_src):
            import shutil
            os.makedirs(local_skills_dir, exist_ok=True)
            for skill_file in os.listdir(skills_src):
                if skill_file.endswith(".md"):
                    shutil.copy2(
                        os.path.join(skills_src, skill_file),
                        os.path.join(local_skills_dir, skill_file),
                    )
            logger.info("Prepared %d skills for transfer", len(os.listdir(local_skills_dir)))
        
        # 6. Copy entire directory to remote via direct SSH (bypasses tmux/screen)
        if not _copy_dir_to_rdev_ssh(local_tmp_dir, host, remote_tmp_dir):
            raise RuntimeError(f"Failed to copy files to remote via SSH: {host}:{remote_tmp_dir}")
        
        logger.info("Copied files to remote via direct SSH: %s", remote_tmp_dir)
        
        # Make scripts executable
        tmux.send_keys(tmux_session, name, f"chmod +x {remote_tmp_dir}/bin/*", enter=True)
        time.sleep(0.3)
        tmux.send_keys(tmux_session, name, f"chmod +x {remote_tmp_dir}/configs/hooks/*.sh 2>/dev/null || true", enter=True)
        time.sleep(0.3)
        
        # Export PATH
        path_export = get_path_export_command(f"{remote_tmp_dir}/bin")
        tmux.send_keys(tmux_session, name, path_export, enter=True)
        time.sleep(0.5)
        logger.info("Copied all files to remote via tar+base64 and updated PATH")

        # 7. cd to work_dir if specified (inside screen)
        if work_dir:
            tmux.send_keys(tmux_session, name, f"cd {work_dir}", enter=True)
            time.sleep(0.3)
        
        # Deploy skills to ~/.claude/commands/ (global user skills directory)
        # NOTE: --add-dir flag doesn't work reliably in recent Claude Code versions,
        # so we copy skills directly to the user's global ~/.claude/commands/ folder
        # which Claude always loads regardless of working directory.
        if skills_src and os.path.isdir(skills_src):
            global_skills_dest = "~/.claude/commands"
            tmux.send_keys(tmux_session, name, f"mkdir -p {global_skills_dest}", enter=True)
            time.sleep(0.2)
            tmux.send_keys(tmux_session, name, f"cp {remote_tmp_dir}/.claude/commands/*.md {global_skills_dest}/ 2>/dev/null || true", enter=True)
            time.sleep(0.3)
            logger.info("Deployed skills to %s for rdev worker %s", global_skills_dest, name)
        
        # 8. Launch Claude (inside screen)
        settings_file = f"{remote_tmp_dir}/configs/settings.json"
        
        claude_args = [
            f"--settings {settings_file}",
            "--dangerously-skip-permissions",
            f"--session-id {session_id}",
        ]
        
        # Use $(cat prompt.md) to load prompt from file instead of pasting content
        if worker_prompt:
            claude_args.append(f'--append-system-prompt "$(cat {remote_prompt_path})"')
        
        claude_cmd = f"claude {' '.join(claude_args)}"
        tmux.send_keys(tmux_session, name, claude_cmd, enter=True)
        logger.info("Launched Claude Code in screen session '%s' for rdev worker %s (work_dir=%s)", 
                    screen_name, name, work_dir)

        return {"ok": True, "tunnel_pid": tunnel_pid}

    except Exception as e:
        logger.exception("Failed to set up rdev worker %s", name)
        # Clean up tunnel subprocess on failure
        if tunnel_manager:
            try:
                tunnel_manager.stop_tunnel(session_id)
            except Exception:
                pass
        return {"ok": False, "error": str(e)}
