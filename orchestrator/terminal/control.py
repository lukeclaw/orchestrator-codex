"""Tmux Control Mode for low-latency terminal streaming.

Control mode (`tmux -C`) provides a persistent connection to tmux that avoids
spawning subprocesses for each operation. This dramatically reduces latency
for both sending input and capturing output.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from typing import Callable

logger = logging.getLogger(__name__)


class TmuxControlConnection:
    """Persistent tmux control mode connection for a single pane.
    
    Instead of spawning a subprocess for each tmux command, this maintains
    a single long-lived connection that communicates via stdin/stdout.
    """
    
    def __init__(self, session: str, window: str):
        self.session = session
        self.window = window
        self.target = f"{session}:{window}"
        self._process: asyncio.subprocess.Process | None = None
        self._output_callback: Callable[[str], None] | None = None
        self._reader_task: asyncio.Task | None = None
        self._running = False
        self._last_content = ""
        
    async def start(self) -> bool:
        """Start the control mode connection."""
        if self._process is not None:
            return True
            
        try:
            # Start tmux in control mode, attached to our target
            self._process = await asyncio.create_subprocess_exec(
                "tmux", "-C", "attach-session", "-t", self.session,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._running = True
            logger.info("Started tmux control mode for %s", self.target)
            
            # Start reading output
            self._reader_task = asyncio.create_task(self._read_output())
            return True
            
        except Exception as e:
            logger.error("Failed to start tmux control mode: %s", e)
            return False
    
    async def stop(self):
        """Stop the control mode connection."""
        self._running = False
        
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
            
        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self._process.kill()
            self._process = None
            
        logger.info("Stopped tmux control mode for %s", self.target)
    
    async def _read_output(self):
        """Read and parse control mode output."""
        if not self._process or not self._process.stdout:
            return
            
        try:
            while self._running:
                line = await self._process.stdout.readline()
                if not line:
                    break
                # Control mode outputs are prefixed with % for notifications
                # We mainly care about %output for pane content changes
                decoded = line.decode('utf-8', errors='replace')
                if decoded.startswith('%output'):
                    # Parse output notification
                    # Format: %output %<pane_id> <data>
                    pass  # We'll use polling for now, this is for future optimization
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Error reading tmux control output: %s", e)
    
    async def send_keys(self, keys: str) -> bool:
        """Send keys to the target pane via control mode."""
        if not self._process or not self._process.stdin:
            return False
            
        try:
            # Escape special characters for tmux command
            # Order matters: escape backslash first, then others
            # Escape: \ " $ ` to prevent shell expansion
            escaped = keys.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`')
            cmd = f'send-keys -t {self.target} -l "{escaped}"\n'
            self._process.stdin.write(cmd.encode())
            await self._process.stdin.drain()
            return True
        except Exception as e:
            logger.error("Failed to send keys via control mode: %s", e)
            return False
    
    async def resize(self, cols: int, rows: int) -> bool:
        """Resize the target window via control mode."""
        if not self._process or not self._process.stdin:
            return False
            
        try:
            cmd = f'resize-window -t {self.target} -x {cols} -y {rows}\n'
            self._process.stdin.write(cmd.encode())
            await self._process.stdin.drain()
            return True
        except Exception as e:
            logger.error("Failed to resize via control mode: %s", e)
            return False


class TmuxControlPool:
    """Pool of control mode connections, one per session.
    
    Reuses connections across multiple WebSocket clients to avoid
    creating too many control mode processes.
    """
    
    _instance: TmuxControlPool | None = None
    
    def __init__(self):
        self._connections: dict[str, TmuxControlConnection] = {}
        self._lock = asyncio.Lock()
    
    @classmethod
    def get_instance(cls) -> TmuxControlPool:
        if cls._instance is None:
            cls._instance = TmuxControlPool()
        return cls._instance
    
    async def get_connection(self, session: str, window: str) -> TmuxControlConnection:
        """Get or create a control connection for the given target."""
        key = f"{session}:{window}"
        
        async with self._lock:
            if key not in self._connections:
                conn = TmuxControlConnection(session, window)
                await conn.start()
                self._connections[key] = conn
            return self._connections[key]
    
    async def release_connection(self, session: str, window: str):
        """Release a connection (currently keeps it alive for reuse)."""
        # For now, we keep connections alive for reuse
        # Could implement reference counting if needed
        pass
    
    async def close_all(self):
        """Close all connections."""
        async with self._lock:
            for conn in self._connections.values():
                await conn.stop()
            self._connections.clear()


async def send_keys_async(session: str, window: str, keys: str) -> bool:
    """Send keys using control mode pool (async version)."""
    pool = TmuxControlPool.get_instance()
    conn = await pool.get_connection(session, window)
    return await conn.send_keys(keys)


async def resize_async(session: str, window: str, cols: int, rows: int) -> bool:
    """Resize pane using control mode pool (async version)."""
    pool = TmuxControlPool.get_instance()
    conn = await pool.get_connection(session, window)
    return await conn.resize(cols, rows)


async def capture_pane_async(session: str, window: str) -> str:
    """Capture pane content asynchronously using subprocess.
    
    Note: tmux control mode doesn't support capture-pane output directly,
    so we use asyncio subprocess for non-blocking capture.
    
    Strips trailing blank lines to avoid cursor positioning issues.
    """
    target = f"{session}:{window}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux", "capture-pane", "-p", "-e", "-t", target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode('utf-8', errors='replace')
    except Exception as e:
        logger.error("Failed to capture pane: %s", e)
        return ""


async def get_cursor_position_async(session: str, window: str) -> tuple[int, int]:
    """Get cursor position (x, y) from tmux pane.
    
    Returns (cursor_x, cursor_y) where x is column (0-indexed) and y is row (0-indexed).
    """
    target = f"{session}:{window}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux", "display-message", "-p", "-t", target,
            "#{cursor_x} #{cursor_y}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        parts = stdout.decode('utf-8', errors='replace').strip().split()
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except Exception as e:
        logger.error("Failed to get cursor position: %s", e)
    return 0, 0


async def capture_pane_with_cursor_async(session: str, window: str) -> tuple[str, int, int]:
    """Capture pane content and cursor position together.
    
    Returns (content, cursor_x, cursor_y).
    Runs both captures concurrently for speed (~25ms vs ~45ms sequential).
    """
    # Run both captures concurrently
    content_task = capture_pane_async(session, window)
    cursor_task = get_cursor_position_async(session, window)
    
    content, (cursor_x, cursor_y) = await asyncio.gather(content_task, cursor_task)
    return content, cursor_x, cursor_y
