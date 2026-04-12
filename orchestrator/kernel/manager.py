"""Jupyter kernel lifecycle management.

Detects jupyter_client at runtime — if not installed, the module is still
importable but all kernel operations return clear errors. The orchestrator
never bundles jupyter_client; users install it per-host.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# ── Runtime detection ────────────────────────────────────────────────────

_jupyter_available = False
try:
    import jupyter_client  # type: ignore[import-untyped]

    _jupyter_available = True
except ImportError:
    jupyter_client = None  # type: ignore[assignment]


def is_kernel_support_available() -> bool:
    """Check if jupyter_client is installed on this host."""
    return _jupyter_available


def refresh_jupyter_availability() -> bool:
    """Re-check if jupyter_client is importable (call after pip install)."""
    global _jupyter_available, jupyter_client  # noqa: PLW0603
    try:
        import importlib

        mod = importlib.import_module("jupyter_client")
        jupyter_client = mod  # type: ignore[assignment]
        _jupyter_available = True
    except ImportError:
        _jupyter_available = False
    return _jupyter_available


def list_kernelspecs() -> dict[str, dict[str, Any]]:
    """Return available kernel specs, or empty dict if jupyter not installed."""
    if not _jupyter_available:
        return {}
    try:
        specs = jupyter_client.kernelspec.find_kernel_specs()  # type: ignore[union-attr]
        result: dict[str, dict[str, Any]] = {}
        for name, path in specs.items():
            try:
                spec = jupyter_client.kernelspec.get_kernel_spec(name)  # type: ignore[union-attr]
                result[name] = {
                    "name": name,
                    "display_name": spec.display_name,
                    "language": getattr(spec, "language", ""),
                    "path": path,
                }
            except Exception:
                result[name] = {"name": name, "display_name": name, "language": "", "path": path}
        return result
    except Exception as e:
        logger.warning("Failed to list kernelspecs: %s", e)
        return {}


# ── Kernel session ───────────────────────────────────────────────────────

# Callback type for iopub message subscribers
KernelMessageCallback = Callable[[dict[str, Any]], None]


class KernelSession:
    """Wraps a single jupyter_client.KernelManager for one notebook."""

    def __init__(
        self,
        session_id: str,
        notebook_path: str,
        kernel_name: str,
        work_dir: str,
    ) -> None:
        self.session_id = session_id
        self.notebook_path = notebook_path
        self.kernel_name = kernel_name
        self.work_dir = work_dir
        self.kernel_id = str(uuid.uuid4())[:8]
        self.status = "starting"
        self.execution_count = 0
        self._subscribers: list[KernelMessageCallback] = []
        self._iopub_task: asyncio.Task[None] | None = None
        self._shell_task: asyncio.Task[None] | None = None
        self._km: Any = None  # jupyter_client.KernelManager
        self._kc: Any = None  # jupyter_client.KernelClient
        # Map msg_id → cell_id for routing iopub messages to the correct cell
        self._msg_to_cell: dict[str, str] = {}

    async def start(self) -> None:
        """Start the kernel subprocess."""
        if not _jupyter_available:
            raise RuntimeError("jupyter_client is not installed")

        # Validate kernel name against known specs
        known = list_kernelspecs()
        if self.kernel_name not in known:
            available = ", ".join(known.keys()) if known else "(none found)"
            raise ValueError(f"Unknown kernel {self.kernel_name!r}. Available: {available}")

        self._km = jupyter_client.KernelManager(kernel_name=self.kernel_name)  # type: ignore[union-attr]
        self._km.start_kernel(cwd=self.work_dir)
        self._kc = self._km.client()
        self._kc.start_channels()

        # Wait for kernel to be ready
        try:
            self._kc.wait_for_ready(timeout=30)
        except Exception as e:
            logger.error("Kernel failed to start: %s", e)
            self.status = "dead"
            await self.shutdown()
            raise

        self.status = "idle"
        self._iopub_task = asyncio.create_task(self._iopub_reader())
        self._shell_task = asyncio.create_task(self._shell_reader())
        logger.info(
            "Kernel started: %s (kernel=%s, cwd=%s)",
            self.kernel_id,
            self.kernel_name,
            self.work_dir,
        )

    async def shutdown(self) -> None:
        """Shut down the kernel and clean up."""
        for task in (self._iopub_task, self._shell_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._iopub_task = None
        self._shell_task = None

        if self._kc:
            self._kc.stop_channels()
            self._kc = None

        if self._km and self._km.is_alive():
            self._km.shutdown_kernel(now=False)
            # If still alive after graceful shutdown, force kill
            try:
                await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, lambda: self._km.is_alive() and self._km.shutdown_kernel(now=True)
                    ),
                    timeout=5.0,
                )
            except (TimeoutError, Exception):
                pass
            self._km = None

        self.status = "dead"
        self._subscribers.clear()
        logger.info("Kernel shut down: %s", self.kernel_id)

    async def restart(self) -> None:
        """Restart the kernel (preserves connection, resets state)."""
        if self._km:
            self.status = "starting"
            self._notify_subscribers({"type": "status", "status": "starting"})
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._km.restart_kernel(now=False)
            )
            # Wait for ready
            try:
                self._kc.wait_for_ready(timeout=30)
            except Exception:
                self.status = "dead"
                self._notify_subscribers({"type": "status", "status": "dead"})
                return
            self.status = "idle"
            self.execution_count = 0
            self._msg_to_cell.clear()
            self._notify_subscribers({"type": "status", "status": "idle"})

    def interrupt(self) -> None:
        """Send SIGINT to the kernel."""
        if self._km and self._km.is_alive():
            self._km.interrupt_kernel()

    def execute(self, cell_id: str, code: str) -> str:
        """Submit code for execution. Returns the message ID."""
        if not self._kc:
            raise RuntimeError("Kernel not connected")
        msg_id = self._kc.execute(code, allow_stdin=False)
        self._msg_to_cell[msg_id] = cell_id
        return msg_id

    def subscribe(self, callback: KernelMessageCallback) -> Callable[[], None]:
        """Subscribe to kernel messages. Returns an unsubscribe function."""
        self._subscribers.append(callback)
        return lambda: self._subscribers.remove(callback) if callback in self._subscribers else None

    def _notify_subscribers(self, msg: dict[str, Any]) -> None:
        for cb in self._subscribers:
            try:
                cb(msg)
            except Exception:
                logger.exception("Error in kernel message subscriber")

    async def _iopub_reader(self) -> None:
        """Background task that reads iopub messages and forwards to subscribers."""
        loop = asyncio.get_event_loop()
        while True:
            try:
                # Run blocking get_iopub_msg in executor to not block the event loop
                msg = await loop.run_in_executor(None, lambda: self._kc.get_iopub_msg(timeout=1.0))
            except Exception:
                # Timeout or channel closed — check if kernel is alive
                if self._km and not self._km.is_alive():
                    self.status = "dead"
                    self._notify_subscribers({"type": "status", "status": "dead"})
                    return
                continue

            msg_type = msg.get("msg_type", "")
            parent_msg_id = msg.get("parent_header", {}).get("msg_id", "")
            cell_id = self._msg_to_cell.get(parent_msg_id)
            content = msg.get("content", {})

            if msg_type == "status":
                new_status = content.get("execution_state", "")
                if new_status in ("idle", "busy"):
                    self.status = new_status
                    self._notify_subscribers({"type": "status", "status": new_status})

            elif msg_type == "stream" and cell_id:
                self._notify_subscribers(
                    {
                        "type": "stream",
                        "cell_id": cell_id,
                        "name": content.get("name", "stdout"),
                        "text": content.get("text", ""),
                    }
                )

            elif msg_type in ("display_data", "execute_result") and cell_id:
                data = content.get("data", {})
                # Join arrays if present (some kernels send arrays)
                joined_data = {}
                for mime, val in data.items():
                    joined_data[mime] = "".join(val) if isinstance(val, list) else val
                out_msg: dict[str, Any] = {
                    "type": "display",
                    "cell_id": cell_id,
                    "data": joined_data,
                    "metadata": content.get("metadata", {}),
                }
                if msg_type == "execute_result":
                    self.execution_count = content.get("execution_count", self.execution_count)
                    out_msg["execution_count"] = self.execution_count
                self._notify_subscribers(out_msg)

            elif msg_type == "error" and cell_id:
                self._notify_subscribers(
                    {
                        "type": "error",
                        "cell_id": cell_id,
                        "ename": content.get("ename", "Error"),
                        "evalue": content.get("evalue", ""),
                        "traceback": content.get("traceback", []),
                    }
                )

    async def _shell_reader(self) -> None:
        """Background task that reads shell channel for execute_reply messages."""
        loop = asyncio.get_event_loop()
        while True:
            try:
                msg = await loop.run_in_executor(None, lambda: self._kc.get_shell_msg(timeout=1.0))
            except Exception:
                if self._km and not self._km.is_alive():
                    return
                continue

            msg_type = msg.get("msg_type", "")
            if msg_type != "execute_reply":
                continue

            parent_msg_id = msg.get("parent_header", {}).get("msg_id", "")
            cell_id = self._msg_to_cell.get(parent_msg_id)
            if not cell_id:
                continue

            content = msg.get("content", {})
            exec_count = content.get("execution_count", self.execution_count)
            self.execution_count = exec_count
            self._notify_subscribers(
                {
                    "type": "execute_complete",
                    "cell_id": cell_id,
                    "execution_count": exec_count,
                }
            )
            self._msg_to_cell.pop(parent_msg_id, None)


# ── Kernel pool (singleton) ──────────────────────────────────────────────


class KernelPool:
    """Manages kernel sessions across all notebooks."""

    _instance: KernelPool | None = None

    def __init__(self) -> None:
        self._sessions: dict[str, KernelSession] = {}

    @classmethod
    def get_instance(cls) -> KernelPool:
        if cls._instance is None:
            cls._instance = KernelPool()
        return cls._instance

    def _key(self, session_id: str, notebook_path: str) -> str:
        return f"{session_id}:{notebook_path}"

    def get_session(self, session_id: str, notebook_path: str) -> KernelSession | None:
        return self._sessions.get(self._key(session_id, notebook_path))

    async def start_kernel(
        self,
        session_id: str,
        notebook_path: str,
        kernel_name: str = "python3",
        work_dir: str = ".",
    ) -> KernelSession:
        key = self._key(session_id, notebook_path)
        existing = self._sessions.get(key)
        if existing and existing.status not in ("dead",):
            return existing

        ks = KernelSession(session_id, notebook_path, kernel_name, work_dir)
        self._sessions[key] = ks
        await ks.start()
        return ks

    async def shutdown_kernel(self, session_id: str, notebook_path: str) -> None:
        key = self._key(session_id, notebook_path)
        ks = self._sessions.pop(key, None)
        if ks:
            await ks.shutdown()

    async def restart_kernel(self, session_id: str, notebook_path: str) -> None:
        key = self._key(session_id, notebook_path)
        ks = self._sessions.get(key)
        if ks:
            await ks.restart()

    def interrupt_kernel(self, session_id: str, notebook_path: str) -> None:
        key = self._key(session_id, notebook_path)
        ks = self._sessions.get(key)
        if ks:
            ks.interrupt()

    async def shutdown_all(self) -> None:
        """Shut down all kernels. Called during app shutdown."""
        tasks = [ks.shutdown() for ks in self._sessions.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._sessions.clear()
        logger.info("All kernels shut down")
