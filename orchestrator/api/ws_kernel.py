"""WebSocket endpoint for Jupyter kernel communication.

Protocol
--------
All frames are JSON text.

Client → Server:
  { "type": "start",     "kernel_name": "python3", "notebook_path": "...", "work_dir": "..." }
  { "type": "execute",   "cell_id": "...", "code": "..." }
  { "type": "interrupt" }
  { "type": "restart" }
  { "type": "shutdown" }

Server → Client:
  { "type": "status",           "status": "idle"|"busy"|"starting"|"dead" }
  { "type": "stream",           "cell_id": "...", "name": "stdout"|"stderr", "text": "..." }
  { "type": "display",          "cell_id": "...", "data": {...}, "metadata": {...} }
  { "type": "error",  "cell_id": "...", "ename": "...", "evalue": "...", "traceback": [...] }
  { "type": "execute_complete",  "cell_id": "...", "execution_count": N }
  { "type": "specs",            "available": bool, "specs": [...], "install_hint": "..." | null }
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

from orchestrator.kernel.manager import (
    KernelPool,
    KernelSession,
    is_kernel_support_available,
)

logger = logging.getLogger(__name__)

# Try to import activity tracking from terminal WS
try:
    from orchestrator.api.ws_terminal import record_user_input
except ImportError:

    def record_user_input(session_id: str) -> None:  # type: ignore[misc]
        pass


async def ws_kernel(websocket: WebSocket, session_id: str) -> None:
    """WebSocket handler for kernel communication."""
    await websocket.accept()

    pool = KernelPool.get_instance()
    kernel_session: KernelSession | None = None
    message_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    unsubscribe: Any = None

    def on_kernel_message(msg: dict[str, Any]) -> None:
        """Callback from kernel iopub reader — enqueue for WS sender."""
        try:
            message_queue.put_nowait(msg)
        except asyncio.QueueFull:
            logger.warning("Kernel message queue full, dropping message")

    async def send_messages() -> None:
        """Background task: drain message_queue and send to WebSocket."""
        try:
            while True:
                msg = await message_queue.get()
                await websocket.send_json(msg)
        except (WebSocketDisconnect, Exception):
            pass

    sender_task: asyncio.Task[None] | None = None

    try:
        # Start the sender task
        sender_task = asyncio.create_task(send_messages())

        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = msg.get("type", "")
            record_user_input(session_id)

            if msg_type == "start":
                kernel_name = msg.get("kernel_name", "python3")
                notebook_path = msg.get("notebook_path", "")
                work_dir = msg.get("work_dir", ".")

                if not is_kernel_support_available():
                    await websocket.send_json(
                        {
                            "type": "specs",
                            "available": False,
                            "specs": [],
                            "install_hint": "pip install jupyter_client ipykernel",
                        }
                    )
                    continue

                try:
                    kernel_session = await pool.start_kernel(
                        session_id, notebook_path, kernel_name, work_dir
                    )
                    unsubscribe = kernel_session.subscribe(on_kernel_message)
                    await websocket.send_json(
                        {
                            "type": "status",
                            "status": kernel_session.status,
                        }
                    )
                except Exception as e:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": f"Failed to start kernel: {e}",
                        }
                    )

            elif msg_type == "execute":
                cell_id = msg.get("cell_id", "")
                code = msg.get("code", "")
                if not kernel_session:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "No kernel running. Send 'start' first.",
                        }
                    )
                    continue
                try:
                    kernel_session.execute(cell_id, code)
                except Exception as e:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "cell_id": cell_id,
                            "ename": "KernelError",
                            "evalue": str(e),
                            "traceback": [],
                        }
                    )

            elif msg_type == "interrupt":
                if kernel_session:
                    kernel_session.interrupt()

            elif msg_type == "restart":
                if kernel_session:
                    await kernel_session.restart()

            elif msg_type == "shutdown":
                if kernel_session:
                    notebook_path = kernel_session.notebook_path
                    if unsubscribe:
                        unsubscribe()
                        unsubscribe = None
                    await pool.shutdown_kernel(session_id, notebook_path)
                    kernel_session = None
                    await websocket.send_json({"type": "status", "status": "none"})

    except WebSocketDisconnect:
        logger.debug("Kernel WebSocket disconnected: session=%s", session_id)
    except Exception:
        logger.exception("Kernel WebSocket error: session=%s", session_id)
    finally:
        if sender_task:
            sender_task.cancel()
            try:
                await sender_task
            except asyncio.CancelledError:
                pass
        if unsubscribe:
            unsubscribe()
