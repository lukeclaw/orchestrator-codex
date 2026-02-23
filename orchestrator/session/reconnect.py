"""Session reconnection logic for rdev and local workers.

Handles re-establishing SSH tunnels, screen sessions, and relaunching Claude.

Reconnect Flow — Sequential Pipeline (rdev workers):

  Step 0: Acquire per-session lock (prevents concurrent reconnects)
  Step 1: Check pane safety (TUI + SSH alive — non-intrusive)
    → If TUI + SSH alive → verify via subprocess SSH → fix tunnel only → done
    → If TUI + SSH dead → stale screen, will be cleaned
    → No TUI → safe to interact
  Step 2: Fix tunnel if dead (subprocess only, no pane interaction)
  Step 3: Ensure SSH (if dead: clean pane → rdev ssh → wait for prompt)
    → After this: guaranteed at remote shell prompt
  Step 4: Copy configs to remote (subprocess SSH, no pane)
  Step 5: Check screen/Claude status (safe: at shell prompt, send_keys OK)
  Step 6: Act: reattach screen / reattach+launch Claude / create screen+launch Claude

Critical invariant: **never send commands to a tmux pane that has a TUI running.**
"""

import logging
import os
import shlex
import subprocess
import threading
import time

from orchestrator.terminal.manager import capture_output, send_keys, kill_window
from orchestrator.terminal.session import _copy_dir_to_remote_ssh
from orchestrator.session.health import (
    get_screen_session_name,
    check_tui_running_in_pane,
)
from orchestrator.agents import get_path_export_command, get_worker_prompt

logger = logging.getLogger(__name__)


# =============================================================================
# TUI Safety Guard
# =============================================================================

class TUIActiveError(RuntimeError):
    """Raised when attempting to send keys to a pane with an active TUI."""
    pass


def safe_send_keys(tmux_sess: str, tmux_win: str, text: str, enter: bool = True):
    """Send keys to a tmux pane, but only if no TUI is active.

    Defense-in-depth wrapper around ``send_keys()``.  Used by Steps 5 and 6
    of the reconnect pipeline where we *believe* the pane is at a shell
    prompt but want to be absolutely sure.

    Raises:
        TUIActiveError: if a TUI (alternate screen buffer) is detected.
    """
    if check_tui_running_in_pane(tmux_sess, tmux_win):
        raise TUIActiveError(
            f"TUI running in {tmux_sess}:{tmux_win}, refusing send_keys"
        )
    return send_keys(tmux_sess, tmux_win, text, enter=enter)


# =============================================================================
# Per-Session Reconnect Locking
# =============================================================================

_reconnect_locks: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()


def get_reconnect_lock(session_id: str) -> threading.Lock:
    """Return (or create) a per-session reconnect lock."""
    with _registry_lock:
        if session_id not in _reconnect_locks:
            _reconnect_locks[session_id] = threading.Lock()
        return _reconnect_locks[session_id]


def cleanup_reconnect_lock(session_id: str):
    """Remove the per-session reconnect lock (call on session delete)."""
    with _registry_lock:
        _reconnect_locks.pop(session_id, None)


# =============================================================================
# Internal Helpers
# =============================================================================

def _detach_from_screen(tmux_sess: str, tmux_win: str):
    """Detach from GNU Screen by sending Ctrl-A then d as separate keys.

    tmux ``send-keys`` interprets ``C-a`` as Ctrl-A only when it is a
    standalone argument.  Passing ``"C-a d"`` as a single string sends the
    literal characters ``C``, ``-``, ``a``, `` ``, ``d``.
    """
    import subprocess
    target = f"{tmux_sess}:{tmux_win}"
    subprocess.run(["tmux", "send-keys", "-t", target, "C-a"], capture_output=True, timeout=5)
    time.sleep(0.1)
    subprocess.run(["tmux", "send-keys", "-t", target, "d"], capture_output=True, timeout=5)
    time.sleep(0.5)
    # Send Enter to ensure we're at a clean prompt after detach
    send_keys(tmux_sess, tmux_win, "", enter=True)
    time.sleep(0.5)


def _clean_pane_for_ssh(tmux_sess: str, tmux_win: str, cwd: str | None = None):
    """Prepare a pane for SSH reconnection.

    Only called when we've determined SSH is dead.  Handles the edge case
    where a dead SSH left the pane in alternate screen mode (e.g. GNU Screen
    was attached when SSH died).
    """
    from orchestrator.terminal.manager import ensure_window

    if check_tui_running_in_pane(tmux_sess, tmux_win):
        # Stale alternate screen from dead SSH — try Ctrl-C + Enter
        send_keys(tmux_sess, tmux_win, "C-c", enter=False)
        time.sleep(0.5)
        send_keys(tmux_sess, tmux_win, "", enter=True)
        time.sleep(0.5)

        # If still stuck, kill and recreate pane
        if check_tui_running_in_pane(tmux_sess, tmux_win):
            logger.info("_clean_pane_for_ssh: TUI still active after Ctrl-C, killing and recreating pane")
            kill_window(tmux_sess, tmux_win)
            ensure_window(tmux_sess, tmux_win, cwd=cwd)
            return

    # Normal case: Ctrl-C + Enter to ensure clean shell prompt
    send_keys(tmux_sess, tmux_win, "C-c", enter=False)
    time.sleep(0.3)
    send_keys(tmux_sess, tmux_win, "", enter=True)
    time.sleep(0.5)


def _ensure_tunnel(session, tunnel_manager, repo, conn):
    """Restart the reverse tunnel subprocess.  Never touches the pane."""
    if tunnel_manager is None:
        logger.warning("_ensure_tunnel: no tunnel_manager for %s", session.name)
        return
    new_pid = tunnel_manager.restart_tunnel(session.id, session.name, session.host)
    if new_pid:
        repo.update_session(conn, session.id, tunnel_pid=new_pid)
        logger.info("_ensure_tunnel: tunnel started for %s (pid=%d)", session.name, new_pid)
    else:
        logger.warning("_ensure_tunnel: tunnel restart failed for %s", session.name)


# =============================================================================
# Unchanged Helpers (kept from previous version)
# =============================================================================

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


def _get_claude_session_arg(session_id: str, session_exists: bool, has_tracked_id: bool = False) -> str:
    """Get the appropriate Claude CLI argument based on session existence.

    Returns:
        '-r <id>' if session exists (resume specific conversation)
        '--session-id <id>' if session doesn't exist (create new)

    Note: We intentionally never use 'claude -c' (resume most recent).
    On shared rdev hosts, -c can pick up a conversation from a *different*
    worker that previously ran in the same work_dir.  That resumed session
    carries the old worker's stored hooks (absolute paths + baked-in
    SESSION_ID), causing cross-worker hook contamination and task mismatches.
    Creating a fresh session is always safer than gambling on -c.
    """
    if session_exists:
        return f"-r {session_id}"
    else:
        return f"--session-id {session_id}"


def _verify_claude_started(
    tmux_sess: str, tmux_win: str,
    timeout: int = 10, poll_interval: float = 2.0,
) -> tuple[bool, str]:
    """Check that Claude's TUI actually started after sending the launch command.

    Polls for the alternate screen buffer (TUI indicator) within *timeout*
    seconds.  If the TUI never appears it usually means ``claude -r`` failed
    with an error like "No conversation found with session ID: …".

    Returns:
        (started, error_output) — *started* is True when the TUI is detected.
        When False, *error_output* contains the last terminal lines for diagnosis.
    """
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(poll_interval)
        if check_tui_running_in_pane(tmux_sess, tmux_win):
            return True, ""

    # TUI never appeared — capture output to check for errors
    output = capture_output(tmux_sess, tmux_win, lines=15)
    return False, output


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

    # Use Claude's tracked session ID if available, otherwise orchestrator ID
    target_id = session.claude_session_id or session.id
    has_tracked_id = session.claude_session_id is not None

    session_exists = _check_claude_session_exists_remote(session.host, target_id)
    session_arg = _get_claude_session_arg(target_id, session_exists, has_tracked_id)
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

    # Verify Claude actually started — recover if -r failed
    started, error_output = _verify_claude_started(tmux_sess, tmux_win)
    if not started:
        logger.warning(
            "Reconnect %s: Claude failed to start (arg=%s, output=%s). "
            "Retrying with --session-id to create a fresh session.",
            session.name, session_arg, error_output[:300],
        )
        # Clean up the failed command prompt
        send_keys(tmux_sess, tmux_win, "C-c", enter=False)
        time.sleep(0.5)
        send_keys(tmux_sess, tmux_win, "", enter=True)
        time.sleep(0.5)

        # Retry with --session-id (creates a new conversation)
        fallback_arg = f"--session-id {target_id}"
        claude_args_retry = [
            fallback_arg,
            f"--settings {settings_file}",
            f"--add-dir {remote_tmp_dir}",
            "--dangerously-skip-permissions",
        ]
        if remote_prompt_path:
            claude_args_retry.append(get_prompt_load_arg(remote_prompt_path))

        claude_cmd_retry = f"claude {' '.join(claude_args_retry)}"
        logger.info("Reconnect %s: retrying with: %s", session.name, fallback_arg)
        send_keys(tmux_sess, tmux_win, claude_cmd_retry, enter=True)

        # Check the retry — if this also fails, let the health check loop handle it
        retry_started, retry_output = _verify_claude_started(tmux_sess, tmux_win)
        if not retry_started:
            logger.error(
                "Reconnect %s: retry also failed (output=%s). Giving up.",
                session.name, retry_output[:300],
            )
            repo.update_session(conn, session.id, status="error")
            return

    repo.update_session(conn, session.id, status="waiting")
    logger.info("Reconnect %s: SUCCESS - launched Claude in screen session", session.name)


def _get_custom_skills_from_conn(conn, target: str) -> list[dict]:
    """Read enabled custom skills from an existing DB connection."""
    try:
        rows = conn.execute(
            "SELECT name, description, content FROM skills WHERE target = ? AND enabled = 1",
            (target,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        # Table may not exist if migration hasn't run yet
        return []


def _ensure_local_configs_exist(tmp_dir: str, session_id: str, api_base: str = "http://127.0.0.1:8093", conn=None):
    """Regenerate local configs from templates.

    Always regenerates to ensure configs match current templates, even if files exist.
    This handles both missing files (orchestrator restart) and stale files (template updates).
    """
    import shutil
    from orchestrator.agents.deploy import generate_worker_hooks, deploy_worker_scripts, get_worker_skills_dir, deploy_custom_skills

    configs_dir = os.path.join(tmp_dir, "configs")
    os.makedirs(configs_dir, exist_ok=True)

    # Always regenerate configs from templates
    logger.info("Regenerating local configs at %s from templates", configs_dir)
    generate_worker_hooks(configs_dir, session_id, api_base)

    # Always regenerate bin scripts from templates
    logger.info("Regenerating local bin scripts at %s", tmp_dir)
    deploy_worker_scripts(tmp_dir, session_id, api_base)

    # Always regenerate skills to .claude/commands/ (skip disabled built-ins)
    disabled_builtin_names: set[str] = set()
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT name FROM skill_overrides WHERE enabled = 0 AND target = 'worker'",
            ).fetchall()
            disabled_builtin_names = {r["name"] for r in rows}
        except Exception:
            pass  # Table may not exist yet

    skills_src = get_worker_skills_dir()
    local_skills_dir = os.path.join(tmp_dir, ".claude", "commands")
    # Clear stale skill files before repopulating
    if os.path.isdir(local_skills_dir):
        for f in os.listdir(local_skills_dir):
            if f.endswith(".md"):
                os.remove(os.path.join(local_skills_dir, f))
    if skills_src and os.path.isdir(skills_src):
        os.makedirs(local_skills_dir, exist_ok=True)
        for skill_file in os.listdir(skills_src):
            if skill_file.endswith(".md"):
                skill_name = os.path.splitext(skill_file)[0]
                if skill_name in disabled_builtin_names:
                    continue
                shutil.copy2(
                    os.path.join(skills_src, skill_file),
                    os.path.join(local_skills_dir, skill_file),
                )
        logger.info("Regenerated %d built-in skills at %s", len(os.listdir(local_skills_dir)), local_skills_dir)

    # Deploy custom skills from DB
    if conn is not None:
        custom_skills = _get_custom_skills_from_conn(conn, "worker")
        if custom_skills:
            os.makedirs(local_skills_dir, exist_ok=True)
            deploy_custom_skills(local_skills_dir, custom_skills)
            logger.info("Regenerated %d custom skills at %s", len(custom_skills), local_skills_dir)


def _copy_configs_to_remote(host: str, tmp_dir: str, remote_tmp_dir: str, session_name: str):
    """Copy settings.json, hooks, bin scripts, and skills to remote host via direct SSH.

    This ensures the remote configs and scripts exist, which may have been cleared if /tmp was wiped.
    Called before both reattach and new screen creation.
    Uses direct SSH subprocess (bypasses tmux/screen for reliability).
    """
    import subprocess

    # Copy entire directory to remote via direct SSH
    if not _copy_dir_to_remote_ssh(tmp_dir, host, remote_tmp_dir):
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
    # Clear stale skills first, then copy current set.
    skills_copy_cmd = f"rm -f ~/.claude/commands/*.md 2>/dev/null; mkdir -p ~/.claude/commands && cp {remote_tmp_dir}/.claude/commands/*.md ~/.claude/commands/ 2>/dev/null || true"
    subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, skills_copy_cmd],
        capture_output=True,
        timeout=30,
    )

    logger.info("Reconnect %s: copied all files to remote via direct SSH (including skills to ~/.claude/commands/)", session_name)


def reconnect_tunnel_only(conn, session, tmux_sess: str, api_port: int, repo, tunnel_manager=None) -> bool:
    """Reconnect just the SSH tunnel without touching the main worker window.

    Use this when SSH/screen/Claude are all running fine but the tunnel died.
    This avoids typing commands into Claude.

    Uses ReverseTunnelManager for subprocess-based tunnel management.

    Args:
        conn: Database connection
        session: Session object
        tmux_sess: tmux session name (unused, kept for backward compat)
        api_port: API port for tunnel
        repo: Sessions repository
        tunnel_manager: ReverseTunnelManager instance

    Returns:
        True if tunnel was successfully reconnected, False otherwise
    """
    logger.info("Reconnect tunnel only for %s", session.name)

    if tunnel_manager is None:
        logger.error("No tunnel_manager provided, cannot reconnect tunnel for %s", session.name)
        return False

    try:
        new_pid = tunnel_manager.restart_tunnel(session.id, session.name, session.host)
        if new_pid:
            repo.update_session(conn, session.id, tunnel_pid=new_pid)
            logger.info("Tunnel reconnected for %s (pid=%d)", session.name, new_pid)
            return True
        else:
            logger.warning("Tunnel reconnect failed for %s", session.name)
            return False
    except Exception as e:
        logger.error("Failed to reconnect tunnel for %s: %s", session.name, e)
        return False


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


# =============================================================================
# Main Reconnect — rdev Workers (Sequential Pipeline)
# =============================================================================

def reconnect_remote_worker(conn, session, tmux_sess: str, tmux_win: str, api_port: int, tmp_dir: str, repo, tunnel_manager=None):
    """Reconnect a remote worker (rdev or generic SSH) using the sequential pipeline.

    Each step fixes one layer, then evaluates the next.  The critical
    invariant is: **never send commands to a tmux pane that has a TUI running.**

    Steps:
      0. Acquire per-session lock
      1. Check pane safety (TUI + SSH alive — non-intrusive)
      2. Fix tunnel if dead (subprocess only)
      3. Ensure SSH connection (clean pane → ssh/rdev ssh → wait for prompt)
      4. Copy configs to remote (subprocess SSH)
      5. Check screen/Claude status (safe: at shell prompt)
      6. Act: reattach / reattach+launch / create+launch
    """
    from orchestrator.terminal import ssh
    from orchestrator.terminal.manager import ensure_window
    from orchestrator.terminal.session import _install_screen_if_needed
    from orchestrator.session.health import check_screen_and_claude_remote, check_worker_ssh_alive

    remote_tmp_dir = f"/tmp/orchestrator/workers/{session.name}"
    screen_name = get_screen_session_name(session.id)

    # ── Step 0: Acquire per-session lock ──────────────────────────────────
    lock = get_reconnect_lock(session.id)
    if not lock.acquire(timeout=5):
        logger.warning("Reconnect %s: another reconnect in progress, skipping", session.name)
        return

    try:
        os.makedirs(tmp_dir, exist_ok=True)
        ensure_window(tmux_sess, tmux_win, cwd=tmp_dir)

        # ── Step 1: Is the pane safe to interact with? ────────────────────
        tui_active = check_tui_running_in_pane(tmux_sess, tmux_win)
        ssh_alive = check_worker_ssh_alive(tmux_sess, tmux_win, session.host)

        logger.info(
            "Reconnect %s: Step 1 — tui_active=%s, ssh_alive=%s",
            session.name, tui_active, ssh_alive,
        )

        if tui_active and ssh_alive:
            # Claude is probably running fine.  Verify via subprocess SSH.
            remote_status, reason = check_screen_and_claude_remote(
                session.host, session.id, tmux_sess=None, tmux_win=None,
            )
            if remote_status == "alive":
                # Everything is fine!  Just fix tunnel if needed.
                if not (tunnel_manager and tunnel_manager.is_alive(session.id)):
                    _ensure_tunnel(session, tunnel_manager, repo, conn)
                repo.update_session(conn, session.id, status="waiting")
                logger.info("Reconnect %s: already alive, tunnel fixed if needed", session.name)
                return

            if remote_status == "screen_only":
                # TUI is alternate_on=1 because we're attached to GNU Screen,
                # but Claude has exited inside screen.  Detach from screen
                # (Ctrl-A d) to get back to a shell prompt, then continue the
                # pipeline normally from Step 2.
                logger.info(
                    "Reconnect %s: inside screen but Claude not running — "
                    "detaching from screen to continue reconnect",
                    session.name,
                )
                _detach_from_screen(tmux_sess, tmux_win)
                # Fall through to Step 2 — now at shell prompt outside screen

            else:
                # Truly unexpected state (e.g. "screen_detached", "dead" but
                # SSH is alive in the pane).  Don't touch the pane.
                logger.warning(
                    "Reconnect %s: TUI active + SSH alive but remote says %s (%s). "
                    "Not touching pane to avoid disruption.",
                    session.name, remote_status, reason,
                )
                repo.update_session(conn, session.id, status="error")
                return

        # If we reach here, either:
        #   - No TUI (shell prompt visible) → safe to send commands
        #   - TUI active but SSH dead → stale screen, _clean_pane_for_ssh handles it

        # ── Step 2: Fix tunnel if dead ────────────────────────────────────
        if not (tunnel_manager and tunnel_manager.is_alive(session.id)):
            _ensure_tunnel(session, tunnel_manager, repo, conn)

        # ── Step 3: Ensure SSH connection ─────────────────────────────────
        if not ssh_alive:
            logger.info("Reconnect %s: Step 3 — SSH dead, cleaning pane and reconnecting", session.name)
            _clean_pane_for_ssh(tmux_sess, tmux_win, cwd=tmp_dir)
            ssh.remote_connect(tmux_sess, tmux_win, session.host)
            if not ssh.wait_for_prompt(tmux_sess, tmux_win, timeout=60):
                raise RuntimeError(f"Timed out waiting for shell prompt on {session.host}")
            time.sleep(1)
        # ✓ We are now guaranteed at a remote shell prompt.

        # ── Step 4: Ensure configs on remote ──────────────────────────────
        api_base = f"http://127.0.0.1:{api_port}"
        _ensure_local_configs_exist(tmp_dir, session.id, api_base, conn=conn)
        _copy_configs_to_remote(session.host, tmp_dir, remote_tmp_dir, session.name)

        # ── Step 5: Check screen/Claude status (safe: at shell prompt) ────
        if not _install_screen_if_needed(tmux_sess, tmux_win):
            raise RuntimeError(f"Reconnect {session.name}: screen not available and could not be installed")

        screen_exists, claude_running = check_screen_exists_via_tmux(
            tmux_sess, tmux_win, screen_name, session.id,
        )
        logger.info(
            "Reconnect %s: Step 5 — screen_exists=%s, claude_running=%s",
            session.name, screen_exists, claude_running,
        )

        # ── Step 6: Act on findings ───────────────────────────────────────
        if screen_exists and claude_running:
            safe_send_keys(tmux_sess, tmux_win, f"screen -rd {screen_name}", enter=True)
            repo.update_session(conn, session.id, status="waiting")
            logger.info("Reconnect %s: SUCCESS — reattached to screen with Claude", session.name)

        elif screen_exists and not claude_running:
            safe_send_keys(tmux_sess, tmux_win, f"screen -rd {screen_name}", enter=True)
            time.sleep(1)
            _launch_claude_in_screen(
                tmux_sess, tmux_win, session, tmp_dir, remote_tmp_dir, repo, conn,
            )

        else:  # no screen
            _install_screen_if_needed(tmux_sess, tmux_win)
            safe_send_keys(tmux_sess, tmux_win, f"screen -S {screen_name}", enter=True)
            time.sleep(2)
            _launch_claude_in_screen(
                tmux_sess, tmux_win, session, tmp_dir, remote_tmp_dir, repo, conn,
            )

    except TUIActiveError as e:
        logger.error("Reconnect %s: TUI guard blocked send_keys: %s", session.name, e)
        repo.update_session(conn, session.id, status="error")
    except Exception:
        logger.exception("Reconnect failed for %s", session.name)
        raise
    finally:
        lock.release()


# =============================================================================
# Main Reconnect — Local Workers
# =============================================================================

def reconnect_local_worker(session, tmux_sess: str, tmux_win: str, api_port: int, tmp_dir: str):
    """Reconnect a local worker with TUI guard.

    Local workers have no SSH/screen/tunnel.  The main concern is avoiding
    sending commands into a running Claude TUI.
    """
    from orchestrator.terminal.manager import ensure_window
    from orchestrator.session.health import check_claude_process_local

    lock = get_reconnect_lock(session.id)
    if not lock.acquire(timeout=5):
        logger.warning("Reconnect local %s: another reconnect in progress, skipping", session.name)
        return

    try:
        os.makedirs(tmp_dir, exist_ok=True)
        ensure_window(tmux_sess, tmux_win, cwd=tmp_dir)

        # Check if Claude is still running
        if check_tui_running_in_pane(tmux_sess, tmux_win):
            alive, _ = check_claude_process_local(session.id)
            if alive:
                logger.info("Reconnect local %s: Claude still running, nothing to do", session.name)
                return
            # TUI showing but Claude dead — exit dead TUI
            send_keys(tmux_sess, tmux_win, "C-c", enter=False)
            time.sleep(0.5)
            send_keys(tmux_sess, tmux_win, "", enter=True)
            time.sleep(0.5)

        # Now at shell prompt — safe to send commands via safe_send_keys
        api_base = f"http://127.0.0.1:{api_port}"
        _ensure_local_configs_exist(tmp_dir, session.id, api_base)

        path_export = get_path_export_command(os.path.join(tmp_dir, "bin"))
        safe_send_keys(tmux_sess, tmux_win, path_export, enter=True)
        time.sleep(0.3)

        if session.work_dir:
            safe_send_keys(tmux_sess, tmux_win, f"cd {shlex.quote(session.work_dir)}", enter=True)
            time.sleep(0.3)

        # Use Claude's tracked session ID if available, otherwise orchestrator ID
        target_id = session.claude_session_id or session.id
        has_tracked_id = session.claude_session_id is not None

        session_exists = _check_claude_session_exists_local(target_id)
        session_arg = _get_claude_session_arg(target_id, session_exists, has_tracked_id)
        logger.info("Reconnect local %s: Claude session exists=%s, using arg: %s", session.name, session_exists, session_arg)

        settings_file = os.path.join(tmp_dir, "configs", "settings.json")
        claude_args = [
            session_arg,
            "--dangerously-skip-permissions",
            f"--settings {shlex.quote(settings_file)}",
        ]

        # Write prompt to file and load via $(cat) to avoid pasting large content through tmux
        prompt = get_worker_prompt(session.id)
        prompt_file = os.path.join(tmp_dir, "prompt.md")
        if prompt:
            with open(prompt_file, "w") as f:
                f.write(prompt)
            claude_args.append(f'--append-system-prompt "$(cat {shlex.quote(prompt_file)})"')

        claude_cmd = f"claude {' '.join(claude_args)}"
        safe_send_keys(tmux_sess, tmux_win, claude_cmd, enter=True)

        # Verify Claude actually started — recover if -r failed
        started, error_output = _verify_claude_started(tmux_sess, tmux_win)
        if not started:
            logger.warning(
                "Reconnect local %s: Claude failed to start (arg=%s, output=%s). "
                "Retrying with --session-id to create a fresh session.",
                session.name, session_arg, error_output[:300],
            )
            # Clean up the failed command prompt
            send_keys(tmux_sess, tmux_win, "C-c", enter=False)
            time.sleep(0.5)
            send_keys(tmux_sess, tmux_win, "", enter=True)
            time.sleep(0.5)

            # Retry with --session-id (creates a new conversation)
            fallback_arg = f"--session-id {target_id}"
            claude_args_retry = [
                fallback_arg,
                "--dangerously-skip-permissions",
                f"--settings {shlex.quote(settings_file)}",
            ]
            if prompt:
                claude_args_retry.append(f'--append-system-prompt "$(cat {shlex.quote(prompt_file)})"')

            claude_cmd_retry = f"claude {' '.join(claude_args_retry)}"
            logger.info("Reconnect local %s: retrying with: %s", session.name, fallback_arg)
            safe_send_keys(tmux_sess, tmux_win, claude_cmd_retry, enter=True)

            # Check the retry — if this also fails, let the health check loop handle it
            retry_started, retry_output = _verify_claude_started(tmux_sess, tmux_win)
            if not retry_started:
                logger.error(
                    "Reconnect local %s: retry also failed (output=%s). Giving up.",
                    session.name, retry_output[:300],
                )
                return

        logger.info("Launched Claude Code for local worker %s (session_id=%s)", session.name, session.id)
    finally:
        lock.release()


# Backward-compat alias
reconnect_rdev_worker = reconnect_remote_worker
