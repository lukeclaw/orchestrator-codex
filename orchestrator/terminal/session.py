"""Full session lifecycle: create, start Claude Code, remove."""

from __future__ import annotations

import base64
import logging
import os
import shlex
import sqlite3
import subprocess
import time

from orchestrator.agents import (
    get_path_export_command,
)
from orchestrator.agents.deploy import (
    deploy_custom_skills,
)
from orchestrator.state.models import Session
from orchestrator.state.repositories import sessions as sessions_repo
from orchestrator.terminal import manager as tmux
from orchestrator.terminal import ssh
from orchestrator.terminal.ssh import is_rdev_host

logger = logging.getLogger(__name__)

_SOURCE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Shell one-liner: skip `claude plugin install` if plugin already present.
_PW_INSTALL_CMD = (
    "claude plugin list 2>/dev/null | grep -q 'playwright@' || claude plugin install playwright"
)


def create_session(
    conn: sqlite3.Connection,
    name: str,
    host: str,
    work_dir: str | None = None,
    tmux_session: str = "orchestrator",
    tmp_dir: str | None = None,
) -> Session:
    """Create a new session: tmux window, SSH, cd to path, persist to DB."""
    # Create tmux window (start in worker's tmp dir if provided)
    if tmp_dir:
        os.makedirs(tmp_dir, exist_ok=True)
    tmux.create_window(tmux_session, name, cwd=tmp_dir)

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
    session = sessions_repo.create_session(conn, name=name, host=host, work_dir=work_dir)
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
    sessions_repo.update_session(conn, session.id, status="idle")
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
    lines = output.strip().split("\n")
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
        message_tail_normalized = " ".join(message_tail.split())
        last_line_normalized = " ".join(last_line.split())

        if len(last_line_normalized) > 20 and message_tail_normalized[-50:] in last_line_normalized:
            logger.debug("Message appears stuck in input - last line matches message tail")
            return False

    # Also check if the cursor line appears to have unsubmitted content
    # Claude Code shows ">" prompt when waiting for input
    # If we see significant text after the prompt, message may be stuck
    if last_line.startswith(">") and len(last_line) > 20:
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
    # Send message content using bracketed paste (preferred) or literal mode (fallback).
    # Bracketed paste wraps text in ESC[200~ … ESC[201~ so Claude Code's Ink TUI
    # inserts the text as-is instead of interpreting each \n as Ctrl-J (newline).
    if not tmux.paste_to_pane(tmux_session, name, message):
        logger.warning("paste_to_pane failed, falling back to send_keys_literal")
        if not tmux.send_keys_literal(tmux_session, name, message):
            return False

    # Brief delay so the TUI finishes processing the pasted text before Enter.
    time.sleep(0.3)

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
                attempt + 1,
                max_enter_retries,
            )
            time.sleep(retry_delay)

    # All retries exhausted
    logger.error("Failed to send message after %d Enter attempts", max_enter_retries)
    return False


def _wait_for_command_completion(
    tmux_session: str, window_name: str, timeout: int = 60, poll_interval: float = 2.0
) -> bool:
    """Wait for a command to complete by checking for shell prompt return.

    Uses the markers module for safe marker-based detection.
    Returns True if command completed within timeout, False otherwise.
    """
    from orchestrator.terminal.markers import wait_for_completion

    return wait_for_completion(
        tmux.send_keys,
        tmux.capture_output,
        tmux_session,
        window_name,
        timeout=timeout,
        poll_interval=poll_interval,
    )


def _copy_dir_to_remote_ssh(local_dir: str, host: str, remote_dir: str) -> bool:
    """Copy a local directory to a remote host using direct SSH subprocess.

    This bypasses tmux/screen entirely by piping tar directly through SSH stdin.
    Much more reliable than sending large commands through tmux send-keys.

    Args:
        local_dir: Local directory to copy
        host: Remote host (rdev or generic SSH)
        remote_dir: Remote directory to extract to

    Returns:
        True if copy succeeded, False otherwise
    """
    try:
        # First create remote directory via SSH
        mkdir_result = subprocess.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "BatchMode=yes",
                host,
                f"mkdir -p {remote_dir}",
            ],
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
            [
                "ssh",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "BatchMode=yes",
                host,
                f"tar xzf - -C {remote_dir}",
            ],
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


# Backward-compat alias
_copy_dir_to_rdev_ssh = _copy_dir_to_remote_ssh


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
    chunks = [encoded[i : i + chunk_size] for i in range(0, len(encoded), chunk_size)]

    tmux.send_keys(tmux_session, window_name, "rm -f /tmp/_orch_transfer.b64", enter=True)
    time.sleep(0.1)

    for chunk in chunks:
        tmux.send_keys(
            tmux_session, window_name, f"echo -n '{chunk}' >> /tmp/_orch_transfer.b64", enter=True
        )
        time.sleep(0.05)

    tmux.send_keys(
        tmux_session,
        window_name,
        f"base64 -d /tmp/_orch_transfer.b64 | tar xzf - -C {remote_dir}"
        " && rm -f /tmp/_orch_transfer.b64",
        enter=True,
    )
    time.sleep(0.5)

    logger.info("Copied %s to remote %s via tar+base64 (tmux)", local_dir, remote_dir)


def ensure_rdev_node(tmux_session: str, window_name: str, remote_tmp_dir: str):
    """Install Node 24 via volta and create symlinks in node-bin/.

    Rdev images ship Node 16 via a LinkedIn wrapper that force-resets volta's
    platform.json on every invocation.  We bypass both the wrapper and the volta
    shim by symlinking the actual Node 24 binary into a dedicated directory
    that gets placed first in PATH.

    Idempotent — safe to call on every setup or reconnect.
    """
    node_bin_dir = f"{remote_tmp_dir}/node-bin"
    volta_cmd = (
        "volta install node@24"
        f" && mkdir -p {node_bin_dir}"
        f" && ln -sf $(volta which node) {node_bin_dir}/node"
        f" && ln -sf $(volta which npx) {node_bin_dir}/npx"
        f" && ln -sf $(volta which npm) {node_bin_dir}/npm"
    )
    tmux.send_keys(tmux_session, window_name, volta_cmd, enter=True)
    time.sleep(8)  # volta downloads + installs + creates symlinks
    logger.info("Ensured Node 24 symlinks at %s", node_bin_dir)


def _ensure_rws_ready(host: str, timeout: float = 30.0):
    """Synchronously ensure RWS daemon is deployed and connected.

    Polls get_remote_worker_server() with retries until ready or timeout.
    Returns the RemoteWorkerServer instance.
    """
    from orchestrator.terminal.remote_worker_server import get_remote_worker_server

    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            return get_remote_worker_server(host)
        except RuntimeError as e:
            last_err = e
            time.sleep(2)
    raise RuntimeError(f"RWS daemon not ready for {host} after {timeout}s: {last_err}")


def _build_claude_command(
    session_id: str,
    host: str,
    remote_tmp_dir: str,
    work_dir: str | None,
    claude_session_id: str | None = None,
    is_resume: bool = False,
) -> str:
    """Build full bash command chain for Claude in RWS PTY.

    Returns a single shell command string that sets up PATH, installs
    plugins, configures environment, and launches Claude.
    """
    parts = []

    # PATH setup — on rdev, include node-bin for Node 24 symlinks
    if is_rdev_host(host):
        parts.append(
            f'export PATH="{remote_tmp_dir}/node-bin:{remote_tmp_dir}/bin:$HOME/.local/bin:$PATH"'
        )
    else:
        parts.append(get_path_export_command(f"{remote_tmp_dir}/bin"))

    # Make scripts executable
    parts.append(f"chmod +x {remote_tmp_dir}/bin/* 2>/dev/null || true")
    parts.append(f"chmod +x {remote_tmp_dir}/configs/hooks/*.sh 2>/dev/null || true")

    # Install Playwright plugin (skip if already installed; failure is non-fatal)
    parts.append(f"({_PW_INSTALL_CMD} || true)")

    # Configure Playwright MCP via env var
    parts.append("export PLAYWRIGHT_MCP_CDP_ENDPOINT=http://localhost:9222")

    # cd to work_dir
    if work_dir:
        parts.append(f"cd {work_dir}")

    # Build Claude command
    settings_file = f"{remote_tmp_dir}/configs/settings.json"
    target_id = claude_session_id or session_id

    if is_resume:
        session_arg = f"-r {target_id}"
    else:
        session_arg = f"--session-id {session_id}"

    claude_args = [
        session_arg,
        f"--settings {settings_file}",
        "--dangerously-skip-permissions",
    ]

    # Load prompt from file if it exists
    remote_prompt_path = f"{remote_tmp_dir}/prompt.md"
    claude_args.append(f'--append-system-prompt "$(cat {remote_prompt_path} 2>/dev/null || true)"')

    parts.append(f"claude {' '.join(claude_args)}")

    return " && ".join(parts)


def setup_remote_worker(
    conn: sqlite3.Connection,
    session_id: str,
    name: str,
    host: str,
    tmux_session: str = "orchestrator",
    api_port: int = 8093,
    work_dir: str | None = None,
    tmp_dir: str | None = None,
    tunnel_manager=None,
    custom_skills: list[dict] | None = None,
    disabled_builtin_names: set[str] | None = None,
    update_before_start: bool = False,
) -> dict:
    """Set up a full remote worker via RWS PTY (new architecture).

    Replaces the legacy Screen+tmux path. Claude runs in a PTY managed by
    the RWS daemon on the remote host, giving full scrollback and eliminating
    the need for GNU Screen.

    Falls back to legacy screen path only if RWS PTY setup fails.

    Returns {"ok": True, "tunnel_pid": ...} on success,
    or {"ok": False, "error": "..."} on failure.
    """
    from orchestrator.session.reconnect import get_reconnect_lock

    remote_tmp_dir = f"/tmp/orchestrator/workers/{name}"
    local_tmp_dir = tmp_dir or f"/tmp/orchestrator/workers/{name}"

    lock = get_reconnect_lock(session_id)
    lock.acquire(timeout=5)

    try:
        # 0. Ensure rdev host is running (auto-start if stopped)
        from orchestrator.session.reconnect import _ensure_rdev_running

        if not _ensure_rdev_running(session_id, host):
            return {"ok": False, "error": f"Rdev host {host} is stopped and could not be started"}

        # 0.5. Ensure SSH config entry exists for rdev host.
        # Brand-new rdevs won't have an entry in ~/.ssh/config.rdev until
        # the first `rdev ssh` connection, causing plain `ssh host` to fail
        # with "Could not resolve hostname".
        if is_rdev_host(host):
            from orchestrator.terminal.ssh import ensure_rdev_ssh_config

            if not ensure_rdev_ssh_config(host):
                return {"ok": False, "error": f"Could not bootstrap SSH config for {host}"}

        # 1. Start reverse SSH tunnel via subprocess (for API callbacks)
        tunnel_pid = None
        if tunnel_manager:
            tunnel_pid = tunnel_manager.start_tunnel(session_id, name, host)
            if tunnel_pid:
                logger.info(
                    "Started reverse tunnel subprocess for %s -> %s (pid=%d)",
                    name,
                    host,
                    tunnel_pid,
                )
            else:
                logger.warning("Failed to start tunnel subprocess for %s, continuing setup", name)
        else:
            logger.warning("No tunnel_manager provided, skipping tunnel setup for %s", name)
        time.sleep(2)  # Give tunnel a moment to establish

        # 2. Deploy all files locally via SOT function
        from orchestrator.agents.deploy import deploy_worker_tmp_contents

        deploy_worker_tmp_contents(
            local_tmp_dir,
            session_id,
            api_base=f"http://127.0.0.1:{api_port}",
            cdp_port=9222,
            browser_headless=True,  # Remote: headless (no display)
            custom_skills=custom_skills,
            disabled_builtin_names=disabled_builtin_names,
        )
        logger.info("Deployed worker tmp contents for remote worker %s", name)

        # 3. Copy entire directory to remote via direct SSH (bypasses tmux/screen)
        if not _copy_dir_to_remote_ssh(local_tmp_dir, host, remote_tmp_dir):
            raise RuntimeError(f"Failed to copy files to remote via SSH: {host}:{remote_tmp_dir}")
        logger.info("Copied files to remote via direct SSH: %s", remote_tmp_dir)

        # 4. Copy skills to ~/.claude/commands/ via SSH subprocess
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
        logger.info("Deployed skills to ~/.claude/commands/ for %s", name)

        # 5. Install Node 24 via SSH subprocess (needed for Playwright)
        if is_rdev_host(host):
            # On rdev, create node-bin symlinks for Node 24
            node_cmd = (
                "volta install node@24"
                f" && mkdir -p {remote_tmp_dir}/node-bin"
                f" && ln -sf $(volta which node) {remote_tmp_dir}/node-bin/node"
                f" && ln -sf $(volta which npx) {remote_tmp_dir}/node-bin/npx"
                f" && ln -sf $(volta which npm) {remote_tmp_dir}/node-bin/npm"
            )
        else:
            node_cmd = "volta install node@24 2>/dev/null || true"
        subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, node_cmd],
            capture_output=True,
            timeout=60,
        )
        logger.info("Ensured Node 24 on %s for %s", host, name)

        # 6. Ensure RWS daemon is running
        rws = _ensure_rws_ready(host, timeout=30)

        # 7. Build Claude command and create PTY
        claude_cmd = _build_claude_command(
            session_id,
            host,
            remote_tmp_dir,
            work_dir,
            claude_session_id=None,
            is_resume=False,
        )

        pty_id = rws.create_pty(
            cmd=claude_cmd,
            cwd=work_dir or os.path.expanduser("~"),
            cols=120,
            rows=40,
            session_id=session_id,
            role="main",
        )
        logger.info("Created RWS PTY %s for worker %s", pty_id, name)

        # 8. Store pty_id — start as idle (no task assigned yet).
        # The hook will transition to "working" when a task is submitted.
        sessions_repo.update_session(conn, session_id, rws_pty_id=pty_id, status="idle")

        # 9. Verify PTY alive after a few seconds
        time.sleep(3)
        try:
            resp = rws.execute({"action": "pty_list"})
            ptys = resp.get("ptys", [])
            alive = any(p["pty_id"] == pty_id and p["alive"] for p in ptys)
            if not alive:
                # PTY died — read ringbuffer for error info
                try:
                    cap = rws.execute({"action": "pty_capture", "pty_id": pty_id, "lines": 30})
                    error_output = cap.get("data", "")
                except Exception:
                    error_output = "(could not read PTY output)"
                raise RuntimeError(f"Claude failed to start in RWS PTY: {error_output[:300]}")
        except RuntimeError:
            raise
        except Exception:
            logger.warning("Could not verify PTY status for %s, continuing", name)

        return {"ok": True, "tunnel_pid": tunnel_pid}

    except Exception as e:
        logger.exception("Failed to set up remote worker %s", name)
        # Clean up tunnel subprocess on failure
        if tunnel_manager:
            try:
                tunnel_manager.stop_tunnel(session_id)
            except Exception:
                pass
        return {"ok": False, "error": str(e)}
    finally:
        try:
            lock.release()
        except RuntimeError:
            pass


def setup_local_worker(
    conn: sqlite3.Connection,
    session_id: str,
    name: str,
    tmux_session: str = "orchestrator",
    api_port: int = 8093,
    work_dir: str | None = None,
    tmp_dir: str | None = None,
    custom_skills: list[dict] | None = None,
    disabled_builtin_names: set[str] | None = None,
    update_before_start: bool = False,
) -> dict:
    """Set up a local worker: deploy scripts, hooks, skills, prompt, launch Claude.

    This is the local equivalent of ``setup_remote_worker``.  Everything runs
    on the local machine — no SSH, no screen, no tunnel.

    Returns {"ok": True} on success, or {"ok": False, "error": "..."} on failure.
    """
    from orchestrator.agents.deploy import (
        _deploy_builtin_skills,
        deploy_worker_tmp_contents,
        get_worker_skills_dir,
    )

    local_tmp_dir = tmp_dir or f"/tmp/orchestrator/workers/{name}"

    try:
        api_base = f"http://127.0.0.1:{api_port}"
        cdp_port = 9222  # Shared Chrome instance — each worker gets its own tab

        # 1. Deploy all tmp dir contents via SOT function
        deploy_worker_tmp_contents(
            local_tmp_dir,
            session_id,
            api_base=api_base,
            cdp_port=cdp_port,
            browser_headless=False,  # Local: headed for Touch ID / passkeys
            custom_skills=custom_skills,
            disabled_builtin_names=disabled_builtin_names,
        )
        logger.info("Deployed worker tmp contents for local worker %s", name)

        # 2. Deploy skills to work_dir/.claude/commands/ (separate from tmp dir)
        # Local workers need skills in the project directory for Claude to discover them.
        skills_src = get_worker_skills_dir()
        if skills_src and os.path.isdir(skills_src) and work_dir:
            skills_dest = os.path.join(work_dir, ".claude", "commands")
            _deploy_builtin_skills(skills_src, skills_dest, disabled_builtin_names)
            logger.info("Deployed built-in skills to %s for local worker %s", skills_dest, name)

        if custom_skills and work_dir:
            skills_dest = os.path.join(work_dir, ".claude", "commands")
            deploy_custom_skills(skills_dest, custom_skills)
            logger.info(
                "Deployed %d custom worker skills for local worker %s", len(custom_skills), name
            )

        # 3. Build and send claude command
        prompt_file = os.path.join(local_tmp_dir, "prompt.md")

        cmd_parts = []
        if work_dir:
            cmd_parts.append(f"cd {work_dir}")

        cmd_parts.append("volta install node@24")  # Ensure Node 24 for npx
        cmd_parts.append(f"({_PW_INSTALL_CMD} || true)")  # Ensure Playwright plugin

        # Configure Playwright plugin to connect via per-worker CDP proxy.
        # Each worker gets its own proxy port so Playwright only sees its tab.
        from orchestrator.browser.cdp_worker_proxy import start_cdp_proxy

        try:
            proxy_port = start_cdp_proxy(session_id, chrome_port=cdp_port)
        except Exception:
            logger.warning("CDP proxy failed for %s, falling back to direct", name)
            proxy_port = cdp_port
        cmd_parts.append(f"export PLAYWRIGHT_MCP_CDP_ENDPOINT=http://localhost:{proxy_port}")

        path_export = get_path_export_command(os.path.join(local_tmp_dir, "bin"))
        cmd_parts.append(path_export)

        if update_before_start:
            from orchestrator.terminal.claude_update import get_claude_update_chain_command

            cmd_parts.append(get_claude_update_chain_command())

        settings_file = os.path.join(local_tmp_dir, "configs", "settings.json")
        claude_args = [
            "--dangerously-skip-permissions",
            f"--settings {shlex.quote(settings_file)}",
            f"--session-id {session_id}",
        ]
        if os.path.exists(prompt_file):
            claude_args.append(f'--append-system-prompt "$(cat {shlex.quote(prompt_file)})"')

        cmd_parts.append(f"claude {' '.join(claude_args)}")

        cmd = " && ".join(cmd_parts)
        tmux.send_keys(tmux_session, name, cmd, enter=True)
        logger.info("Launched Claude for local worker %s (work_dir=%s)", name, work_dir)

        # Dismiss any "trust this folder" prompt that may appear after launch
        tmux.dismiss_trust_prompt(tmux_session, name, session_id=session_id)

        return {"ok": True}

    except Exception as e:
        logger.exception("Failed to set up local worker %s", name)
        return {"ok": False, "error": str(e)}


# Backward-compat alias
setup_rdev_worker = setup_remote_worker
