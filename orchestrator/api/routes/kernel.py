"""REST endpoints for Jupyter kernel lifecycle and installation."""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import platform
import shutil
import site
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from orchestrator.kernel.manager import (
    KernelPool,
    is_kernel_support_available,
    list_kernelspecs,
    refresh_jupyter_availability,
)
from orchestrator.state.repositories import sessions as repo
from orchestrator.terminal.remote_worker_server import (
    ensure_rws_starting,
    get_remote_worker_server,
)
from orchestrator.terminal.ssh import is_remote_host

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions/{session_id}/kernel", tags=["kernel"])

# ── Venv location (local installs) ──────────────────────────────────────


def _kernel_venv_dir() -> Path:
    """Platform-appropriate directory for the orchestrator's Jupyter venv.

    macOS:  ~/Library/Application Support/orchestrator/jupyter-env
    Linux:  ~/.local/share/orchestrator/jupyter-env
    """
    if platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "orchestrator" / "jupyter-env"


def _venv_python(venv: Path) -> Path:
    return venv / "bin" / "python"


def _venv_pip(venv: Path) -> Path:
    return venv / "bin" / "pip"


def _add_venv_to_path(venv: Path) -> None:
    """Add the venv's site-packages to sys.path so we can import jupyter_client."""
    for sp in glob.glob(str(venv / "lib" / "python*" / "site-packages")):
        if sp not in sys.path:
            sys.path.insert(0, sp)
            site.addsitedir(sp)


# ── Helpers ──────────────────────────────────────────────────────────────


def _get_session_host(request: Request, session_id: str) -> str:
    db = request.app.state.conn
    session = repo.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.host


# ── Routes ───────────────────────────────────────────────────────────────


@router.get("/specs")
async def get_kernel_specs(session_id: str, request: Request) -> dict:
    """List available kernel specs, or report that jupyter is not installed."""
    host = _get_session_host(request, session_id)
    remote = is_remote_host(host)

    # For remote sessions, check kernel availability on the remote host via RWS
    if remote:
        try:
            ensure_rws_starting(host)
            rws = get_remote_worker_server(host)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: rws.execute({"action": "kernel_status"}, timeout=10),
            )
            available = result.get("available", False)
            remote_spec = {
                "name": "orchestrator-python",
                "display_name": "Python 3 (orchestrator)",
                "language": "python",
            }
            return {
                "available": available,
                "specs": [remote_spec] if available else [],
                "install_hint": "pip install ipykernel" if not available else None,
                "is_remote": True,
                "host": host,
            }
        except Exception:
            # RWS not reachable — report as unavailable
            return {
                "available": False,
                "specs": [],
                "install_hint": "pip install ipykernel",
                "is_remote": True,
                "host": host,
            }

    # Local session — check on this machine
    if not is_kernel_support_available():
        return {
            "available": False,
            "specs": [],
            "install_hint": "pip install jupyter_client ipykernel",
            "is_remote": False,
            "host": host,
        }

    specs = list_kernelspecs()
    return {
        "available": True,
        "specs": list(specs.values()),
        "install_hint": None,
        "is_remote": False,
        "host": host,
    }


@router.get("/status")
async def get_kernel_status(session_id: str, notebook_path: str = "") -> dict:
    pool = KernelPool.get_instance()
    ks = pool.get_session(session_id, notebook_path)
    if not ks:
        return {"status": "none", "kernel_name": None, "execution_count": 0}
    return {
        "status": ks.status,
        "kernel_name": ks.kernel_name,
        "execution_count": ks.execution_count,
    }


@router.post("/install")
async def install_kernel_deps(session_id: str, request: Request) -> StreamingResponse:
    """Set up a dedicated Python venv with ipykernel and register it.

    For LOCAL sessions: creates a venv on the local machine.
    For REMOTE sessions: sends a kernel_install command to the RWS daemon
    which creates the venv on the remote host.

    Streams progress as SSE (text/event-stream).
    """
    host = _get_session_host(request, session_id)
    remote = is_remote_host(host)

    if remote:
        return StreamingResponse(
            _stream_remote_install(host),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return StreamingResponse(
        _stream_local_install(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Local installation ───────────────────────────────────────────────────


async def _stream_local_install():
    """Create venv + install ipykernel on the local machine."""
    venv_dir = _kernel_venv_dir()
    venv_py = _venv_python(venv_dir)
    venv_pip = _venv_pip(venv_dir)

    base_python = shutil.which("python3") or sys.executable
    yield "data: Setting up Python kernel environment...\n\n"
    yield f"data: Location: {venv_dir}\n\n"

    # Create venv
    if not venv_py.exists():
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        yield "data: Creating virtual environment...\n\n"
        proc = await asyncio.create_subprocess_exec(
            base_python,
            "-m",
            "venv",
            str(venv_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out = await proc.stdout.read() if proc.stdout else b""  # type: ignore[union-attr]
        rc = await proc.wait()
        if rc != 0:
            text = out.decode("utf-8", errors="replace").strip()
            yield f"data: [error] Failed to create venv: {text}\n\n"
            return
        yield "data: Virtual environment created.\n\n"
    else:
        yield "data: Using existing environment.\n\n"

    # Install ipykernel
    yield "data: Installing ipykernel...\n\n"
    proc = await asyncio.create_subprocess_exec(
        *[str(c) for c in [venv_pip, "install", "-q", "ipykernel", "jupyter_client"]],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    async for line in proc.stdout:  # type: ignore[union-attr]
        text = line.decode("utf-8", errors="replace").rstrip()
        if text:
            yield f"data:   {text}\n\n"
    if await proc.wait() != 0:
        yield "data: [error] pip install failed\n\n"
        return

    # Register kernel spec
    yield "data: Registering kernel...\n\n"
    proc = await asyncio.create_subprocess_exec(
        *[
            str(c)
            for c in [
                venv_py,
                "-m",
                "ipykernel",
                "install",
                "--user",
                "--name",
                "orchestrator-python",
                "--display-name",
                "Python 3 (orchestrator)",
            ]
        ],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    async for line in proc.stdout:  # type: ignore[union-attr]
        text = line.decode("utf-8", errors="replace").rstrip()
        if text:
            yield f"data:   {text}\n\n"
    if await proc.wait() != 0:
        yield "data: [error] Kernel registration failed\n\n"
        return

    # Add venv to sys.path so orchestrator can import jupyter_client
    _add_venv_to_path(venv_dir)
    refresh_jupyter_availability()

    yield "data: [done]\n\n"
    logger.info("Local kernel environment set up: %s", venv_dir)


# ── Remote installation (via RWS daemon) ─────────────────────────────────


async def _stream_remote_install(host: str):
    """Install ipykernel on a remote host via the RWS daemon."""
    yield f"data: Setting up Python kernel on {host}...\n\n"

    # Ensure RWS is running
    ensure_rws_starting(host)
    yield "data: Connecting to remote host...\n\n"

    loop = asyncio.get_event_loop()
    max_polls = 60  # 60 * 2s = 2 min timeout

    for i in range(max_polls):
        try:
            rws = get_remote_worker_server(host)
            result = await loop.run_in_executor(
                None,
                lambda: rws.execute({"action": "kernel_install"}, timeout=30),
            )
        except Exception as e:
            if i < 3:
                yield "data: Waiting for remote connection...\n\n"
                await asyncio.sleep(2)
                continue
            yield f"data: [error] Failed to connect to {host}: {e}\n\n"
            return

        status = result.get("status", "")
        error = result.get("error")

        if error:
            yield f"data: [error] {error}\n\n"
            return

        if status == "ok":
            msg = result.get("message", "Installation complete")
            yield f"data: {msg}\n\n"

            # For remote installs, we also need jupyter_client locally
            # to communicate with the remote kernel. Add the local venv if it exists.
            local_venv = _kernel_venv_dir()
            if local_venv.exists():
                _add_venv_to_path(local_venv)
                refresh_jupyter_availability()

            yield "data: [done]\n\n"
            logger.info("Remote kernel environment set up on %s", host)
            return

        if status == "installing":
            yield "data: Installing on remote host...\n\n"
            await asyncio.sleep(2)
            continue

        yield f"data: [error] Unexpected response: {result}\n\n"
        return

    yield "data: [error] Installation timed out after 2 minutes\n\n"
