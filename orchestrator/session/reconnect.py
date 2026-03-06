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
import re
import shlex
import subprocess
import threading
import time

from orchestrator.agents import get_path_export_command, get_worker_prompt
from orchestrator.session.health import (
    check_tui_running_in_pane,
    get_screen_session_name,
)
from orchestrator.terminal.manager import (
    capture_output,
    dismiss_trust_prompt,
    kill_window,
    send_keys,
)
from orchestrator.terminal.markers import MarkerCommand
from orchestrator.terminal.session import (
    _copy_dir_to_remote_ssh,
    _kill_orphaned_screen,
    ensure_rdev_node,
)

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
        raise TUIActiveError(f"TUI running in {tmux_sess}:{tmux_win}, refusing send_keys")
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


def _verify_pane_responsive(
    tmux_sess: str, tmux_win: str, timeout: float = 3.0, poll_interval: float = 0.5
) -> bool:
    """Check if the pane responds to a marker command within *timeout* seconds.

    Sends ``echo OK`` wrapped with :class:`MarkerCommand` markers and polls
    for the marker in captured output.  Returns ``True`` if the pane echoed
    the expected marker (i.e. it is at a shell prompt and responsive), or
    ``False`` if the marker never appeared (pane is stuck).
    """
    cmd = MarkerCommand("echo OK", prefix="PANE_CHK")
    send_keys(tmux_sess, tmux_win, cmd.full_command, enter=True)

    elapsed = 0.0
    while elapsed < timeout:
        time.sleep(poll_interval)
        elapsed += poll_interval
        output = capture_output(tmux_sess, tmux_win, lines=15)
        result = cmd.parse_result(output)
        if result is not None and "OK" in result:
            return True

    logger.warning(
        "_verify_pane_responsive: pane %s:%s did not respond within %.1fs",
        tmux_sess,
        tmux_win,
        timeout,
    )
    return False


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
            logger.info(
                "_clean_pane_for_ssh: TUI still active after Ctrl-C, killing and recreating pane"
            )
            kill_window(tmux_sess, tmux_win)
            ensure_window(tmux_sess, tmux_win, cwd=cwd)
            return

    # Normal case: Ctrl-C + Enter to ensure clean shell prompt
    send_keys(tmux_sess, tmux_win, "C-c", enter=False)
    time.sleep(0.3)
    send_keys(tmux_sess, tmux_win, "", enter=True)
    time.sleep(0.5)

    # Verify the pane actually responded (catches stuck `rdev ssh` that ignores Ctrl-C)
    if not _verify_pane_responsive(tmux_sess, tmux_win):
        logger.info(
            "_clean_pane_for_ssh: pane %s:%s unresponsive after Ctrl-C, killing and recreating",
            tmux_sess,
            tmux_win,
        )
        kill_window(tmux_sess, tmux_win)
        ensure_window(tmux_sess, tmux_win, cwd=cwd)


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


def _cleanup_stale_claude_session_local(session_id: str):
    """Delete stale Claude session files for the given session ID locally.

    This resolves the deadlock where ``-r`` fails ("No conversation found")
    *and* ``--session-id`` fails ("already in use").  The ``.jsonl`` file
    exists (so ``--session-id`` refuses to overwrite) but is corrupt or empty
    (so ``-r`` cannot load it).  Deleting it lets ``--session-id`` succeed.

    Also kills any orphaned Claude processes still referencing this session ID.
    """
    import glob
    import re

    # 1. Delete stale .jsonl session files
    claude_dir = os.path.expanduser("~/.claude/projects")
    pattern_path = os.path.join(claude_dir, "*", f"{session_id}.jsonl")
    for f in glob.glob(pattern_path):
        try:
            os.remove(f)
            logger.info("Deleted stale Claude session file: %s", f)
        except Exception:
            logger.debug("Failed to delete %s", f, exc_info=True)

    # 2. Kill orphaned processes (secondary defense)
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        proc_pattern = re.compile(r"claude\s+(-r|--|--settings)")
        for line in result.stdout.splitlines():
            if session_id in line and proc_pattern.search(line):
                parts = line.split()
                if len(parts) >= 2:
                    pid = parts[1]
                    logger.info(
                        "Killing orphaned local Claude process pid=%s for session %s: %s",
                        pid,
                        session_id,
                        line.strip()[:120],
                    )
                    subprocess.run(["kill", pid], capture_output=True, timeout=5)
    except Exception:
        logger.debug("_cleanup_stale_claude_session_local: process kill failed", exc_info=True)


def _cleanup_stale_claude_session_remote(host: str, session_id: str):
    """Delete stale Claude session files and kill orphaned processes on a remote host.

    Same purpose as ``_cleanup_stale_claude_session_local`` but executed via SSH.
    """
    try:
        cleanup_cmd = (
            # Delete stale session files
            f"find ~/.claude/projects -name '{session_id}.jsonl' -delete 2>/dev/null; "
            # Kill orphaned Claude processes
            f"ps aux | grep -v grep "
            f"| grep -E 'claude (-r|--|--settings)' "
            f"| grep '{session_id}' "
            f"| awk '{{print $2}}' "
            f"| xargs -r kill 2>/dev/null || true"
        )
        subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, cleanup_cmd],
            capture_output=True,
            text=True,
            timeout=15,
        )
        logger.info("Cleaned up stale Claude session on %s for session %s", host, session_id)
    except Exception:
        logger.debug("_cleanup_stale_claude_session_remote failed", exc_info=True)


def _check_claude_session_exists_remote(host: str, session_id: str) -> bool:
    """Check if a Claude session file exists on remote host via SSH.

    Claude stores sessions in ~/.claude/projects/<path>/<session_id>.jsonl
    We check if any .jsonl file with this session_id exists.

    Returns True if session exists, False otherwise.
    """
    try:
        # Search for session file in any project directory
        check_cmd = (
            f"ls ~/.claude/projects/*/{session_id}.jsonl 2>/dev/null"
            " && echo SESSION_EXISTS || echo SESSION_MISSING"
        )
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, check_cmd],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stdout + result.stderr
        exists = (
            "SESSION_EXISTS" in output
            and "SESSION_MISSING" not in output.split("SESSION_EXISTS")[-1]
        )
        logger.debug(
            "Claude session check on %s for %s: exists=%s (output=%r)",
            host,
            session_id,
            exists,
            output,
        )
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
    logger.debug(
        "Claude session check local for %s: exists=%s (matches=%s)", session_id, exists, matches
    )
    return exists


def _get_claude_session_arg(
    session_id: str, session_exists: bool, has_tracked_id: bool = False
) -> str:
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
    tmux_sess: str,
    tmux_win: str,
    timeout: int = 10,
    poll_interval: float = 2.0,
) -> tuple[bool, str]:
    """Check that Claude's TUI actually started after sending the launch command.

    Polls using two methods:
    1. Alternate screen buffer (``#{alternate_on}``) — works for remote
       workers inside GNU Screen.
    2. Process-tree walk from the tmux pane PID — works for local workers
       where Claude Code does not use the alternate screen buffer.

    Returns:
        (started, error_output) — *started* is True when Claude is detected.
        When False, *error_output* contains the last terminal lines for diagnosis.
    """
    from orchestrator.session.health import _get_pane_pid, _has_claude_in_process_tree

    start = time.time()
    while time.time() - start < timeout:
        time.sleep(poll_interval)
        # Method 1: alternate screen (works inside GNU Screen on remote)
        if check_tui_running_in_pane(tmux_sess, tmux_win):
            return True, ""
        # Method 2: process tree (works for local Claude Code)
        pane_pid = _get_pane_pid(tmux_sess, tmux_win)
        if pane_pid is not None and _has_claude_in_process_tree(pane_pid):
            return True, ""

    # Claude never appeared — capture output to check for errors
    output = capture_output(tmux_sess, tmux_win, lines=15)
    return False, output


def _verify_claude_running_via_ssh(
    host: str,
    session_id: str,
    timeout: int = 15,
    poll_interval: float = 3.0,
    claude_session_id: str | None = None,
) -> tuple[bool, str]:
    """Verify Claude is actually running on remote by checking the process via SSH.

    Unlike ``_verify_claude_started`` (which checks the alternate screen buffer),
    this uses a subprocess SSH connection to look for the Claude process.  This is
    necessary inside GNU Screen sessions where ``#{alternate_on}`` is always 1,
    making TUI detection unreliable.

    Returns:
        (running, reason) — *running* is True when the Claude process is found.
    """
    from orchestrator.session.health import check_screen_and_claude_remote

    last_status = ""
    last_reason = ""
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(poll_interval)
        status, reason = check_screen_and_claude_remote(
            host,
            session_id,
            tmux_sess=None,
            tmux_win=None,
            claude_session_id=claude_session_id,
        )
        last_status = status
        last_reason = reason
        if status == "alive":
            return True, ""

    return False, f"Process check: {last_status} - {last_reason}"


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
    # Set up environment — on rdev, include node-bin for Node 24 symlinks
    from orchestrator.terminal.ssh import is_rdev_host

    if is_rdev_host(session.host):
        path_export = (
            f'export PATH="{remote_tmp_dir}/node-bin:{remote_tmp_dir}/bin:$HOME/.local/bin:$PATH"'
        )
    else:
        path_export = get_path_export_command(f"{remote_tmp_dir}/bin")
    send_keys(tmux_sess, tmux_win, path_export, enter=True)
    time.sleep(0.3)

    if session.work_dir:
        send_keys(tmux_sess, tmux_win, f"cd {session.work_dir}", enter=True)
        time.sleep(0.3)

    # Copy prompt to remote (avoids pasting large content through tmux)
    remote_prompt_path = ensure_prompt_on_remote(tmux_sess, tmux_win, session.id, remote_tmp_dir)

    # Use Claude's tracked session ID if available, otherwise orchestrator ID
    target_id = session.claude_session_id or session.id
    has_tracked_id = session.claude_session_id is not None

    session_exists = _check_claude_session_exists_remote(session.host, target_id)
    session_arg = _get_claude_session_arg(target_id, session_exists, has_tracked_id)
    logger.info(
        "Reconnect %s: Claude session exists=%s, using arg: %s",
        session.name,
        session_exists,
        session_arg,
    )

    # Install Playwright plugin and configure CDP endpoint
    send_keys(tmux_sess, tmux_win, "claude plugin install playwright", enter=True)
    time.sleep(3)

    send_keys(
        tmux_sess,
        tmux_win,
        "export PLAYWRIGHT_MCP_CDP_ENDPOINT=http://localhost:9222",
        enter=True,
    )
    time.sleep(0.3)

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

    # Dismiss any "trust this folder" prompt that may appear after launch
    dismiss_trust_prompt(tmux_sess, tmux_win, session_id=session.id)

    # Verify Claude actually started — use SSH process check because TUI detection
    # (alternate_on) is unreliable inside screen sessions (screen itself uses
    # alternate screen buffer, so alternate_on is always 1).
    started, error_output = _verify_claude_running_via_ssh(
        session.host,
        session.id,
        claude_session_id=session.claude_session_id,
    )
    if not started:
        logger.warning(
            "Reconnect %s: Claude failed to start (arg=%s, check=%s). "
            "Retrying with --session-id to create a fresh session.",
            session.name,
            session_arg,
            error_output[:300],
        )
        # Clean up the failed command prompt
        send_keys(tmux_sess, tmux_win, "C-c", enter=False)
        time.sleep(0.5)
        send_keys(tmux_sess, tmux_win, "", enter=True)
        time.sleep(0.5)

        # Clean up stale session file + orphaned processes.
        # Without this, --session-id fails with "already in use".
        _cleanup_stale_claude_session_remote(session.host, target_id)
        time.sleep(1)

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

        # Dismiss any "trust this folder" prompt that may appear after launch
        dismiss_trust_prompt(tmux_sess, tmux_win, session_id=session.id)

        # Check the retry with SSH process check
        retry_started, retry_output = _verify_claude_running_via_ssh(
            session.host,
            session.id,
            claude_session_id=session.claude_session_id,
        )
        if not retry_started:
            logger.error(
                "Reconnect %s: retry also failed (%s). Giving up.",
                session.name,
                retry_output[:300],
            )
            repo.update_session(conn, session.id, status="error")
            return

    repo.update_session(conn, session.id, status="waiting")

    # Detect work_dir if not set (e.g., created without specifying a path)
    if not session.work_dir:
        from orchestrator.api.routes.files import _detect_remote_work_dir

        time.sleep(3)  # Give Claude a moment to start
        detected = _detect_remote_work_dir(session.host, session.id)
        if detected:
            repo.update_session(conn, session.id, work_dir=detected)
            logger.info("Reconnect %s: detected work_dir: %s", session.name, detected)

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


def _read_cdp_port_from_lib(tmp_dir: str) -> int:
    """Read ORCH_CDP_PORT from the deployed lib.sh file."""
    lib_path = os.path.join(tmp_dir, "bin", "lib.sh")
    if os.path.exists(lib_path):
        with open(lib_path) as f:
            for line in f:
                if "ORCH_CDP_PORT" in line:
                    m = re.search(r":-(\d+)", line)
                    if m:
                        return int(m.group(1))
    return 9222  # fallback default


def _ensure_local_configs_exist(
    tmp_dir: str, session_id: str, api_base: str = "http://127.0.0.1:8093", conn=None
):
    """Regenerate local configs from templates.

    Delegates to the SOT function ``deploy_worker_tmp_contents`` which handles
    hooks, settings, bin scripts, skills, and prompt.md in a single call.
    """
    from orchestrator.agents.deploy import deploy_worker_tmp_contents

    cdp_port = _read_cdp_port_from_lib(tmp_dir)
    deploy_worker_tmp_contents(
        tmp_dir,
        session_id,
        api_base=api_base,
        cdp_port=cdp_port,
        browser_headless=False,
        conn=conn,
    )
    logger.info("Regenerated local configs at %s via SOT", tmp_dir)


def _copy_configs_to_remote(host: str, tmp_dir: str, remote_tmp_dir: str, session_name: str):
    """Copy settings.json, hooks, bin scripts, and skills to remote host via direct SSH.

    This ensures the remote configs and scripts exist, which may have been
    cleared if /tmp was wiped.
    Called before both reattach and new screen creation.
    Uses direct SSH subprocess (bypasses tmux/screen for reliability).
    """
    import subprocess

    # Copy entire directory to remote via direct SSH
    if not _copy_dir_to_remote_ssh(tmp_dir, host, remote_tmp_dir):
        raise RuntimeError(f"Failed to copy configs to remote via SSH: {host}:{remote_tmp_dir}")

    # Make scripts executable via SSH subprocess
    chmod_cmd = (
        f"chmod +x {remote_tmp_dir}/bin/* 2>/dev/null;"
        f" chmod +x {remote_tmp_dir}/configs/hooks/*.sh 2>/dev/null"
    )
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
    skills_copy_cmd = (
        "rm -f ~/.claude/commands/*.md 2>/dev/null;"
        " mkdir -p ~/.claude/commands"
        f" && cp {remote_tmp_dir}/.claude/commands/*.md"
        " ~/.claude/commands/ 2>/dev/null || true"
    )
    subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, skills_copy_cmd],
        capture_output=True,
        timeout=30,
    )

    logger.info(
        "Reconnect %s: copied all files to remote via direct SSH"
        " (including skills to ~/.claude/commands/)",
        session_name,
    )


def reconnect_tunnel_only(
    conn, session, tmux_sess: str, api_port: int, repo, tunnel_manager=None
) -> bool:
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
    send_keys(
        tmux_sess,
        tmux_win,
        f"cat > {remote_prompt_path} << 'ORCHEOF'\n{prompt}\nORCHEOF",
        enter=True,
    )
    time.sleep(0.3)
    logger.info("Copied worker prompt to remote: %s", remote_prompt_path)

    return remote_prompt_path


def get_prompt_load_arg(remote_prompt_path: str) -> str:
    """Get the claude CLI argument to load prompt from file on remote."""
    return f'--append-system-prompt "$(cat {remote_prompt_path})"'


def check_screen_exists_via_tmux(
    tmux_sess: str, tmux_win: str, screen_name: str, session_id: str
) -> tuple[bool, bool, str | None]:
    """Check if screen session exists and if Claude is running inside it.

    Sends commands via tmux to check screen status on the remote host.
    Uses the markers module for safe parsing (avoids command echo false positives).

    Args:
        screen_name: The screen session name (e.g., "claude-{session_id}")
        session_id: The orchestrator session ID (used to find Claude process)

    Returns:
        (screen_exists, claude_running, screen_pid) — *screen_pid* is the
        full ``pid.name`` identifier (e.g. ``12345.claude-abc``) of the
        newest matching session, or ``None`` when no session is found.
        Using *screen_pid* for ``screen -rd`` avoids the "several suitable
        screens" ambiguity when duplicates exist.
    """
    from orchestrator.terminal.markers import MarkerCommand, parse_between_markers

    # Build the check command — outputs SCREEN_EXISTS/MISSING, the newest
    # session id (pid.name), and CLAUDE_RUNNING/MISSING.
    # We anchor the grep with a word-boundary (\b) to avoid substring matches.
    # The awk+sort+tail extracts the newest (highest-PID) session id.
    screen_check = (
        f"screen -ls 2>/dev/null | grep -w '{screen_name}' "
        f"| awk '{{print $1}}' | sort -t. -k1 -n | tail -1 | "
        f'{{ read sid; if [ -n "$sid" ]; then '
        f'echo SCREEN_EXISTS; echo "SCREEN_PID=$sid"; '
        f"else echo SCREEN_MISSING; fi; }}"
    )
    claude_check = (
        f"ps aux | grep -v grep "
        f"| grep -E 'claude (-r|--|--settings)' "
        f"| grep -q '{session_id}' "
        f"&& echo CLAUDE_RUNNING || echo CLAUDE_MISSING"
    )
    inner_cmd = f"({screen_check}) && ({claude_check})"
    cmd = MarkerCommand(inner_cmd, prefix="SCRCHK")

    # Debug: log the actual command being sent
    logger.info("Screen check command: %s", cmd.full_command)

    send_keys(tmux_sess, tmux_win, cmd.full_command, enter=True)
    time.sleep(1.5)

    output = capture_output(tmux_sess, tmux_win, lines=20)
    logger.debug("Screen check raw output: %r", output[:500] if output else None)

    # Parse result between markers (safe from command echo)
    result = parse_between_markers(output, cmd.start_marker, cmd.end_marker)
    logger.debug(
        "Screen check parsed result: %r (start=%s, end=%s)",
        result,
        cmd.start_marker,
        cmd.end_marker,
    )

    screen_exists = False
    claude_running = False
    screen_pid: str | None = None

    if result:
        # Check for exact matches in the parsed result
        for line in result.splitlines():
            stripped = line.strip()
            logger.debug("Screen check line: %r", stripped)
            if stripped == "SCREEN_EXISTS":
                screen_exists = True
            elif stripped.startswith("SCREEN_PID="):
                screen_pid = stripped.split("=", 1)[1]
            elif stripped == "CLAUDE_RUNNING":
                claude_running = True

    logger.info(
        "Screen check via tmux: exists=%s, claude=%s, pid=%s (result=%r)",
        screen_exists,
        claude_running,
        screen_pid,
        result,
    )
    return screen_exists, claude_running, screen_pid


# =============================================================================
# RWS Reconnect Helper
# =============================================================================


def _reconnect_rws_for_host(session) -> None:
    """Re-establish the RWS forward tunnel after SSH reconnects.

    If an RWS daemon client exists for this host, reconnect the forward tunnel
    (which died when SSH dropped).  If the daemon itself is dead (e.g. host
    rebooted), the next `ensure_rws_starting()` will redeploy it.

    After reconnecting, checks the daemon's version via ``server_info`` and
    forces a full redeploy if the daemon is outdated (e.g. new actions were
    added to the daemon script since the daemon was started).

    Also checks if any active interactive CLI PTYs are still alive.
    """
    from orchestrator.terminal.interactive import _active_clis, get_active_cli
    from orchestrator.terminal.remote_worker_server import (
        _SCRIPT_HASH,
        _server_pool,
        ensure_rws_starting,
        force_restart_server,
    )

    host = session.host
    rws = _server_pool.get(host)

    if rws is not None:
        # Try to reconnect the existing client's tunnel
        try:
            rws.reconnect_tunnel()
            logger.info("Reconnect %s: RWS forward tunnel re-established", session.name)
        except Exception:
            logger.warning(
                "Reconnect %s: RWS tunnel reconnect failed, will redeploy",
                session.name,
                exc_info=True,
            )
            # Remove stale client so ensure_rws_starting() redeploys
            try:
                rws.stop()
            except Exception:
                pass
            _server_pool.pop(host, None)
            ensure_rws_starting(host)
            return  # PTY check below needs a live RWS; will happen next cycle

        # Check daemon version — if outdated, force a full redeploy so
        # new actions (e.g. browser_start) become available.
        try:
            info = rws.execute({"action": "server_info"}, timeout=5)
            daemon_version = info.get("version", "")
            if daemon_version != _SCRIPT_HASH:
                logger.warning(
                    "Reconnect %s: RWS daemon outdated (daemon=%s, expected=%s), redeploying",
                    session.name,
                    daemon_version[:8],
                    _SCRIPT_HASH[:8],
                )
                try:
                    force_restart_server(host)
                    logger.info("Reconnect %s: RWS daemon redeployed successfully", session.name)
                except Exception:
                    logger.warning(
                        "Reconnect %s: RWS daemon redeploy failed",
                        session.name,
                        exc_info=True,
                    )
            else:
                logger.debug(
                    "Reconnect %s: RWS daemon version OK (%s)",
                    session.name,
                    daemon_version[:8],
                )
        except Exception:
            logger.debug(
                "Reconnect %s: could not check RWS daemon version",
                session.name,
                exc_info=True,
            )
    else:
        # No existing RWS — kick off a fresh start (non-blocking)
        ensure_rws_starting(host)

    # Check if interactive CLI PTY is still alive
    cli = get_active_cli(session.id)
    if cli and cli.remote_pty_id and cli.rws_host:
        try:
            rws_new = _server_pool.get(host)
            if rws_new:
                resp = rws_new.execute({"action": "pty_list"})
                ptys = resp.get("ptys", [])
                alive = any(p["pty_id"] == cli.remote_pty_id and p["alive"] for p in ptys)
                if not alive:
                    logger.info(
                        "Reconnect %s: remote PTY %s is dead, cleaning up",
                        session.name,
                        cli.remote_pty_id,
                    )
                    _active_clis.pop(session.id, None)
                else:
                    logger.info(
                        "Reconnect %s: remote PTY %s still alive",
                        session.name,
                        cli.remote_pty_id,
                    )
        except Exception:
            logger.debug(
                "Reconnect %s: could not check PTY status",
                session.name,
                exc_info=True,
            )


# =============================================================================
# Main Reconnect — rdev Workers (Sequential Pipeline)
# =============================================================================


def reconnect_remote_worker(
    conn,
    session,
    tmux_sess: str,
    tmux_win: str,
    api_port: int,
    tmp_dir: str,
    repo,
    tunnel_manager=None,
):
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
    from orchestrator.session.health import check_screen_and_claude_remote, check_worker_ssh_alive
    from orchestrator.terminal import ssh
    from orchestrator.terminal.manager import ensure_window
    from orchestrator.terminal.session import _install_screen_if_needed

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
            session.name,
            tui_active,
            ssh_alive,
        )

        if tui_active and ssh_alive:
            # Claude is probably running fine.  Verify via subprocess SSH.
            remote_status, reason = check_screen_and_claude_remote(
                session.host,
                session.id,
                tmux_sess=None,
                tmux_win=None,
                claude_session_id=session.claude_session_id,
            )
            if remote_status == "alive":
                # Everything is fine!  Just fix tunnel if needed.
                if not (tunnel_manager and tunnel_manager.is_alive(session.id)):
                    _ensure_tunnel(session, tunnel_manager, repo, conn)
                repo.update_session(conn, session.id, status="waiting")

                # Detect work_dir if not set
                if not session.work_dir:
                    from orchestrator.api.routes.files import _detect_remote_work_dir

                    detected = _detect_remote_work_dir(session.host, session.id)
                    if detected:
                        repo.update_session(conn, session.id, work_dir=detected)
                        logger.info("Reconnect %s: detected work_dir: %s", session.name, detected)

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
                    session.name,
                    remote_status,
                    reason,
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
            logger.info(
                "Reconnect %s: Step 3 — SSH dead, cleaning pane and reconnecting", session.name
            )
            _clean_pane_for_ssh(tmux_sess, tmux_win, cwd=tmp_dir)
            ssh.remote_connect(tmux_sess, tmux_win, session.host)
            if not ssh.wait_for_prompt(tmux_sess, tmux_win, timeout=60):
                # First attempt failed — kill pane and retry once with a clean slate
                logger.warning(
                    "Reconnect %s: Step 3 — first SSH attempt timed out, killing pane and retrying",
                    session.name,
                )
                kill_window(tmux_sess, tmux_win)
                ensure_window(tmux_sess, tmux_win, cwd=tmp_dir)
                ssh.remote_connect(tmux_sess, tmux_win, session.host)
                if not ssh.wait_for_prompt(tmux_sess, tmux_win, timeout=60):
                    raise RuntimeError(
                        f"Timed out waiting for shell prompt on {session.host} "
                        f"(after kill+recreate retry)"
                    )
            time.sleep(1)
        # ✓ We are now guaranteed at a remote shell prompt.

        # ── Step 3b: ensure Node 24 for Playwright ─────────────────────
        if ssh.is_rdev_host(session.host):
            ensure_rdev_node(tmux_sess, tmux_win, remote_tmp_dir)
        else:
            # Plain SSH: ensure Node 24 via volta (needed for Playwright plugin's npx)
            safe_send_keys(tmux_sess, tmux_win, "volta install node@24", enter=True)
            time.sleep(3)
            logger.info("Reconnect %s: installed Node 24 via volta for SSH worker", session.name)

        # ── Step 4: Ensure configs on remote ──────────────────────────────
        api_base = f"http://127.0.0.1:{api_port}"
        _ensure_local_configs_exist(tmp_dir, session.id, api_base, conn=conn)
        _copy_configs_to_remote(session.host, tmp_dir, remote_tmp_dir, session.name)

        # ── Step 5: Check screen/Claude status (safe: at shell prompt) ────
        if not _install_screen_if_needed(tmux_sess, tmux_win):
            raise RuntimeError(
                f"Reconnect {session.name}: screen not available and could not be installed"
            )

        screen_exists, claude_running, screen_pid = check_screen_exists_via_tmux(
            tmux_sess,
            tmux_win,
            screen_name,
            session.id,
        )
        logger.info(
            "Reconnect %s: Step 5 — screen_exists=%s, claude_running=%s, screen_pid=%s",
            session.name,
            screen_exists,
            claude_running,
            screen_pid,
        )

        # Reattach target: use pid.name when available (always unambiguous),
        # fall back to bare name if the check didn't capture a PID.
        reattach_target = screen_pid if screen_pid else screen_name

        # ── Step 6: Act on findings ───────────────────────────────────────
        if screen_exists and claude_running:
            safe_send_keys(tmux_sess, tmux_win, f"screen -rd {reattach_target}", enter=True)
            repo.update_session(conn, session.id, status="waiting")
            logger.info("Reconnect %s: SUCCESS — reattached to screen with Claude", session.name)

        elif screen_exists and not claude_running:
            safe_send_keys(tmux_sess, tmux_win, f"screen -rd {reattach_target}", enter=True)
            time.sleep(1)
            _launch_claude_in_screen(
                tmux_sess,
                tmux_win,
                session,
                tmp_dir,
                remote_tmp_dir,
                repo,
                conn,
            )

        else:  # no screen
            # Kill any residual sessions the check may have missed
            _kill_orphaned_screen(tmux_sess, tmux_win, screen_name)
            _install_screen_if_needed(tmux_sess, tmux_win)
            safe_send_keys(tmux_sess, tmux_win, f"screen -S {screen_name}", enter=True)
            time.sleep(2)
            _launch_claude_in_screen(
                tmux_sess,
                tmux_win,
                session,
                tmp_dir,
                remote_tmp_dir,
                repo,
                conn,
            )

        # ── Post-reconnect: Re-establish RWS forward tunnel ──────────────
        _reconnect_rws_for_host(session)

    except TUIActiveError as e:
        logger.error("Reconnect %s: TUI guard blocked send_keys: %s", session.name, e)
        repo.update_session(conn, session.id, status="error")
    except Exception:
        logger.exception("Reconnect failed for %s", session.name)
        try:
            repo.update_session(conn, session.id, status="error")
        except Exception:
            pass
        raise
    finally:
        lock.release()


# =============================================================================
# Main Reconnect — Local Workers
# =============================================================================


def reconnect_local_worker(
    session,
    tmux_sess: str,
    tmux_win: str,
    api_port: int,
    tmp_dir: str,
    conn=None,
) -> bool:
    """Reconnect a local worker with TUI guard.

    Local workers have no SSH/screen/tunnel.  The main concern is avoiding
    sending commands into a running Claude TUI.

    Args:
        conn: Optional DB connection for reading skills during config regeneration.
            When provided, custom skills and disabled overrides are read from DB.

    Returns:
        True if Claude was successfully (re)started, False otherwise.
        Exceptions still propagate to the caller for unexpected errors.
    """
    from orchestrator.session.health import check_claude_running_local
    from orchestrator.terminal.manager import ensure_window

    lock = get_reconnect_lock(session.id)
    if not lock.acquire(timeout=5):
        logger.warning("Reconnect local %s: another reconnect in progress, skipping", session.name)
        return False

    try:
        os.makedirs(tmp_dir, exist_ok=True)
        ensure_window(tmux_sess, tmux_win, cwd=tmp_dir)

        # Check if Claude is still running — use process-tree detection
        # (not alternate screen, since Claude Code doesn't use it locally).
        alive, _ = check_claude_running_local(
            session.id,
            session.claude_session_id,
            tmux_sess,
            tmux_win,
        )
        if alive:
            logger.info("Reconnect local %s: Claude still running, nothing to do", session.name)
            return True

        # Claude not running — make sure the pane is at a shell prompt.
        # If alternate screen is active (e.g. stale TUI), exit it first.
        if check_tui_running_in_pane(tmux_sess, tmux_win):
            send_keys(tmux_sess, tmux_win, "C-c", enter=False)
            time.sleep(0.5)
            send_keys(tmux_sess, tmux_win, "", enter=True)
            time.sleep(0.5)

        # Now at shell prompt — safe to send commands via safe_send_keys
        api_base = f"http://127.0.0.1:{api_port}"
        _ensure_local_configs_exist(tmp_dir, session.id, api_base, conn=conn)

        # Ensure Node 24 is the volta default (needed for Playwright plugin's npx)
        safe_send_keys(tmux_sess, tmux_win, "volta install node@24", enter=True)
        time.sleep(3)

        # Ensure the official Playwright plugin is installed
        safe_send_keys(tmux_sess, tmux_win, "claude plugin install playwright", enter=True)
        time.sleep(2)

        path_export = get_path_export_command(os.path.join(tmp_dir, "bin"))
        safe_send_keys(tmux_sess, tmux_win, path_export, enter=True)
        time.sleep(0.3)

        # Configure Playwright MCP via per-worker CDP proxy
        cdp_port = _read_cdp_port_from_lib(tmp_dir)
        from orchestrator.browser.cdp_worker_proxy import get_proxy_port, start_cdp_proxy

        proxy_port = get_proxy_port(session.id)
        if proxy_port is None:
            try:
                proxy_port = start_cdp_proxy(session.id, chrome_port=cdp_port)
            except Exception:
                logger.warning("CDP proxy failed for %s, falling back to direct", session.name)
                proxy_port = cdp_port
        safe_send_keys(
            tmux_sess,
            tmux_win,
            f"export PLAYWRIGHT_MCP_CDP_ENDPOINT=http://localhost:{proxy_port}",
            enter=True,
        )
        time.sleep(0.3)

        if session.work_dir:
            safe_send_keys(tmux_sess, tmux_win, f"cd {shlex.quote(session.work_dir)}", enter=True)
            time.sleep(0.3)

        # Use Claude's tracked session ID if available, otherwise orchestrator ID
        target_id = session.claude_session_id or session.id
        has_tracked_id = session.claude_session_id is not None

        session_exists = _check_claude_session_exists_local(target_id)
        session_arg = _get_claude_session_arg(target_id, session_exists, has_tracked_id)
        logger.info(
            "Reconnect local %s: Claude session exists=%s, using arg: %s",
            session.name,
            session_exists,
            session_arg,
        )

        settings_file = os.path.join(tmp_dir, "configs", "settings.json")
        claude_args = [
            session_arg,
            "--dangerously-skip-permissions",
            f"--settings {shlex.quote(settings_file)}",
        ]

        # Read prompt from SOT-deployed file (includes custom skills)
        prompt_file = os.path.join(tmp_dir, "prompt.md")
        if os.path.exists(prompt_file):
            claude_args.append(f'--append-system-prompt "$(cat {shlex.quote(prompt_file)})"')

        claude_cmd = f"claude {' '.join(claude_args)}"
        safe_send_keys(tmux_sess, tmux_win, claude_cmd, enter=True)

        # Dismiss any "trust this folder" prompt that may appear after launch
        dismiss_trust_prompt(tmux_sess, tmux_win, session_id=session.id)

        # Verify Claude actually started — recover if -r failed
        started, error_output = _verify_claude_started(tmux_sess, tmux_win)
        if not started:
            logger.warning(
                "Reconnect local %s: Claude failed to start (arg=%s, output=%s). "
                "Retrying with --session-id to create a fresh session.",
                session.name,
                session_arg,
                error_output[:300],
            )
            # Clean up the failed command prompt
            send_keys(tmux_sess, tmux_win, "C-c", enter=False)
            time.sleep(0.5)
            send_keys(tmux_sess, tmux_win, "", enter=True)
            time.sleep(0.5)

            # Clean up stale session file + orphaned processes.
            # Without this, --session-id fails with "already in use".
            _cleanup_stale_claude_session_local(target_id)
            time.sleep(1)

            # Retry with --session-id (creates a new conversation)
            fallback_arg = f"--session-id {target_id}"
            claude_args_retry = [
                fallback_arg,
                "--dangerously-skip-permissions",
                f"--settings {shlex.quote(settings_file)}",
            ]
            if os.path.exists(prompt_file):
                claude_args_retry.append(
                    f'--append-system-prompt "$(cat {shlex.quote(prompt_file)})"'
                )

            claude_cmd_retry = f"claude {' '.join(claude_args_retry)}"
            logger.info("Reconnect local %s: retrying with: %s", session.name, fallback_arg)
            safe_send_keys(tmux_sess, tmux_win, claude_cmd_retry, enter=True)

            # Dismiss any "trust this folder" prompt that may appear after launch
            dismiss_trust_prompt(tmux_sess, tmux_win, session_id=session.id)

            # Check the retry — if this also fails, let the health check loop handle it
            retry_started, retry_output = _verify_claude_started(tmux_sess, tmux_win)
            if not retry_started:
                logger.error(
                    "Reconnect local %s: retry also failed (output=%s). Giving up.",
                    session.name,
                    retry_output[:300],
                )
                return False

        logger.info(
            "Launched Claude Code for local worker %s (session_id=%s)", session.name, session.id
        )
        return True
    finally:
        lock.release()


# Backward-compat alias
reconnect_rdev_worker = reconnect_remote_worker


# =============================================================================
# High-Level Reconnect Orchestration
# =============================================================================

WORKER_BASE_DIR = "/tmp/orchestrator/workers"


def trigger_reconnect(
    session,
    db,
    db_path: str | None = None,
    api_port: int = 8093,
    tunnel_manager=None,
) -> dict:
    """Trigger reconnection for a worker session.

    Unified entry point that replaces the duplicated reconnect orchestration
    previously copy-pasted across manual reconnect, toggle auto-reconnect,
    and health-check-all endpoints.

    For remote workers: sets status to 'connecting', spawns a background
    thread, and returns immediately.  The thread creates its own DB
    connection, calls ``reconnect_remote_worker``, and handles errors.

    For local workers: reconnects synchronously (blocking).

    Args:
        session: Session model object (must have id, name, host).
        db: Current SQLite connection (used for immediate status update).
        db_path: Path to DB file — background threads open their own
            connection via this path.  Required for remote workers.
        api_port: Orchestrator API port (default 8093).
        tunnel_manager: ReverseTunnelManager instance (remote workers).

    Returns:
        {"ok": True} if reconnect started/succeeded.
        {"ok": False, "error": "..."} on failure.
    """
    from orchestrator.state.repositories import sessions as repo
    from orchestrator.terminal.manager import tmux_target
    from orchestrator.terminal.ssh import is_remote_host

    tmux_sess, tmux_win = tmux_target(session.name)
    tmp_dir = os.path.join(WORKER_BASE_DIR, session.name)

    # Check if a reconnect is already in progress (RC-18: avoid double-click confusion)
    lock = get_reconnect_lock(session.id)
    if lock.locked():
        logger.info("trigger_reconnect %s: reconnect already in progress, skipping", session.name)
        return {"ok": False, "error": "Reconnect already in progress"}

    repo.update_session(db, session.id, status="connecting")

    if is_remote_host(session.host):
        # Remote worker — reconnect in a background thread so the API
        # request returns immediately.  All loop-variable-sensitive values
        # are bound via default parameters to avoid closure bugs.
        def _bg_reconnect(_session=session, _ts=tmux_sess, _tw=tmux_win, _td=tmp_dir):
            from orchestrator.state.db import get_connection

            bg_conn = get_connection(db_path) if db_path else db
            try:
                reconnect_remote_worker(
                    bg_conn,
                    _session,
                    _ts,
                    _tw,
                    api_port,
                    _td,
                    repo,
                    tunnel_manager=tunnel_manager,
                )
                logger.info("Reconnect succeeded for %s", _session.name)
            except Exception:
                logger.exception("Reconnect failed for %s", _session.name)
                try:
                    repo.update_session(bg_conn, _session.id, status="disconnected")
                except Exception:
                    pass
            finally:
                if db_path and bg_conn is not db:
                    bg_conn.close()

        thread = threading.Thread(target=_bg_reconnect, daemon=True)
        thread.start()
        return {"ok": True, "async": True}
    else:
        # Local worker — reconnect synchronously
        try:
            success = reconnect_local_worker(
                session, tmux_sess, tmux_win, api_port, tmp_dir, conn=db
            )
            if success:
                repo.update_session(db, session.id, status="waiting")
            else:
                repo.update_session(db, session.id, status="disconnected")
            return {"ok": success, "async": False}
        except Exception as e:
            logger.exception("Local reconnect failed for %s", session.name)
            repo.update_session(db, session.id, status="disconnected")
            return {"ok": False, "error": str(e)}
