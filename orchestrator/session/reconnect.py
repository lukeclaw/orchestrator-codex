"""Session reconnection logic for remote and local workers.

Handles re-establishing SSH tunnels and relaunching Claude via RWS PTY.

Reconnect Flow — RWS PTY (remote workers):

  Step 0: Acquire per-session lock (prevents concurrent reconnects)
  Step 1: Ensure reverse tunnel alive (for API callbacks)
  Step 2: Ensure RWS daemon connected
  Step 3: Deploy configs to remote via SSH
  Step 4: Create new RWS PTY with Claude (resume if session exists)

Local workers use a separate path via ``reconnect_local_worker``.
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
)
from orchestrator.terminal.manager import (
    capture_output,
    dismiss_trust_prompt,
    send_keys,
)
from orchestrator.terminal.session import (
    _copy_dir_to_remote_ssh,
)

logger = logging.getLogger(__name__)

# Shell one-liner: skip `claude plugin install` if plugin already present.
_PW_INSTALL_CMD = (
    "claude plugin list 2>/dev/null | grep -q 'playwright@' || claude plugin install playwright"
)


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
    from orchestrator.session.health import _reconnect_backoff

    with _registry_lock:
        _reconnect_locks.pop(session_id, None)
    clear_reconnect_step(session_id)
    _reconnect_backoff.cleanup(session_id)


# =============================================================================
# In-Memory Reconnect Step Tracking (Ephemeral)
# =============================================================================

# Key: session_id, Value: step string (e.g. "tunnel", "daemon", "failed:daemon")
_reconnect_steps: dict[str, str] = {}
_steps_lock = threading.Lock()


def get_reconnect_step(session_id: str) -> str | None:
    """Read the current reconnect step for a session (thread-safe)."""
    with _steps_lock:
        return _reconnect_steps.get(session_id)


def _set_reconnect_step(session_id: str, step: str):
    """Update the reconnect step and broadcast to frontend via event bus."""
    with _steps_lock:
        _reconnect_steps[session_id] = step
    try:
        from orchestrator.core.events import Event, publish

        publish(
            Event(
                type="reconnect.step_changed",
                data={"session_id": session_id, "step": step},
            )
        )
    except Exception:
        pass  # Non-critical


def clear_reconnect_step(session_id: str):
    """Clear the reconnect step (call on success, or session delete)."""
    with _steps_lock:
        _reconnect_steps.pop(session_id, None)
    try:
        from orchestrator.core.events import Event, publish

        publish(
            Event(
                type="reconnect.step_changed",
                data={"session_id": session_id, "step": None},
            )
        )
    except Exception:
        pass


# =============================================================================
# Rdev Auto-Start Helpers
# =============================================================================


def _get_rdev_state(host: str) -> str | None:
    """Query the live state of an rdev host via ``rdev info``.

    Returns the uppercase state string (e.g. ``"RUNNING"``, ``"STOPPED"``)
    or ``None`` if the command fails or the state line is not found.
    """
    try:
        result = subprocess.run(
            ["rdev", "info", host],
            capture_output=True,
            text=True,
            timeout=15,
        )
        for line in result.stdout.splitlines():
            # rdev info prints a table like:  State | RUNNING
            if "State" in line and "|" in line:
                return line.split("|", 1)[1].strip().upper()
    except Exception as exc:
        logger.debug("_get_rdev_state(%s) failed: %s", host, exc)
    return None


def _ensure_rdev_running(session_id: str, host: str, timeout: int = 120) -> bool:
    """Ensure an rdev host is in RUNNING state, restarting it if stopped.

    Returns ``True`` if the host is running (or not an rdev host),
    ``False`` if the host cannot be started.
    """
    from orchestrator.terminal.ssh import is_rdev_host

    if not is_rdev_host(host):
        return True

    state = _get_rdev_state(host)

    # Can't determine state, or already running — proceed optimistically
    if state is None or state == "RUNNING":
        return True

    # Transitional states — just wait, no restart needed
    if state in ("CREATING", "STARTING"):
        _set_reconnect_step(session_id, "rdev_start")
        deadline = time.time() + 60
        while time.time() < deadline:
            time.sleep(5)
            s = _get_rdev_state(host)
            if s == "RUNNING":
                _invalidate_rdev_cache()
                return True
            if s not in ("CREATING", "STARTING"):
                break
        logger.error("_ensure_rdev_running: %s stuck in %s", host, state)
        return False

    # Stopped/stopping — restart
    if state in ("STOPPED", "STOPPING"):
        _set_reconnect_step(session_id, "rdev_start")
        logger.info("_ensure_rdev_running: restarting stopped rdev %s", host)
        try:
            result = subprocess.run(
                ["rdev", "restart", host],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                logger.error(
                    "_ensure_rdev_running: rdev restart failed (rc=%d): %s",
                    result.returncode,
                    result.stderr.strip(),
                )
                return False
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            logger.error("_ensure_rdev_running: rdev restart error: %s", exc)
            return False

        # Poll until RUNNING
        deadline = time.time() + 60
        while time.time() < deadline:
            time.sleep(5)
            s = _get_rdev_state(host)
            if s == "RUNNING":
                _invalidate_rdev_cache()
                return True
        logger.error("_ensure_rdev_running: %s did not reach RUNNING after restart", host)
        return False

    # DELETED, ERROR, or unknown — cannot start
    logger.error("_ensure_rdev_running: %s in unrecoverable state %s", host, state)
    return False


def _invalidate_rdev_cache():
    """Invalidate the rdev list cache so the next API call fetches fresh data."""
    try:
        from orchestrator.api.routes.rdevs import _rdev_cache

        _rdev_cache["timestamp"] = 0
    except Exception:
        pass


# =============================================================================
# Internal Helpers
# =============================================================================


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

    Cleans up:
    - ``<session_id>.jsonl`` conversation log (existence blocks ``--session-id``)
    - ``<session_id>/`` directory (subagents, tool-results)
    - Any Claude process whose command line contains the session ID
    """
    try:
        cleanup_cmd = (
            # Delete stale session files (.jsonl) AND session directories
            f"find ~/.claude/projects -name '{session_id}.jsonl' -delete 2>/dev/null; "
            f"find ~/.claude/projects -maxdepth 2 -type d -name '{session_id}' "
            f"-exec rm -rf {{}} + 2>/dev/null; "
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


def _kill_orphan_claude_processes_remote(
    host: str, session_id: str, claude_session_id: str | None = None
):
    """Kill orphaned Claude processes on a remote host WITHOUT deleting session files.

    This preserves conversation history (the ``.jsonl`` files) while clearing
    process-level locks (``flock``).  Call before creating a new PTY so that
    ``-r`` (resume) can acquire the flock on the existing ``.jsonl``.
    """
    ids = [session_id]
    if claude_session_id and claude_session_id != session_id:
        ids.append(claude_session_id)
    grep_pattern = "|".join(ids)
    kill_cmd = (
        f"ps aux | grep -v grep "
        f"| grep -E 'claude (-r|--|--settings)' "
        f"| grep -E '{grep_pattern}' "
        f"| awk '{{print $2}}' "
        f"| xargs -r kill 2>/dev/null || true"
    )
    try:
        subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, kill_cmd],
            capture_output=True,
            text=True,
            timeout=15,
        )
        logger.info("Killed orphan Claude processes on %s for %s", host, session_id)
    except Exception:
        logger.debug("_kill_orphan_claude_processes_remote failed", exc_info=True)


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


def _reconnect_rws_pty_worker(conn, session, repo, tunnel_manager):
    """Reconnect a session using RWS PTY architecture."""
    from orchestrator.terminal.session import (
        _build_claude_command,
        _ensure_rws_ready,
    )

    remote_tmp_dir = f"/tmp/orchestrator/workers/{session.name}"
    tmp_dir = f"/tmp/orchestrator/workers/{session.name}"
    api_base = "http://127.0.0.1:8093"

    # 1. Ensure reverse tunnel alive
    _set_reconnect_step(session.id, "tunnel")
    if tunnel_manager and not tunnel_manager.is_alive(session.id):
        _ensure_tunnel(session, tunnel_manager, repo, conn)

    # 2. Ensure RWS daemon connected
    _set_reconnect_step(session.id, "daemon")
    rws = _ensure_rws_ready(session.host, timeout=30)

    # 3. Reconnect RWS forward tunnel if needed
    _reconnect_rws_for_host(session)

    # Re-fetch RWS in case _reconnect_rws_for_host replaced the pool entry
    rws = _ensure_rws_ready(session.host, timeout=10)

    # 4. Check PTY status
    _set_reconnect_step(session.id, "pty_check")
    try:
        resp = rws.execute({"action": "pty_list"}, timeout=5)
        ptys = resp.get("ptys", [])
    except Exception:
        ptys = []

    our_pty = next((p for p in ptys if p["pty_id"] == session.rws_pty_id), None)

    if our_pty and our_pty["alive"]:
        # PTY still running — nothing to do! Browser will auto-reconnect.
        repo.update_session(conn, session.id, status="waiting")
        logger.info("Reconnect RWS %s: PTY still alive, nothing to do", session.name)
        return

    # PTY not found by ID — check if a PTY with our session_id exists
    # (handles daemon restart where PTY IDs changed)
    if not our_pty:
        by_session = next(
            (
                p
                for p in ptys
                if p.get("session_id") == session.id
                and p.get("alive")
                and p.get("role") != "interactive-cli"
            ),
            None,
        )
        if by_session:
            pty_id = by_session["pty_id"]
            repo.update_session(conn, session.id, rws_pty_id=pty_id, status="waiting")
            logger.info(
                "Reconnect RWS %s: found alive PTY %s by session_id, re-attached",
                session.name,
                pty_id,
            )
            return

    if our_pty and not our_pty["alive"]:
        try:
            rws.execute({"action": "pty_destroy", "pty_id": session.rws_pty_id})
        except Exception:
            pass

    # 5. PTY dead or gone — kill orphaned Claude processes, redeploy, recreate
    _kill_orphan_claude_processes_remote(session.host, session.id, session.claude_session_id)
    time.sleep(1)  # Let flock release after process kill

    _set_reconnect_step(session.id, "deploy")
    _ensure_local_configs_exist(tmp_dir, session.id, api_base, conn=conn)
    _copy_configs_to_remote(session.host, tmp_dir, remote_tmp_dir, session.name)

    # 6. Create new PTY
    _set_reconnect_step(session.id, "pty_create")
    target_id = session.claude_session_id or session.id
    session_exists = _check_claude_session_exists_remote(session.host, target_id)
    claude_cmd = _build_claude_command(
        session.id,
        session.host,
        remote_tmp_dir,
        session.work_dir,
        claude_session_id=session.claude_session_id,
        is_resume=session_exists,
    )
    pty_id = rws.create_pty(
        cmd=claude_cmd,
        cwd=session.work_dir or os.path.expanduser("~"),
        session_id=session.id,
        role="main",
    )
    repo.update_session(conn, session.id, rws_pty_id=pty_id, status="working")
    logger.info("Reconnect RWS %s: created new PTY %s", session.name, pty_id)

    # 7. Verify
    _set_reconnect_step(session.id, "verify")
    time.sleep(3)
    try:
        resp = rws.execute({"action": "pty_list"}, timeout=5)
        ptys = resp.get("ptys", [])
        alive = any(p["pty_id"] == pty_id and p["alive"] for p in ptys)
    except Exception:
        alive = True  # Assume alive if we can't check

    if not alive:
        logger.warning(
            "Reconnect RWS %s: PTY died after creation, retrying with fresh session",
            session.name,
        )
        # Clean up ALL session files — both target_id and session.id
        # (--session-id uses session.id, so its .jsonl must be removed)
        _cleanup_stale_claude_session_remote(session.host, target_id)
        if session.id != target_id:
            _cleanup_stale_claude_session_remote(session.host, session.id)
        time.sleep(1)

        claude_cmd = _build_claude_command(
            session.id,
            session.host,
            remote_tmp_dir,
            session.work_dir,
            claude_session_id=session.claude_session_id,
            is_resume=False,
        )
        pty_id = rws.create_pty(
            cmd=claude_cmd,
            cwd=session.work_dir or os.path.expanduser("~"),
            session_id=session.id,
            role="main",
        )
        repo.update_session(conn, session.id, rws_pty_id=pty_id, status="working")
        logger.info("Reconnect RWS %s: retry created PTY %s", session.name, pty_id)

        # Second verify — if retry also fails, mark disconnected (not working)
        # so the backoff prevents rapid oscillation.
        time.sleep(3)
        try:
            resp = rws.execute({"action": "pty_list"}, timeout=5)
            ptys = resp.get("ptys", [])
            retry_alive = any(p["pty_id"] == pty_id and p["alive"] for p in ptys)
        except Exception:
            retry_alive = True

        if not retry_alive:
            logger.error(
                "Reconnect RWS %s: retry PTY also died, giving up",
                session.name,
            )
            repo.update_session(conn, session.id, status="disconnected")
            return

    # Detect work_dir if not set
    if not session.work_dir:
        from orchestrator.api.routes.files import _detect_remote_work_dir

        time.sleep(3)
        detected = _detect_remote_work_dir(session.host, session.id)
        if detected:
            repo.update_session(conn, session.id, work_dir=detected)
            logger.info("Reconnect RWS %s: detected work_dir: %s", session.name, detected)


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
    """Reconnect a remote worker (rdev or generic SSH) via RWS PTY.

    All remote workers use the RWS PTY architecture:
      1. Acquire per-session lock
      2. Ensure reverse tunnel alive
      3. Ensure RWS daemon connected
      4. Kill old screen session on remote (best-effort cleanup of legacy sessions)
      5. Deploy configs to remote
      6. Create new RWS PTY with Claude (resume if session exists)
    """
    from orchestrator.terminal.session import (
        _build_claude_command,
        _ensure_rws_ready,
    )

    remote_tmp_dir = f"/tmp/orchestrator/workers/{session.name}"

    # ── Step 0: Acquire per-session lock ──────────────────────────────────
    lock = get_reconnect_lock(session.id)
    if not lock.acquire(timeout=5):
        logger.warning("Reconnect %s: another reconnect in progress, skipping", session.name)
        return

    try:
        # ── Ensure rdev host is running (auto-start if stopped) ──────────
        if not _ensure_rdev_running(session.id, session.host):
            repo.update_session(conn, session.id, status="disconnected")
            logger.error(
                "Reconnect %s: rdev host %s not running, cannot start",
                session.name,
                session.host,
            )
            return

        # ── If session already has rws_pty_id, use existing reconnect logic ──
        if session.rws_pty_id:
            _reconnect_rws_pty_worker(conn, session, repo, tunnel_manager)
            return

        # ── No rws_pty_id: either new session, legacy migration, or
        #    health check cleared it because daemon was unreachable.
        #    First check if a PTY for this session is already alive. ────────
        _set_reconnect_step(session.id, "tunnel")
        rws = _ensure_rws_ready(session.host, timeout=30)

        # Ensure reverse tunnel alive (for API callbacks)
        if tunnel_manager:
            _ensure_tunnel(session, tunnel_manager, repo, conn)

        # Re-establish RWS forward tunnel (may have died with SSH)
        _set_reconnect_step(session.id, "daemon")
        _reconnect_rws_for_host(session)

        # Re-fetch RWS in case _reconnect_rws_for_host replaced the pool entry
        rws = _ensure_rws_ready(session.host, timeout=10)

        # Check if there's already a PTY running for this session
        _set_reconnect_step(session.id, "pty_check")
        try:
            resp = rws.execute({"action": "pty_list"}, timeout=5)
            ptys = resp.get("ptys", [])
            existing = next(
                (
                    p
                    for p in ptys
                    if p.get("session_id") == session.id
                    and p.get("alive")
                    and p.get("role") != "interactive-cli"
                ),
                None,
            )
            if existing:
                # PTY still alive — just re-attach, no need to restart anything
                pty_id = existing["pty_id"]
                repo.update_session(conn, session.id, rws_pty_id=pty_id, status="waiting")
                logger.info(
                    "Reconnect %s: found existing alive PTY %s, re-attached",
                    session.name,
                    pty_id,
                )
                return
        except Exception:
            logger.debug(
                "Reconnect %s: could not query PTY list, will create new PTY",
                session.name,
                exc_info=True,
            )

        # Kill orphaned Claude processes before creating new PTY
        _kill_orphan_claude_processes_remote(session.host, session.id, session.claude_session_id)
        time.sleep(1)  # Let flock release after process kill

        # Deploy configs
        _set_reconnect_step(session.id, "deploy")
        api_base = f"http://127.0.0.1:{api_port}"
        _ensure_local_configs_exist(tmp_dir, session.id, api_base, conn=conn)
        _copy_configs_to_remote(session.host, tmp_dir, remote_tmp_dir, session.name)

        # Build Claude command (resume if session exists remotely)
        target_id = session.claude_session_id or session.id
        sess_exists = _check_claude_session_exists_remote(session.host, target_id)
        claude_cmd = _build_claude_command(
            session.id,
            session.host,
            remote_tmp_dir,
            session.work_dir,
            claude_session_id=session.claude_session_id,
            is_resume=sess_exists,
        )

        # Create PTY
        _set_reconnect_step(session.id, "pty_create")
        pty_id = rws.create_pty(
            cmd=claude_cmd,
            cwd=session.work_dir or os.path.expanduser("~"),
            session_id=session.id,
            role="main",
        )
        repo.update_session(conn, session.id, rws_pty_id=pty_id, status="working")
        logger.info("Reconnect %s: created RWS PTY %s", session.name, pty_id)

        # Verify PTY survived startup (mirrors Path A verify step)
        _set_reconnect_step(session.id, "verify")
        time.sleep(3)
        try:
            resp = rws.execute({"action": "pty_list"}, timeout=5)
            ptys = resp.get("ptys", [])
            alive = any(p["pty_id"] == pty_id and p["alive"] for p in ptys)
        except Exception:
            alive = True  # Assume alive if we can't check

        if not alive:
            logger.warning(
                "Reconnect %s: PTY died after creation, retrying with fresh session",
                session.name,
            )
            # Clean up ALL session files — both target_id and session.id
            # (--session-id uses session.id, so its .jsonl must be removed)
            _cleanup_stale_claude_session_remote(session.host, target_id)
            if session.id != target_id:
                _cleanup_stale_claude_session_remote(session.host, session.id)
            time.sleep(1)

            claude_cmd = _build_claude_command(
                session.id,
                session.host,
                remote_tmp_dir,
                session.work_dir,
                claude_session_id=session.claude_session_id,
                is_resume=False,
            )
            pty_id = rws.create_pty(
                cmd=claude_cmd,
                cwd=session.work_dir or os.path.expanduser("~"),
                session_id=session.id,
                role="main",
            )
            repo.update_session(conn, session.id, rws_pty_id=pty_id, status="working")
            logger.info("Reconnect %s: retry created PTY %s", session.name, pty_id)

            # Second verify — if retry also fails, mark disconnected (not working)
            # so the backoff prevents rapid oscillation.
            time.sleep(3)
            try:
                resp = rws.execute({"action": "pty_list"}, timeout=5)
                ptys = resp.get("ptys", [])
                retry_alive = any(p["pty_id"] == pty_id and p["alive"] for p in ptys)
            except Exception:
                retry_alive = True

            if not retry_alive:
                logger.error(
                    "Reconnect %s: retry PTY also died, giving up",
                    session.name,
                )
                repo.update_session(conn, session.id, status="disconnected")
                return

        # Detect work_dir if not set
        if not session.work_dir:
            from orchestrator.api.routes.files import _detect_remote_work_dir

            time.sleep(3)
            detected = _detect_remote_work_dir(session.host, session.id)
            if detected:
                repo.update_session(conn, session.id, work_dir=detected)
                logger.info("Reconnect %s: detected work_dir: %s", session.name, detected)

    except Exception:
        logger.exception("Reconnect failed for %s", session.name)
        try:
            repo.update_session(conn, session.id, status="disconnected")
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

        # Ensure the official Playwright plugin is installed (skip if already present)
        safe_send_keys(tmux_sess, tmux_win, _PW_INSTALL_CMD, enter=True)
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

        # Optionally update Claude Code before launching
        if conn:
            from orchestrator.terminal.claude_update import (
                run_claude_update,
                should_update_before_start,
            )

            if should_update_before_start(conn):
                run_claude_update(safe_send_keys, capture_output, tmux_sess, tmux_win)

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
            from orchestrator.session.health import _reconnect_backoff
            from orchestrator.state.db import get_connection

            _reconnect_backoff.record_attempt(_session.id)
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
                clear_reconnect_step(_session.id)
                _reconnect_backoff.record_success(_session.id)
            except Exception:
                logger.exception("Reconnect failed for %s", _session.name)
                _reconnect_backoff.record_failure(_session.id)
                try:
                    current_step = get_reconnect_step(_session.id)
                    if current_step and not current_step.startswith("failed:"):
                        _set_reconnect_step(_session.id, f"failed:{current_step}")
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
