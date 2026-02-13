"""Session reconnection logic for rdev and local workers.

Handles re-establishing SSH tunnels, screen sessions, and relaunching Claude.

Reconnect Flow (rdev workers):
1. Check/restore tunnel
2. Check/restore SSH connection (verify hostname starts with 'rdev-')
3. Check if inside screen session ($STY) - detach if so
4. Check screen/Claude status via tmux commands
5. Either reattach to existing screen or create new one
"""

import logging
import os
import random
import shlex
import subprocess
import time

from orchestrator.terminal.manager import capture_output, send_keys, kill_window
from orchestrator.terminal.session import _copy_dir_to_rdev_ssh
from orchestrator.session.health import (
    check_tunnel_alive,
    get_screen_session_name,
)
from orchestrator.agents import get_path_export_command, get_worker_prompt

logger = logging.getLogger(__name__)


def _check_claude_session_exists_remote(host: str, session_id: str) -> bool:
    """Check if a Claude session file exists on remote host via SSH.
    
    Claude stores sessions in ~/.claude/projects/<path>/<session_id>.jsonl
    We check if any .jsonl file with this session_id exists.
    
    Returns True if session exists, False otherwise.
    """
    try:
        # Search for session file in any project directory
        check_cmd = f"ls ~/.claude/projects/*/{session_id}.jsonl 2>/dev/null && echo SESSION_EXISTS || echo SESSION_MISSING"
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, check_cmd],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stdout + result.stderr
        exists = "SESSION_EXISTS" in output and "SESSION_MISSING" not in output.split("SESSION_EXISTS")[-1]
        logger.debug("Claude session check on %s for %s: exists=%s (output=%r)", host, session_id, exists, output)
        return exists
    except Exception as e:
        logger.warning("Failed to check Claude session existence on %s: %s", host, e)
        # Default to -r (resume) if check fails - safer to try resume first
        return True


def _check_claude_session_exists_local(session_id: str) -> bool:
    """Check if a Claude session file exists locally.
    
    Claude stores sessions in ~/.claude/projects/<path>/<session_id>.jsonl
    We check if any .jsonl file with this session_id exists.
    
    Returns True if session exists, False otherwise.
    """
    import glob
    claude_dir = os.path.expanduser("~/.claude/projects")
    pattern = os.path.join(claude_dir, "*", f"{session_id}.jsonl")
    matches = glob.glob(pattern)
    exists = len(matches) > 0
    logger.debug("Claude session check local for %s: exists=%s (matches=%s)", session_id, exists, matches)
    return exists


def _get_claude_session_arg(session_id: str, session_exists: bool) -> str:
    """Get the appropriate Claude CLI argument based on session existence.
    
    Returns:
        '-r <id>' if session exists (resume)
        '--session-id <id>' if session doesn't exist (create new)
    """
    if session_exists:
        return f"-r {session_id}"
    else:
        return f"--session-id {session_id}"


def _launch_claude_in_screen(
    tmux_sess: str, tmux_win: str, session, tmp_dir: str, remote_tmp_dir: str, repo, conn
):
    """Launch Claude inside an existing screen session.
    
    This is called when we're already inside a screen session (either attached or created)
    and just need to launch Claude.
    
    Uses proactive check to determine if session exists:
    - If session exists: use 'claude -r <id>' to resume
    - If session doesn't exist: use 'claude --session-id <id>' to create new
    """
    # Set up environment
    path_export = get_path_export_command(f"{remote_tmp_dir}/bin")
    send_keys(tmux_sess, tmux_win, path_export, enter=True)
    time.sleep(0.3)
    
    if session.work_dir:
        send_keys(tmux_sess, tmux_win, f"cd {session.work_dir}", enter=True)
        time.sleep(0.3)
    
    # Copy prompt to remote (avoids pasting large content through tmux)
    remote_prompt_path = ensure_prompt_on_remote(
        tmux_sess, tmux_win, session.id, remote_tmp_dir
    )
    
    # Check if Claude session exists on remote to choose the right launch command
    session_exists = _check_claude_session_exists_remote(session.host, session.id)
    session_arg = _get_claude_session_arg(session.id, session_exists)
    logger.info("Reconnect %s: Claude session exists=%s, using arg: %s", session.name, session_exists, session_arg)
    
    # Launch Claude with skills from the remote .claude directory
    settings_file = f"{remote_tmp_dir}/configs/settings.json"
    claude_args = [
        session_arg,
        f"--settings {settings_file}",
        f"--add-dir {remote_tmp_dir}",
        "--dangerously-skip-permissions",
    ]
    
    if remote_prompt_path:
        claude_args.append(get_prompt_load_arg(remote_prompt_path))
    
    claude_cmd = f"claude {' '.join(claude_args)}"
    send_keys(tmux_sess, tmux_win, claude_cmd, enter=True)
    
    repo.update_session(conn, session.id, status="waiting")
    logger.info("Reconnect %s: SUCCESS - launched Claude in screen session", session.name)


def _ensure_local_configs_exist(tmp_dir: str, session_id: str, api_base: str = "http://127.0.0.1:8093"):
    """Regenerate local configs from templates.
    
    Always regenerates to ensure configs match current templates, even if files exist.
    This handles both missing files (orchestrator restart) and stale files (template updates).
    """
    import shutil
    from orchestrator.agents.deploy import generate_worker_hooks, deploy_worker_scripts, get_worker_skills_dir
    
    configs_dir = os.path.join(tmp_dir, "configs")
    os.makedirs(configs_dir, exist_ok=True)
    
    # Always regenerate configs from templates
    logger.info("Regenerating local configs at %s from templates", configs_dir)
    generate_worker_hooks(configs_dir, session_id, api_base)
    
    # Always regenerate bin scripts from templates
    logger.info("Regenerating local bin scripts at %s", tmp_dir)
    deploy_worker_scripts(tmp_dir, session_id, api_base)
    
    # Always regenerate skills to .claude/commands/
    skills_src = get_worker_skills_dir()
    local_skills_dir = os.path.join(tmp_dir, ".claude", "commands")
    if skills_src and os.path.isdir(skills_src):
        os.makedirs(local_skills_dir, exist_ok=True)
        for skill_file in os.listdir(skills_src):
            if skill_file.endswith(".md"):
                shutil.copy2(
                    os.path.join(skills_src, skill_file),
                    os.path.join(local_skills_dir, skill_file),
                )
        logger.info("Regenerated %d skills at %s", len(os.listdir(local_skills_dir)), local_skills_dir)


def _copy_configs_to_remote(host: str, tmp_dir: str, remote_tmp_dir: str, session_name: str):
    """Copy settings.json, hooks, bin scripts, and skills to remote host via direct SSH.
    
    This ensures the remote configs and scripts exist, which may have been cleared if /tmp was wiped.
    Called before both reattach and new screen creation.
    Uses direct SSH subprocess (bypasses tmux/screen for reliability).
    """
    import subprocess
    
    # Copy entire directory to remote via direct SSH
    if not _copy_dir_to_rdev_ssh(tmp_dir, host, remote_tmp_dir):
        raise RuntimeError(f"Failed to copy configs to remote via SSH: {host}:{remote_tmp_dir}")
    
    # Make scripts executable via SSH subprocess
    chmod_cmd = f"chmod +x {remote_tmp_dir}/bin/* 2>/dev/null; chmod +x {remote_tmp_dir}/configs/hooks/*.sh 2>/dev/null"
    subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, chmod_cmd],
        capture_output=True,
        timeout=30,
    )
    
    # Copy skills to ~/.claude/commands/ (global user skills directory)
    # NOTE: --add-dir flag doesn't work reliably in recent Claude Code versions,
    # so we copy skills directly to the user's global ~/.claude/commands/ folder
    # which Claude always loads regardless of working directory.
    skills_copy_cmd = f"mkdir -p ~/.claude/commands && cp {remote_tmp_dir}/.claude/commands/*.md ~/.claude/commands/ 2>/dev/null || true"
    subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, skills_copy_cmd],
        capture_output=True,
        timeout=30,
    )
    
    logger.info("Reconnect %s: copied all files to remote via direct SSH (including skills to ~/.claude/commands/)", session_name)


def reconnect_tunnel_only(conn, session, tmux_sess: str, api_port: int, repo) -> bool:
    """Reconnect just the SSH tunnel without touching the main worker window.
    
    Use this when SSH/screen/Claude are all running fine but the tunnel died.
    This avoids typing commands into Claude.
    
    Args:
        conn: Database connection
        session: Session object
        tmux_sess: tmux session name
        api_port: API port for tunnel
        repo: Sessions repository
        
    Returns:
        True if tunnel was successfully reconnected, False otherwise
    """
    from orchestrator.terminal import ssh
    from orchestrator.terminal.manager import create_window
    
    tunnel_name = f"{session.name}-tunnel"
    
    logger.info("Reconnect tunnel only for %s", session.name)
    
    # Clean up old tunnel window if it exists
    if session.tunnel_pane:
        try:
            if ":" in session.tunnel_pane:
                t_sess, t_win = session.tunnel_pane.split(":", 1)
            else:
                t_sess, t_win = tmux_sess, session.tunnel_pane
            kill_window(t_sess, t_win)
            logger.info("Killed old tunnel window %s", session.tunnel_pane)
        except Exception as e:
            logger.debug("Failed to kill old tunnel window: %s", e)
    
    # Create new tunnel
    try:
        create_window(tmux_sess, tunnel_name)
        ssh.setup_rdev_tunnel(tmux_sess, tunnel_name, session.host, api_port, api_port)
        time.sleep(3)
        
        # Verify tunnel is alive
        if check_tunnel_alive(tmux_sess, tunnel_name):
            repo.update_session(conn, session.id, tunnel_pane=f"{tmux_sess}:{tunnel_name}")
            logger.info("Tunnel reconnected successfully for %s", session.name)
            return True
        else:
            logger.warning("Tunnel reconnect failed verification for %s", session.name)
            return False
    except Exception as e:
        logger.error("Failed to reconnect tunnel for %s: %s", session.name, e)
        return False


def parse_hostname_from_output(output: str, start_marker: str, end_marker: str) -> str | None:
    """Extract hostname from captured terminal output between markers.
    
    The output includes the command line itself, so we need to find markers
    that appear at the START of a line (the actual echo output), not within
    the command line.
    
    Returns the hostname string or None if parsing failed.
    """
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
    
    hostname_lines = [l.strip() for l in lines[start_line_idx + 1:end_line_idx] if l.strip()]
    if hostname_lines:
        return hostname_lines[0]
    return None


def check_ssh_alive(tmux_sess: str, worker_win: str, host: str, retries: int = 3) -> bool:
    """Check if the SSH session in worker window is still alive by testing hostname.
    
    Sends 'hostname' command and checks if the HOSTNAME LINE (not other output) 
    contains 'rdev-' prefix indicating we're connected to an rdev VM.
    
    Retries a few times in case the shell is still loading.
    """
    from orchestrator.terminal.markers import MarkerCommand, parse_first_line
    
    for attempt in range(retries):
        try:
            cmd = MarkerCommand("hostname", prefix="SSH")
            send_keys(tmux_sess, worker_win, cmd.full_command, enter=True)
            time.sleep(2)
            
            output = capture_output(tmux_sess, worker_win, lines=15)
            logger.debug("SSH alive check output (attempt %d): %s", attempt + 1, output)
            
            hostname = parse_first_line(output, cmd.start_marker, cmd.end_marker)
            
            if hostname is None:
                logger.info("SSH alive check: couldn't parse hostname from output (attempt %d)", attempt + 1)
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                return False
            
            logger.info("SSH alive check: hostname='%s'", hostname)
            
            # If hostname doesn't look like the local machine, we're on a remote host
            if not hostname.lower().endswith(".linkedin.biz"):
                return True
            else:
                logger.info("SSH alive check: hostname '%s' looks like local machine, not connected to rdev", hostname)
                return False
        except Exception as e:
            logger.warning("SSH alive check failed (attempt %d): %s", attempt + 1, e)
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return False
    
    return False


def check_inside_screen(tmux_sess: str, tmux_win: str) -> bool:
    """Check if the tmux window is currently inside a GNU Screen session.
    
    Uses the $STY environment variable which is set when inside screen.
    This is important to know BEFORE sending commands - if we're inside screen,
    we need to detach first (C-a d) before running screen commands.
    
    Returns:
        True if inside screen session, False otherwise
    """
    from orchestrator.terminal.markers import MarkerCommand, parse_first_line
    
    cmd = MarkerCommand('echo "$STY"', prefix="STY")
    send_keys(tmux_sess, tmux_win, cmd.full_command, enter=True)
    time.sleep(1)
    
    output = capture_output(tmux_sess, tmux_win, lines=10)
    
    # Parse between markers
    sty_value = parse_first_line(output, cmd.start_marker, cmd.end_marker)
    
    if sty_value and sty_value.strip():
        logger.info("Inside screen check: $STY='%s' - we ARE inside screen", sty_value)
        return True
    else:
        logger.info("Inside screen check: $STY is empty - we are NOT inside screen")
        return False


def detach_from_screen(tmux_sess: str, tmux_win: str) -> None:
    """Detach from current screen session using C-a d.
    
    Only call this if check_inside_screen() returned True.
    """
    logger.info("Detaching from screen session (C-a d)")
    send_keys(tmux_sess, tmux_win, "C-a d", enter=False)
    time.sleep(0.5)
    # Send Enter to ensure we're at a clean prompt after detach
    send_keys(tmux_sess, tmux_win, "", enter=True)
    time.sleep(0.5)


def build_system_prompt(session_id: str) -> str | None:
    """Build the system prompt from template, same as new worker setup.
    
    DEPRECATED: For rdev workers, use ensure_prompt_on_remote() + get_prompt_load_arg() instead.
    This function is kept for local workers where file-based loading isn't needed.
    """
    prompt = get_worker_prompt(session_id)
    if prompt is None:
        logger.warning("Worker prompt template not found")
        return None
    
    return shlex.quote(prompt)


def ensure_prompt_on_remote(
    tmux_sess: str,
    tmux_win: str,
    session_id: str,
    remote_tmp_dir: str,
) -> str | None:
    """Copy worker prompt to remote and return the remote path.

    This avoids pasting large prompt content through tmux by copying the file
    to remote and using $(cat) to load it during claude launch.
    """
    prompt = get_worker_prompt(session_id)
    if prompt is None:
        logger.warning("Worker prompt template not found")
        return None
    
    remote_prompt_path = f"{remote_tmp_dir}/prompt.md"
    send_keys(tmux_sess, tmux_win,
        f"cat > {remote_prompt_path} << 'ORCHEOF'\n{prompt}\nORCHEOF",
        enter=True)
    time.sleep(0.3)
    logger.info("Copied worker prompt to remote: %s", remote_prompt_path)
    
    return remote_prompt_path


def get_prompt_load_arg(remote_prompt_path: str) -> str:
    """Get the claude CLI argument to load prompt from file on remote."""
    return f'--append-system-prompt "$(cat {remote_prompt_path})"'


def check_screen_exists_via_tmux(
    tmux_sess: str, 
    tmux_win: str, 
    screen_name: str, 
    session_id: str
) -> tuple[bool, bool]:
    """Check if screen session exists and if Claude is running inside it.
    
    Sends commands via tmux to check screen status on the remote host.
    Uses the markers module for safe parsing (avoids command echo false positives).
    
    Args:
        screen_name: The screen session name (e.g., "claude-{session_id}")
        session_id: The orchestrator session ID (used to find Claude process)
    
    Returns (screen_exists: bool, claude_running: bool)
    """
    from orchestrator.terminal.markers import MarkerCommand, parse_between_markers
    
    # Build the check command - outputs SCREEN_EXISTS/MISSING and CLAUDE_RUNNING/MISSING
    # Note: Check for 'claude -r' or 'claude --' to avoid matching screen session names
    # which contain 'claude-<session_id>'. The actual Claude CLI is invoked with flags.
    inner_cmd = (
        f"(screen -ls 2>/dev/null | grep -q '{screen_name}' && echo SCREEN_EXISTS || echo SCREEN_MISSING) && "
        f"(ps aux | grep -v grep | grep -E 'claude (-r|--|--settings)' | grep -q '{session_id}' && echo CLAUDE_RUNNING || echo CLAUDE_MISSING)"
    )
    cmd = MarkerCommand(inner_cmd, prefix="SCRCHK")
    
    # Debug: log the actual command being sent
    logger.info("Screen check command: %s", cmd.full_command)
    
    send_keys(tmux_sess, tmux_win, cmd.full_command, enter=True)
    time.sleep(1.5)
    
    output = capture_output(tmux_sess, tmux_win, lines=20)
    logger.debug("Screen check raw output: %r", output[:500] if output else None)
    
    # Parse result between markers (safe from command echo)
    result = parse_between_markers(output, cmd.start_marker, cmd.end_marker)
    logger.debug("Screen check parsed result: %r (start=%s, end=%s)", result, cmd.start_marker, cmd.end_marker)
    
    screen_exists = False
    claude_running = False
    
    if result:
        # Check for exact matches in the parsed result
        for line in result.splitlines():
            stripped = line.strip()
            logger.debug("Screen check line: %r", stripped)
            if stripped == "SCREEN_EXISTS":
                screen_exists = True
            elif stripped == "CLAUDE_RUNNING":
                claude_running = True
    
    logger.info("Screen check via tmux: screen_exists=%s, claude_running=%s (result=%r)", 
                screen_exists, claude_running, result)
    return screen_exists, claude_running


def reconnect_rdev_worker(conn, session, tmux_sess: str, tmux_win: str, api_port: int, tmp_dir: str, repo):
    """Reconnect an rdev worker: check/restore tunnel and SSH, then reattach to screen or launch claude.
    
    Simplified reconnection flow:
    1. Check/restore tunnel
    2. Check/restore SSH connection (verify hostname starts with 'rdev-')
    3. Check if inside screen session ($STY) - detach if so
    4. Check screen/Claude status via tmux commands (single check, not subprocess)
    5. Either reattach to existing screen or create new one
    
    All successful reconnects set status to 'waiting' for consistency.
    """
    from orchestrator.terminal import ssh
    from orchestrator.terminal.manager import create_window
    from orchestrator.terminal.session import _install_screen_if_needed
    
    tunnel_name = f"{session.name}-tunnel"
    remote_tmp_dir = f"/tmp/orchestrator/workers/{session.name}"
    screen_name = get_screen_session_name(session.id)
    
    # =========================================================================
    # STEP 0: Check if only tunnel needs fixing (SSH/screen/Claude all running)
    # =========================================================================
    # Use subprocess SSH check to verify remote state WITHOUT typing into the terminal
    # This prevents accidentally typing commands into Claude
    from orchestrator.session.health import check_screen_and_claude_rdev, check_worker_ssh_alive
    
    tunnel_alive = False
    if session.tunnel_pane:
        if ":" in session.tunnel_pane:
            t_sess, t_win = session.tunnel_pane.split(":", 1)
        else:
            t_sess, t_win = tmux_sess, session.tunnel_pane
        tunnel_alive = check_tunnel_alive(t_sess, t_win)
    
    if not tunnel_alive:
        # Check if SSH/screen/Claude are all running via subprocess (doesn't touch terminal)
        screen_status, _ = check_screen_and_claude_rdev(session.host, session.id)
        ssh_process_alive = check_worker_ssh_alive(tmux_sess, tmux_win, session.host)
        
        logger.info("Reconnect %s: tunnel_alive=%s, screen_status=%s, ssh_process_alive=%s",
                    session.name, tunnel_alive, screen_status, ssh_process_alive)
        
        if screen_status == "alive" and ssh_process_alive:
            # SSH/screen/Claude all fine - ONLY fix the tunnel, don't touch main window
            logger.info("Reconnect %s: SSH/screen/Claude all running, only tunnel needs fixing", session.name)
            if reconnect_tunnel_only(conn, session, tmux_sess, api_port, repo):
                # Tunnel fixed, update status and return
                repo.update_session(conn, session.id, status="waiting")
                logger.info("Reconnect %s: SUCCESS - tunnel reconnected, worker fully operational", session.name)
                return
            else:
                logger.warning("Reconnect %s: tunnel-only reconnect failed, continuing with full reconnect", session.name)
    
    # =========================================================================
    # STEP 1: Check/restore tunnel (full reconnect path)
    # =========================================================================
    logger.info("Reconnect %s: Step 1 - checking tunnel (tunnel_pane=%s)", session.name, session.tunnel_pane)
    
    if not tunnel_alive:
        logger.info("Reconnect %s: re-establishing tunnel", session.name)
        # Clean up old tunnel window if it exists
        if session.tunnel_pane:
            try:
                if ":" in session.tunnel_pane:
                    t_sess, t_win = session.tunnel_pane.split(":", 1)
                else:
                    t_sess, t_win = tmux_sess, session.tunnel_pane
                kill_window(t_sess, t_win)
                logger.info("Reconnect %s: killed old tunnel window %s", session.name, session.tunnel_pane)
            except Exception as e:
                logger.debug("Reconnect %s: failed to kill old tunnel window: %s", session.name, e)
        
        # Create new tunnel
        logger.info("Reconnect %s: creating tunnel window %s", session.name, tunnel_name)
        create_window(tmux_sess, tunnel_name)
        logger.info("Reconnect %s: setting up SSH tunnel to %s", session.name, session.host)
        ssh.setup_rdev_tunnel(tmux_sess, tunnel_name, session.host, api_port, api_port)
        time.sleep(3)
        repo.update_session(conn, session.id, tunnel_pane=f"{tmux_sess}:{tunnel_name}")
        logger.info("Reconnect %s: tunnel created and saved", session.name)
    
    # =========================================================================
    # STEP 2: Check/restore SSH connection in tmux window
    # =========================================================================
    logger.info("Reconnect %s: Step 2 - checking SSH connection", session.name)
    ssh_ok = check_ssh_alive(tmux_sess, tmux_win, session.host, retries=1)
    logger.info("Reconnect %s: SSH in tmux window alive=%s", session.name, ssh_ok)
    
    if not ssh_ok:
        logger.info("Reconnect %s: re-establishing SSH connection", session.name)
        # We're on local machine (not connected to rdev), just ensure clean prompt
        send_keys(tmux_sess, tmux_win, "", enter=True)
        time.sleep(0.3)
        
        ssh.rdev_connect(tmux_sess, tmux_win, session.host)
        if not ssh.wait_for_prompt(tmux_sess, tmux_win, timeout=60):
            raise RuntimeError(f"Timed out waiting for shell prompt on {session.host}")
        time.sleep(2)
        
        # Verify SSH is now working
        ssh_verified = check_ssh_alive(tmux_sess, tmux_win, session.host, retries=2)
        if not ssh_verified:
            raise RuntimeError(f"SSH reconnect to {session.host} failed verification (hostname check)")
        logger.info("Reconnect %s: SSH connection re-established and verified", session.name)
    
    # =========================================================================
    # STEP 3: Check if inside screen session - detach if so
    # =========================================================================
    logger.info("Reconnect %s: Step 3 - checking if inside screen session", session.name)
    if check_inside_screen(tmux_sess, tmux_win):
        logger.info("Reconnect %s: currently inside screen, detaching first", session.name)
        detach_from_screen(tmux_sess, tmux_win)
    
    # =========================================================================
    # STEP 4: Install screen if needed, then check screen/Claude status
    # =========================================================================
    logger.info("Reconnect %s: Step 4 - checking screen/Claude status", session.name)
    
    if not _install_screen_if_needed(tmux_sess, tmux_win):
        raise RuntimeError(f"Reconnect {session.name}: screen not available and could not be installed")
    
    try:
        screen_exists, claude_running = check_screen_exists_via_tmux(tmux_sess, tmux_win, screen_name, session.id)
        logger.info("Reconnect %s: screen_exists=%s, claude_running=%s", session.name, screen_exists, claude_running)
    except Exception as e:
        logger.exception("Reconnect %s: check_screen_exists_via_tmux failed: %s", session.name, e)
        # Default to creating new screen if check fails
        screen_exists, claude_running = False, False
        logger.info("Reconnect %s: defaulting to screen_exists=False, claude_running=False", session.name)
    
    # =========================================================================
    # STEP 5: Reattach or create new screen
    # =========================================================================
    logger.info("Reconnect %s: Step 5 - reattach or create screen (screen_exists=%s, claude_running=%s)", 
                session.name, screen_exists, claude_running)
    
    logger.info("Reconnect %s: evaluating path - screen_exists=%s, claude_running=%s", 
                session.name, screen_exists, claude_running)
    
    # Ensure local configs exist (regenerate from templates if orchestrator restarted)
    api_base = f"http://127.0.0.1:{api_port}"
    _ensure_local_configs_exist(tmp_dir, session.id, api_base)
    
    # Always ensure remote configs exist (may have been cleared even if screen is running)
    logger.info("Reconnect %s: ensuring remote configs exist", session.name)
    _copy_configs_to_remote(session.host, tmp_dir, remote_tmp_dir, session.name)
    
    if screen_exists and claude_running:
        # Best case: screen exists with Claude running
        # Check if we're already inside this screen session
        inside_screen = check_inside_screen(tmux_sess, tmux_win)
        
        if inside_screen:
            # Already inside screen with Claude running - nothing to do
            logger.info("Reconnect %s: already inside screen session with Claude running, done", session.name)
            repo.update_session(conn, session.id, status="waiting")
            return
        
        # Not inside screen - reattach
        logger.info("Reconnect %s: screen session '%s' found with Claude running, reattaching", 
                    session.name, screen_name)
        
        # Send sync marker before reattach to ensure clean state
        sync_marker = f"__SYNC_REATTACH_{random.randint(10000, 99999)}__"
        send_keys(tmux_sess, tmux_win, f"echo {sync_marker}", enter=True)
        time.sleep(1)
        
        sync_output = capture_output(tmux_sess, tmux_win, lines=10)
        if sync_marker not in sync_output:
            logger.warning("Reconnect %s: sync marker not found, waiting longer", session.name)
            time.sleep(2)
        
        send_keys(tmux_sess, tmux_win, f"screen -r {screen_name}", enter=True)
        repo.update_session(conn, session.id, status="waiting")
        logger.info("Reconnect %s: SUCCESS - reattached to existing screen with Claude", session.name)
        return
    
    if screen_exists and not claude_running:
        # Screen exists but Claude crashed/exited
        # Check if we're already inside this screen session
        inside_screen = check_inside_screen(tmux_sess, tmux_win)
        
        if inside_screen:
            # Already inside screen, just launch Claude directly
            logger.info("Reconnect %s: already inside screen session, launching Claude directly", session.name)
            _launch_claude_in_screen(tmux_sess, tmux_win, session, tmp_dir, remote_tmp_dir, repo, conn)
            return
        else:
            # Not inside screen - attach to existing screen and launch Claude
            logger.info("Reconnect %s: screen exists but Claude not running, attaching and relaunching", session.name)
            send_keys(tmux_sess, tmux_win, f"screen -r {screen_name}", enter=True)
            time.sleep(1)
            _launch_claude_in_screen(tmux_sess, tmux_win, session, tmp_dir, remote_tmp_dir, repo, conn)
            return
    
    # No screen exists - create new screen session and launch Claude
    logger.info("Reconnect %s: creating new screen session '%s' and launching Claude", session.name, screen_name)
    
    try:
        send_keys(tmux_sess, tmux_win, f"screen -S {screen_name}", enter=True)
        logger.info("Reconnect %s: sent 'screen -S %s' command", session.name, screen_name)
    except Exception as e:
        logger.exception("Reconnect %s: failed to send screen -S command: %s", session.name, e)
        raise
    
    time.sleep(2)  # Give screen time to start
    
    # Verify screen was created successfully using markers module
    from orchestrator.terminal.markers import check_yes_no
    
    logger.info("Reconnect %s: verifying screen creation...", session.name)
    verify_result = check_yes_no(
        send_keys, capture_output,
        tmux_sess, tmux_win,
        check_command=f"screen -ls | grep -q '{screen_name}'",
        prefix="SCREEN_CREATED",
        wait_time=1.0,
        retry_wait=2.0
    )
    logger.info("Reconnect %s: screen creation verify_result=%s", session.name, verify_result)
    
    if verify_result is False:
        raise RuntimeError(f"Reconnect {session.name}: failed to create screen session '{screen_name}'")
    
    if verify_result is None:
        logger.warning("Reconnect %s: screen creation verification timed out, proceeding anyway", session.name)
    
    # Set up environment (configs already copied earlier in flow)
    path_export = get_path_export_command(f"{remote_tmp_dir}/bin")
    send_keys(tmux_sess, tmux_win, path_export, enter=True)
    time.sleep(0.3)
    
    if session.work_dir:
        send_keys(tmux_sess, tmux_win, f"cd {session.work_dir}", enter=True)
        time.sleep(0.3)
    
    # Copy prompt to remote (avoids pasting large content through tmux)
    remote_prompt_path = ensure_prompt_on_remote(
        tmux_sess, tmux_win, session.id, remote_tmp_dir
    )
    
    # Check if Claude session exists on remote to choose the right launch command
    session_exists = _check_claude_session_exists_remote(session.host, session.id)
    session_arg = _get_claude_session_arg(session.id, session_exists)
    logger.info("Reconnect %s: Claude session exists=%s, using arg: %s", session.name, session_exists, session_arg)
    
    # Launch Claude with skills from the remote .claude directory
    settings_file = f"{remote_tmp_dir}/configs/settings.json"
    claude_args = [
        session_arg,
        f"--settings {settings_file}",
        f"--add-dir {remote_tmp_dir}",
        "--dangerously-skip-permissions",
    ]
    
    if remote_prompt_path:
        claude_args.append(get_prompt_load_arg(remote_prompt_path))
    
    claude_cmd = f"claude {' '.join(claude_args)}"
    send_keys(tmux_sess, tmux_win, claude_cmd, enter=True)
    
    # Unified status: always 'waiting' on successful reconnect
    repo.update_session(conn, session.id, status="waiting")
    logger.info("Reconnect %s: SUCCESS - launched Claude in new screen session", session.name)


def reconnect_local_worker(session, tmux_sess: str, tmux_win: str, api_port: int, tmp_dir: str):
    """Reconnect a local worker: cd to work_dir and relaunch claude.
    
    Uses proactive check to determine if session exists:
    - If session exists: use 'claude -r <id>' to resume
    - If session doesn't exist: use 'claude --session-id <id>' to create new
    """
    # Ensure local configs exist (regenerate from templates if orchestrator restarted)
    api_base = f"http://127.0.0.1:{api_port}"
    _ensure_local_configs_exist(tmp_dir, session.id, api_base)
    
    path_export = get_path_export_command(os.path.join(tmp_dir, "bin"))
    send_keys(tmux_sess, tmux_win, path_export, enter=True)
    time.sleep(0.3)
    
    if session.work_dir:
        send_keys(tmux_sess, tmux_win, f"cd {shlex.quote(session.work_dir)}", enter=True)
        time.sleep(0.3)
    
    # Check if Claude session exists locally to choose the right launch command
    session_exists = _check_claude_session_exists_local(session.id)
    session_arg = _get_claude_session_arg(session.id, session_exists)
    logger.info("Reconnect local %s: Claude session exists=%s, using arg: %s", session.name, session_exists, session_arg)
    
    settings_file = os.path.join(tmp_dir, "configs", "settings.json")
    claude_args = [
        session_arg,
        f"--settings {shlex.quote(settings_file)}",
    ]
    
    system_prompt = build_system_prompt(session.id)
    if system_prompt:
        claude_args.append(f"--append-system-prompt {system_prompt}")
    
    claude_cmd = f"claude {' '.join(claude_args)}"
    send_keys(tmux_sess, tmux_win, claude_cmd, enter=True)
    logger.info("Launched Claude Code for local worker %s (session_id=%s)", session.name, session.id)
