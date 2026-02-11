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
import time

from orchestrator.terminal.manager import capture_output, send_keys, kill_window
from orchestrator.session.health import (
    check_tunnel_alive,
    get_screen_session_name,
)
from orchestrator.agents import get_path_export_command, get_worker_prompt

logger = logging.getLogger(__name__)


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


def check_ssh_alive(tmux_sess: str, worker_win: str, host: str, retries: int = 2) -> bool:
    """Check if the SSH session in worker window is still alive by testing hostname.
    
    Sends 'hostname' command and checks if the HOSTNAME LINE (not other output) 
    contains 'rdev-' prefix indicating we're connected to an rdev VM.
    
    Retries a few times in case the shell is still loading.
    """
    for attempt in range(retries):
        try:
            marker_id = random.randint(10000, 99999)
            start_marker = f"SSH_START_{marker_id}"
            end_marker = f"SSH_END_{marker_id}"
            
            cmd = f"echo {start_marker} && hostname && echo {end_marker}"
            send_keys(tmux_sess, worker_win, cmd, enter=True)
            time.sleep(2)
            
            output = capture_output(tmux_sess, worker_win, lines=15)
            logger.debug("SSH alive check output (attempt %d): %s", attempt + 1, output)
            
            hostname = parse_hostname_from_output(output, start_marker, end_marker)
            
            if hostname is None:
                logger.info("SSH alive check: couldn't parse hostname from output (attempt %d)", attempt + 1)
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                return False
            
            logger.info("SSH alive check: hostname='%s'", hostname)
            
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


def check_inside_screen(tmux_sess: str, tmux_win: str) -> bool:
    """Check if the tmux window is currently inside a GNU Screen session.
    
    Uses the $STY environment variable which is set when inside screen.
    This is important to know BEFORE sending commands - if we're inside screen,
    we need to detach first (C-a d) before running screen commands.
    
    Returns:
        True if inside screen session, False otherwise
    """
    marker_id = random.randint(10000, 99999)
    start_marker = f"STY_START_{marker_id}"
    end_marker = f"STY_END_{marker_id}"
    
    # $STY contains the screen session name when inside screen, empty otherwise
    cmd = f'echo {start_marker} && echo "$STY" && echo {end_marker}'
    send_keys(tmux_sess, tmux_win, cmd, enter=True)
    time.sleep(1)
    
    output = capture_output(tmux_sess, tmux_win, lines=10)
    
    # Parse between markers
    sty_value = parse_hostname_from_output(output, start_marker, end_marker)
    
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
    """Build the system prompt from template, same as new worker setup."""
    prompt = get_worker_prompt(session_id)
    if prompt is None:
        logger.warning("Worker prompt template not found")
        return None
    
    return shlex.quote(prompt)


def check_screen_exists_via_tmux(
    tmux_sess: str, 
    tmux_win: str, 
    screen_name: str, 
    session_id: str
) -> tuple[bool, bool]:
    """Check if screen session exists and if Claude is running inside it.
    
    Sends commands via tmux to check screen status on the remote host.
    Uses unique markers to parse only the actual output, not the command itself.
    
    Args:
        screen_name: The screen session name (e.g., "claude-{session_id}")
        session_id: The orchestrator session ID (used to find Claude process)
    
    Returns (screen_exists: bool, claude_running: bool)
    """
    marker_id = random.randint(10000, 99999)
    start_marker = f"__SCRCHK_START_{marker_id}__"
    end_marker = f"__SCRCHK_END_{marker_id}__"
    
    check_cmd = (
        f"echo {start_marker} && "
        f"(screen -ls 2>/dev/null | grep -q '{screen_name}' && echo SCREEN_EXISTS || echo SCREEN_MISSING) && "
        f"(ps aux | grep -v grep | grep '{session_id}' | grep -i claude > /dev/null && echo CLAUDE_RUNNING || echo CLAUDE_MISSING) && "
        f"echo {end_marker}"
    )
    
    send_keys(tmux_sess, tmux_win, check_cmd, enter=True)
    time.sleep(1.5)
    
    output = capture_output(tmux_sess, tmux_win, lines=20)
    
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
    # STEP 1: Check/restore tunnel
    # =========================================================================
    tunnel_alive = False
    logger.info("Reconnect %s: Step 1 - checking tunnel (tunnel_pane=%s)", session.name, session.tunnel_pane)
    
    if session.tunnel_pane:
        if ":" in session.tunnel_pane:
            t_sess, t_win = session.tunnel_pane.split(":", 1)
        else:
            t_sess, t_win = tmux_sess, session.tunnel_pane
        tunnel_alive = check_tunnel_alive(t_sess, t_win)
        logger.info("Reconnect %s: tunnel alive=%s", session.name, tunnel_alive)
    else:
        logger.info("Reconnect %s: no tunnel_pane stored, will create new tunnel", session.name)
    
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
        logger.warning("Reconnect %s: screen not available", session.name)
    
    screen_exists, claude_running = check_screen_exists_via_tmux(tmux_sess, tmux_win, screen_name, session.id)
    logger.info("Reconnect %s: screen_exists=%s, claude_running=%s", session.name, screen_exists, claude_running)
    
    # =========================================================================
    # STEP 5: Reattach or create new screen
    # =========================================================================
    logger.info("Reconnect %s: Step 5 - reattach or create screen", session.name)
    
    if screen_exists and claude_running:
        # Best case: screen exists with Claude running, just reattach
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
        # Screen exists but Claude crashed - kill the stale screen
        logger.info("Reconnect %s: screen session exists but Claude not running, killing stale screen", session.name)
        send_keys(tmux_sess, tmux_win, f"screen -X -S {screen_name} quit 2>/dev/null", enter=True)
        time.sleep(0.5)
    
    # Create new screen session and launch Claude
    logger.info("Reconnect %s: creating new screen session '%s' and launching Claude", session.name, screen_name)
    
    send_keys(tmux_sess, tmux_win, f"screen -S {screen_name}", enter=True)
    time.sleep(1)
    
    # Set up environment
    path_export = get_path_export_command(f"{remote_tmp_dir}/bin")
    send_keys(tmux_sess, tmux_win, path_export, enter=True)
    time.sleep(0.3)
    
    if session.work_dir:
        send_keys(tmux_sess, tmux_win, f"cd {session.work_dir}", enter=True)
        time.sleep(0.3)
    
    # Launch Claude
    settings_file = f"{remote_tmp_dir}/configs/settings.json"
    claude_args = [
        f"-r {session.id}",
        f"--settings {settings_file}",
        "--dangerously-skip-permissions",
    ]
    
    system_prompt = build_system_prompt(session.id)
    if system_prompt:
        claude_args.append(f"--append-system-prompt {system_prompt}")
    
    claude_cmd = f"claude {' '.join(claude_args)}"
    send_keys(tmux_sess, tmux_win, claude_cmd, enter=True)
    
    # Unified status: always 'waiting' on successful reconnect
    repo.update_session(conn, session.id, status="waiting")
    logger.info("Reconnect %s: SUCCESS - launched Claude in new screen session", session.name)


def reconnect_local_worker(session, tmux_sess: str, tmux_win: str, api_port: int, tmp_dir: str):
    """Reconnect a local worker: cd to work_dir and relaunch claude.
    
    Uses same claude command as new workers (--session-id auto-resumes existing sessions).
    """
    path_export = get_path_export_command(os.path.join(tmp_dir, "bin"))
    send_keys(tmux_sess, tmux_win, path_export, enter=True)
    time.sleep(0.3)
    
    if session.work_dir:
        send_keys(tmux_sess, tmux_win, f"cd {shlex.quote(session.work_dir)}", enter=True)
        time.sleep(0.3)
    
    settings_file = os.path.join(tmp_dir, "configs", "settings.json")
    claude_args = [
        f"-r {session.id}",
        f"--settings {shlex.quote(settings_file)}",
    ]
    
    system_prompt = build_system_prompt(session.id)
    if system_prompt:
        claude_args.append(f"--append-system-prompt {system_prompt}")
    
    claude_cmd = f"claude {' '.join(claude_args)}"
    send_keys(tmux_sess, tmux_win, claude_cmd, enter=True)
    logger.info("Launched Claude Code for local worker %s (session_id=%s)", session.name, session.id)
