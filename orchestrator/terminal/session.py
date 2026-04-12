"""Full session lifecycle: create, start agent, remove."""

from __future__ import annotations

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
from orchestrator.terminal.file_sync import _ssh_cmd
from orchestrator.terminal.ssh import is_rdev_host

logger = logging.getLogger(__name__)

_SOURCE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Shell one-liner: skip `claude plugin install` if plugin already present.
_PW_INSTALL_CMD = (
    "claude plugin list 2>/dev/null | grep -q 'playwright@' || claude plugin install playwright"
)

# Shell snippet: resolve actual Node 24 binary from volta's image directory.
_VOLTA_NODE24_RESOLVE = (
    "NODE24_BIN=$(ls -d ~/.volta/tools/image/node/24.*/bin/node 2>/dev/null"
    " | sort -V | tail -1)"
    ' && [ -n "$NODE24_BIN" ]'
    ' && NODE24_DIR=$(dirname "$NODE24_BIN")'
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
        tmux.send_keys(tmux_session, name, f"cd {shlex.quote(work_dir)}")
        time.sleep(0.5)

    # Persist to DB
    session = sessions_repo.create_session(conn, name=name, host=host, work_dir=work_dir)
    logger.info("Created session: %s (host=%s, path=%s)", name, host, work_dir)
    return session


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
    """Check if a message was successfully submitted (no longer in input line)."""
    # Give agent a moment to process the Enter
    time.sleep(0.3)

    # Capture recent output
    output = tmux.capture_output(tmux_session, window_name, lines=10)

    # Get the last few lines to check for stuck input
    lines = output.strip().split("\n")
    if not lines:
        return True  # Empty output, assume sent

    last_line = lines[-1].strip()

    if len(message) > 50:
        message_tail = message[-100:] if len(message) > 100 else message
        message_tail_normalized = " ".join(message_tail.split())
        last_line_normalized = " ".join(last_line.split())

        if len(last_line_normalized) > 20 and message_tail_normalized[-50:] in last_line_normalized:
            logger.debug("Message appears stuck in input - last line matches message tail")
            return False

    # Also check if the cursor line appears to have unsubmitted content
    if (last_line.startswith(">") or last_line.startswith("?")) and len(last_line) > 20:
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
    """Send a message to a session's agent instance."""
    if not tmux.paste_to_pane(tmux_session, name, message):
        logger.warning("paste_to_pane failed, falling back to send_keys_literal")
        if not tmux.send_keys_literal(tmux_session, name, message):
            return False

    time.sleep(0.3)

    # Send Enter and verify it was submitted
    for attempt in range(max_enter_retries):
        if not tmux.send_keys(tmux_session, name, "", enter=True):
            return False

        if _verify_message_sent(tmux_session, name, message):
            if attempt > 0:
                logger.info("Message sent successfully after %d Enter retries", attempt + 1)
            return True

        if attempt < max_enter_retries - 1:
            logger.warning(
                "Message may be stuck in input, retrying Enter (attempt %d/%d)",
                attempt + 1,
                max_enter_retries,
            )
            time.sleep(retry_delay)

    logger.error("Failed to send message after %d Enter attempts", max_enter_retries)
    return False


def _wait_for_command_completion(
    tmux_session: str, window_name: str, timeout: int = 60, poll_interval: float = 2.0
) -> bool:
    """Wait for a command to complete by checking for shell prompt return."""
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
    """Copy a local directory to a remote host using direct SSH subprocess."""
    try:
        mkdir_result = subprocess.run(
            _ssh_cmd(host, f"mkdir -p {shlex.quote(remote_dir)}"),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if mkdir_result.returncode != 0:
            logger.error("Failed to create remote dir %s: %s", remote_dir, mkdir_result.stderr)
            return False

        tar_proc = subprocess.Popen(
            ["tar", "czf", "-", "-C", local_dir, "."],
            stdout=subprocess.PIPE,
        )

        ssh_proc = subprocess.Popen(
            _ssh_cmd(host, f"tar xzf - -C {shlex.quote(remote_dir)}"),
            stdin=tar_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

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


def _ensure_rws_ready(host: str, timeout: float = 30.0):
    """Synchronously ensure RWS daemon is deployed and connected."""
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
    skip_permissions: bool = False,
    model: str = "opus",
    effort: str = "high",
    provider: str = "claude",
) -> dict:
    """Set up a full remote worker via RWS PTY (new architecture)."""
    from orchestrator.providers.runtime import get_provider_runtime
    from orchestrator.session.reconnect import get_reconnect_lock

    runtime = get_provider_runtime(provider)
    remote_tmp_dir = f"/tmp/orchestrator/workers/{name}"
    local_tmp_dir = tmp_dir or f"/tmp/orchestrator/workers/{name}"

    lock = get_reconnect_lock(session_id)
    lock.acquire(timeout=5)

    try:
        # 0. Ensure rdev host is running (auto-start if stopped)
        from orchestrator.session.reconnect import _ensure_rdev_running

        if not _ensure_rdev_running(session_id, host):
            return {"ok": False, "error": f"Rdev host {host} is stopped and could not be started"}

        if is_rdev_host(host):
            from orchestrator.terminal.ssh import ensure_rdev_ssh_config

            if not ensure_rdev_ssh_config(host):
                return {"ok": False, "error": f"Could not bootstrap SSH config for {host}"}

        # 1. Start reverse SSH tunnel
        tunnel_pid = None
        if tunnel_manager:
            tunnel_pid = tunnel_manager.start_tunnel(session_id, name, host)
            if tunnel_pid:
                logger.info("Started reverse tunnel for %s -> %s (pid=%d)", name, host, tunnel_pid)
        time.sleep(2)

        # 2. Deploy all files locally
        from orchestrator.agents.deploy import deploy_worker_tmp_contents

        deploy_worker_tmp_contents(
            local_tmp_dir,
            session_id,
            api_base=f"http://127.0.0.1:{api_port}",
            cdp_port=9222,
            browser_headless=True,
            custom_skills=custom_skills,
            disabled_builtin_names=disabled_builtin_names,
            model=model,
            effort=effort,
            provider=provider,
        )

        # 3. Copy to remote
        if not _copy_dir_to_remote_ssh(local_tmp_dir, host, remote_tmp_dir):
            raise RuntimeError(f"Failed to copy files to remote via SSH: {host}:{remote_tmp_dir}")

        # 4. Copy skills to commands/ (provider-agnostic path)
        skills_copy_cmd = (
            "rm -rf ~/commands 2>/dev/null;"
            " mkdir -p ~/commands"
            f" && cp {remote_tmp_dir}/commands/*.md"
            " ~/commands/ 2>/dev/null || true"
        )
        subprocess.run(_ssh_cmd(host, skills_copy_cmd), capture_output=True, timeout=30)

        # 5. Install Node 24 via SSH subprocess
        if is_rdev_host(host):
            node_cmd = (
                "command -v volta >/dev/null 2>&1"
                " && volta install node@24"
                f" && {_VOLTA_NODE24_RESOLVE}"
                f" && mkdir -p {remote_tmp_dir}/node-bin"
                f' && ln -sf "$NODE24_DIR/node" {remote_tmp_dir}/node-bin/node'
                f' && ln -sf "$NODE24_DIR/npx" {remote_tmp_dir}/node-bin/npx'
                f' && ln -sf "$NODE24_DIR/npm" {remote_tmp_dir}/node-bin/npm'
                " || true"
            )
        else:
            node_cmd = "command -v volta >/dev/null 2>&1 && volta install node@24 2>/dev/null || true"
        subprocess.run(_ssh_cmd(host, node_cmd), capture_output=True, timeout=60)

        # 6. Ensure RWS daemon is running
        rws = _ensure_rws_ready(host, timeout=30)

        # 7. Build command and create PTY
        launch_cmd = runtime.get_launch_command(
            session_id,
            remote_tmp_dir,
            model=model,
            effort=effort,
            skip_permissions=skip_permissions,
        )

        # Pre-wrap with PATH and plugins for Claude
        if provider == "claude":
            parts = []
            if is_rdev_host(host):
                parts.append(f'export PATH="{remote_tmp_dir}/node-bin:{remote_tmp_dir}/bin:$HOME/.local/bin:$PATH"')
            else:
                parts.append(get_path_export_command(f"{remote_tmp_dir}/bin"))
            parts.append(f"chmod +x {remote_tmp_dir}/bin/* 2>/dev/null || true")
            parts.append(f"({_PW_INSTALL_CMD} || true)")
            parts.append("export PLAYWRIGHT_MCP_CDP_ENDPOINT=http://localhost:9222")
            if work_dir:
                parts.append(f"cd {work_dir}")
            parts.append(launch_cmd)
            final_cmd = " && ".join(parts)
        else:
            parts = [get_path_export_command(f"{remote_tmp_dir}/bin")]
            if work_dir:
                parts.append(f"cd {work_dir}")
            parts.append(launch_cmd)
            final_cmd = " && ".join(parts)

        pty_id = rws.create_pty(
            cmd=final_cmd,
            cwd=work_dir or os.path.expanduser("~"),
            cols=120,
            rows=40,
            session_id=session_id,
            role="main",
        )
        sessions_repo.update_session(conn, session_id, rws_pty_id=pty_id, status="idle")

        # 9. Verify PTY alive
        time.sleep(3)
        try:
            resp = rws.execute({"action": "pty_list"})
            ptys = resp.get("ptys", [])
            alive = any(p["pty_id"] == pty_id and p["alive"] for p in ptys)
            if not alive:
                raise RuntimeError("Agent failed to start in RWS PTY")
        except Exception as e:
            logger.warning("Could not verify PTY status: %s", e)

        return {"ok": True, "tunnel_pid": tunnel_pid}

    except Exception as e:
        logger.exception("Failed to set up remote worker %s", name)
        if tunnel_manager:
            try: tunnel_manager.stop_tunnel(session_id)
            except: pass
        return {"ok": False, "error": str(e)}
    finally:
        try: lock.release()
        except: pass


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
    skip_permissions: bool = False,
    model: str = "opus",
    effort: str = "high",
    provider: str = "claude",
) -> dict:
    """Set up a local worker: deploy files, launch agent."""
    from orchestrator.providers.runtime import get_provider_runtime
    from orchestrator.agents.deploy import (
        _deploy_builtin_skills,
        deploy_worker_tmp_contents,
        get_worker_skills_dir,
    )

    runtime = get_provider_runtime(provider)
    local_tmp_dir = tmp_dir or f"/tmp/orchestrator/workers/{name}"

    try:
        api_base = f"http://127.0.0.1:{api_port}"
        cdp_port = 9222

        # 1. Deploy tmp dir
        deploy_worker_tmp_contents(
            local_tmp_dir,
            session_id,
            api_base=api_base,
            cdp_port=cdp_port,
            browser_headless=False,
            custom_skills=custom_skills,
            disabled_builtin_names=disabled_builtin_names,
            model=model,
            effort=effort,
            provider=provider,
        )

        # 2. Deploy skills to commands/ (provider-agnostic)
        skills_src = get_worker_skills_dir()
        if skills_src and os.path.isdir(skills_src) and work_dir:
            skills_dest = os.path.join(work_dir, "commands")
            _deploy_builtin_skills(skills_src, skills_dest, disabled_builtin_names)
            if custom_skills:
                deploy_custom_skills(skills_dest, custom_skills)

        # 3. Build and send command
        cmd_parts = []
        if work_dir:
            cmd_parts.append(f"cd {work_dir}")

        # Common env setup
        cmd_parts.append("command -v volta >/dev/null 2>&1 && volta install node@24 || true")
        if provider == "claude":
            cmd_parts.append(f"({_PW_INSTALL_CMD} || true)")

        from orchestrator.browser.cdp_worker_proxy import start_cdp_proxy
        try:
            proxy_port = start_cdp_proxy(session_id, chrome_port=cdp_port)
        except Exception:
            proxy_port = cdp_port
        cmd_parts.append(f"export PLAYWRIGHT_MCP_CDP_ENDPOINT=http://localhost:{proxy_port}")
        cmd_parts.append(get_path_export_command(os.path.join(local_tmp_dir, "bin")))

        if update_before_start and provider == "claude":
            from orchestrator.terminal.claude_update import get_claude_update_chain_command
            cmd_parts.append(get_claude_update_chain_command())

        # Get launch command from runtime
        launch_cmd = runtime.get_launch_command(
            session_id,
            local_tmp_dir,
            model=model,
            effort=effort,
            skip_permissions=skip_permissions,
        )
        cmd_parts.append(launch_cmd)

        cmd = " && ".join(cmd_parts)
        tmux.send_keys(tmux_session, name, cmd, enter=True)
        tmux.dismiss_trust_prompt(tmux_session, name, session_id=session_id)

        return {"ok": True}

    except Exception as e:
        logger.exception("Failed to set up local worker %s", name)
        return {"ok": False, "error": str(e)}


# Backward-compat aliases
setup_rdev_worker = setup_remote_worker
setup_local_worker_alias = setup_local_worker
