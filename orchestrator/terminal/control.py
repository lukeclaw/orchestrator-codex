"""Tmux Control Mode for low-latency terminal I/O.

Control mode (``tmux -C``) provides a persistent connection to tmux that
avoids spawning subprocesses for each operation.  Used for sending keys,
resizing windows, and capturing pane content.  Output streaming is handled
by pipe-pane (see ``pty_stream.py``).
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)


def _strip_tmux_sequences(data: bytes, state: dict[str, bool] | None = None) -> bytes:
    """Strip tmux-specific escape sequences that standard terminals don't understand.

    Removes ``ESC k <title> ST`` (set window name) sequences.  These are
    a tmux/screen convention, not part of the VT100/xterm standard.  If
    forwarded to xterm.js the title text appears as visible characters.

    If *state* is provided, stripping is stateful across chunk boundaries.
    Expected keys in *state*:

    - ``in_title``: currently inside ``ESC k ... ST``
    - ``pending_esc``: previous chunk ended with ``ESC`` byte
    """
    result = bytearray()
    in_title = False
    pending_esc = False

    if state is not None:
        in_title = bool(state.get("in_title", False))
        pending_esc = bool(state.get("pending_esc", False))

    for b in data:
        if in_title:
            if pending_esc:
                if b == 0x5C:  # ST terminator: ESC \\
                    in_title = False
                    pending_esc = False
                elif b == 0x1B:
                    # Stay in pending ESC state inside title.
                    pending_esc = True
                else:
                    pending_esc = False
                continue

            if b == 0x1B:
                pending_esc = True
            continue

        # Normal mode
        if pending_esc:
            if b == ord("k"):
                # Enter ESC k title payload mode and drop both bytes.
                in_title = True
                pending_esc = False
                continue

            # Previous ESC was not part of ESC k sequence; emit it.
            result.append(0x1B)
            pending_esc = False

        if b == 0x1B:
            pending_esc = True
        else:
            result.append(b)

    # In stateless mode, preserve trailing lone ESC exactly as input.
    if state is None and pending_esc:
        result.append(0x1B)

    if state is not None:
        state["in_title"] = in_title
        state["pending_esc"] = pending_esc

    return bytes(result)


def cleanup_stale_control_clients() -> int:
    """Kill orphaned tmux control-mode clients from previous server runs.

    When the orchestrator server restarts without cleanly stopping its
    ``tmux -C attach-session`` subprocess, the child process gets
    reparented to PID 1 (launchd/init).  These zombies accumulate and
    can degrade tmux performance.

    Returns the number of stale clients killed.
    """
    import subprocess

    our_pid = os.getpid()
    killed = 0
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,ppid,command"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            if "tmux -C attach-session" not in line:
                continue
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            pid, ppid = int(parts[0]), int(parts[1])
            if pid == our_pid:
                continue
            # Only kill clients whose parent is init (ppid=1) — orphaned
            if ppid == 1:
                try:
                    os.kill(pid, 15)  # SIGTERM
                    killed += 1
                    logger.info(
                        "Killed stale tmux control client pid=%d (orphaned, ppid=1)",
                        pid,
                    )
                except ProcessLookupError:
                    pass
                except PermissionError:
                    logger.debug("Cannot kill pid=%d (permission denied)", pid)
    except Exception:
        logger.debug("cleanup_stale_control_clients failed", exc_info=True)
    if killed:
        logger.info("Cleaned up %d stale tmux control-mode clients", killed)
    return killed


async def check_alternate_screen_async(session: str, window: str) -> bool:
    """Return True if the pane is currently in alternate screen buffer mode."""
    target = f"{session}:{window}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "display-message",
            "-p",
            "-t",
            target,
            "#{alternate_on}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip() == "1"
    except Exception:
        return False


async def get_pane_id_async(session: str, window: str) -> str | None:
    """Resolve a tmux window to its pane ID (e.g. ``%5``).

    Runs ``tmux list-panes`` once — not called per-message.
    """
    target = f"{session}:{window}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "list-panes",
            "-t",
            target,
            "-F",
            "#{pane_id}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        # Take the first pane (active pane) if multiple exist
        pane_id = stdout.decode().strip().split("\n")[0]
        return pane_id if pane_id else None
    except Exception as e:
        logger.error("Failed to resolve pane ID for %s: %s", target, e)
        return None


class TmuxControlConnection:
    """Persistent tmux control mode connection for a tmux **session**.

    One connection serves all windows/panes in the session.  Used for
    sending keys and resizing windows via control mode commands.
    """

    def __init__(self, session: str):
        self.session = session
        self._process: asyncio.subprocess.Process | None = None
        self._drain_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> bool:
        """Start the control mode connection."""
        if self._process is not None:
            return True

        try:
            import shutil

            tmux_path = shutil.which("tmux")
            logger.info(
                "Starting tmux control mode for session %s (tmux=%s)",
                self.session,
                tmux_path,
            )
            self._process = await asyncio.create_subprocess_exec(
                "tmux",
                "-C",
                "attach-session",
                "-t",
                self.session,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._running = True
            logger.info(
                "Started tmux control mode for session %s (pid=%s)",
                self.session,
                self._process.pid,
            )

            # Drain stdout so the pipe buffer never fills up and blocks
            # the tmux process.  We don't parse the output — pipe-pane
            # handles streaming — but we must keep reading.
            self._drain_task = asyncio.create_task(self._drain_stdout())
            return True

        except Exception as e:
            logger.error("Failed to start tmux control mode: %s", e)
            return False

    async def stop(self):
        """Stop the control mode connection."""
        self._running = False

        if self._drain_task:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
            self._drain_task = None

        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2.0)
            except TimeoutError:
                self._process.kill()
            self._process = None

        logger.info("Stopped tmux control mode for session %s", self.session)

    async def _drain_stdout(self):
        """Read and discard control-mode stdout to prevent pipe backpressure.

        tmux control mode continuously writes notifications (``%output``,
        ``%begin``, ``%end``, etc.) to stdout.  If nobody reads them, the
        pipe buffer fills up (~64KB) and the tmux process blocks — making
        stdin commands (send_keys, resize) hang too.
        """
        if not self._process or not self._process.stdout:
            return
        try:
            while self._running:
                data = await self._process.stdout.read(8192)
                if not data:
                    break  # EOF — process exited
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("stdout drain ended for session %s: %s", self.session, e)

    # -- command helpers --------------------------------------------------------

    @property
    def is_alive(self) -> bool:
        """Check if the control mode connection is still usable."""
        if not self._process or not self._process.stdin:
            return False
        transport = self._process.stdin.transport  # type: ignore[union-attr]
        if transport is not None and transport.is_closing():
            return False
        return self._process.returncode is None

    async def send_keys(self, target: str, keys: str) -> bool:
        """Send keys to *target* (e.g. ``session:window``) via control mode."""
        if not self.is_alive:
            logger.warning(
                "send_keys: control connection not alive for session %s "
                "(process=%s, returncode=%s)",
                self.session,
                self._process is not None,
                self._process.returncode if self._process else "N/A",
            )
            return False

        try:
            key_bytes = keys.encode("utf-8")
            hex_keys = " ".join(f"{b:02x}" for b in key_bytes)
            cmd = f"send-keys -H -t {target} {hex_keys}\n"
            self._process.stdin.write(cmd.encode())
            await self._process.stdin.drain()
            return True
        except Exception as e:
            logger.error("Failed to send keys via control mode: %s", e)
            return False

    async def resize(self, target: str, cols: int, rows: int) -> bool:
        """Resize *target* window via control mode."""
        if not self.is_alive:
            return False

        try:
            cmd = f"resize-window -t {target} -x {cols} -y {rows}\n"
            self._process.stdin.write(cmd.encode())
            await self._process.stdin.drain()
            return True
        except Exception as e:
            logger.error("Failed to resize via control mode: %s", e)
            return False


class TmuxControlPool:
    """Pool of control mode connections, keyed by **session** name.

    One control-mode process serves all windows in a tmux session.
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

    async def get_connection(self, session: str) -> TmuxControlConnection:
        """Get or create a control connection for *session*.

        Automatically replaces dead connections with fresh ones.
        """
        async with self._lock:
            existing = self._connections.get(session)
            if existing and existing.is_alive:
                return existing
            if existing:
                logger.warning("Replacing dead control connection for session %s", session)
                await existing.stop()
            conn = TmuxControlConnection(session)
            await conn.start()
            self._connections[session] = conn
            return conn

    async def release_connection(self, session: str):
        """Release a connection (currently keeps it alive for reuse)."""
        pass

    async def close_all(self):
        """Close all connections."""
        async with self._lock:
            for conn in self._connections.values():
                await conn.stop()
            self._connections.clear()


async def send_keys_async(session: str, window: str, keys: str) -> bool:
    """Send keys using control mode pool (async version).

    Retries once with a fresh connection on failure.
    """
    import time as _time

    t0 = _time.monotonic()
    pool = TmuxControlPool.get_instance()
    target = f"{session}:{window}"
    conn = await pool.get_connection(session)
    t1 = _time.monotonic()
    if await conn.send_keys(target, keys):
        elapsed_ms = (_time.monotonic() - t0) * 1000
        if elapsed_ms > 50:
            conn_ms = (t1 - t0) * 1000
            logger.warning(
                "send_keys_async[%s] took %.0fms (get_conn=%.0fms)",
                target,
                elapsed_ms,
                conn_ms,
            )
        return True
    # First attempt failed — get_connection will replace the dead conn
    logger.warning("send_keys first attempt failed for %s, retrying with fresh connection", target)
    conn = await pool.get_connection(session)
    result = await conn.send_keys(target, keys)
    if not result:
        logger.error("send_keys retry also failed for %s", target)
    return result


async def resize_async(session: str, window: str, cols: int, rows: int) -> bool:
    """Resize pane using control mode pool (async version).

    Retries once with a fresh connection on failure.
    """
    pool = TmuxControlPool.get_instance()
    target = f"{session}:{window}"
    conn = await pool.get_connection(session)
    if await conn.resize(target, cols, rows):
        return True
    conn = await pool.get_connection(session)
    return await conn.resize(target, cols, rows)


async def capture_pane_async(session: str, window: str) -> str:
    """Capture pane content asynchronously using subprocess.

    Note: tmux control mode doesn't support capture-pane output directly,
    so we use asyncio subprocess for non-blocking capture.

    Strips trailing blank lines to avoid cursor positioning issues.
    """
    target = f"{session}:{window}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "capture-pane",
            "-p",
            "-e",
            "-t",
            target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode("utf-8", errors="replace")
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
            "tmux",
            "display-message",
            "-p",
            "-t",
            target,
            "#{cursor_x} #{cursor_y}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        parts = stdout.decode("utf-8", errors="replace").strip().split()
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except Exception as e:
        logger.error("Failed to get cursor position: %s", e)
    return 0, 0


async def capture_pane_with_cursor_async(session: str, window: str) -> tuple[str, int, int]:
    """Capture pane content and cursor position together.

    Returns (content, cursor_x, cursor_y).
    Runs both captures concurrently for speed (~25ms vs ~45ms sequential).

    Note: This has a race condition - content and cursor are captured at slightly
    different times. Use capture_pane_with_cursor_atomic_async for correctness.
    """
    # Run both captures concurrently
    content_task = capture_pane_async(session, window)
    cursor_task = get_cursor_position_async(session, window)

    content, (cursor_x, cursor_y) = await asyncio.gather(content_task, cursor_task)
    return content, cursor_x, cursor_y


async def capture_pane_with_cursor_atomic_async(session: str, window: str) -> tuple[str, int, int]:
    """Capture pane content AND cursor position atomically in a single subprocess.

    Returns (content, cursor_x, cursor_y).

    This eliminates the race condition in capture_pane_with_cursor_async where
    content and cursor could be captured at different times, causing cursor drift.
    """
    target = f"{session}:{window}"
    try:
        # Single shell invocation that captures both content and cursor atomically
        # Uses a separator that won't appear in terminal output
        # Only echo separator if capture-pane succeeds (using &&)
        proc = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            f"tmux capture-pane -p -e -t {target} && "
            f'echo "===CURSOR_POSITION===" && '
            f'tmux display-message -p -t {target} "#{{cursor_x}} #{{cursor_y}}"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        # If tmux command failed, return empty
        if proc.returncode != 0:
            return "", 0, 0

        output = stdout.decode("utf-8", errors="replace")

        # Parse the combined output
        if "===CURSOR_POSITION===" in output:
            content, cursor_line = output.rsplit("===CURSOR_POSITION===\n", 1)
            parts = cursor_line.strip().split()
            if len(parts) == 2:
                cursor_x, cursor_y = int(parts[0]), int(parts[1])
                return content, cursor_x, cursor_y

        # Fallback if parsing fails
        return "", 0, 0

    except Exception as e:
        logger.error("Failed atomic capture: %s", e)
        return "", 0, 0


async def capture_pane_with_history_async(
    session: str, window: str, scrollback_lines: int = 1000
) -> tuple[str, int, int, int]:
    """Capture pane content with scrollback history and cursor position atomically.

    Returns (content, cursor_x, cursor_y, total_lines).

    Args:
        session: tmux session name
        window: tmux window name
        scrollback_lines: Number of scrollback lines to capture (default 1000)
    """
    target = f"{session}:{window}"
    try:
        # Capture content with scrollback, plus cursor position and history size
        # Only echo separator if capture-pane succeeds (using &&)
        proc = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            f"tmux capture-pane -p -e -t {target} -S -{scrollback_lines} && "
            f'echo "===CURSOR_POSITION===" && '
            f'tmux display-message -p -t {target} "#{{cursor_x}} #{{cursor_y}} #{{history_size}}"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        # If tmux command failed, return empty
        if proc.returncode != 0:
            return "", 0, 0, 0

        output = stdout.decode("utf-8", errors="replace")

        # Parse the combined output
        if "===CURSOR_POSITION===" in output:
            content, cursor_line = output.rsplit("===CURSOR_POSITION===\n", 1)
            parts = cursor_line.strip().split()
            if len(parts) >= 2:
                cursor_x = int(parts[0])
                cursor_y = int(parts[1])
                total_lines = content.count("\n") + 1
                return content, cursor_x, cursor_y, total_lines

        # Fallback if parsing fails
        return "", 0, 0, 0

    except Exception as e:
        logger.error("Failed history capture: %s", e)
        return "", 0, 0, 0
