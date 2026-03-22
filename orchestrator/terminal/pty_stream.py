"""Direct PTY streaming via tmux pipe-pane.

Provides raw PTY bytes via ``pipe-pane -O`` as a continuous stream — no
octal encoding, no line-level fragmentation.  This is the only output
streaming mechanism; the ``%output`` control-mode fallback has been removed.

Architecture::

    App → PTY → tmux → pipe-pane -O (raw bytes)
      → FIFO → PtyStreamReader → callback(bytes) → WebSocket → xterm.js

Key classes:

* ``PtyStreamReader`` — reads raw bytes from one pane via a FIFO
* ``PtyStreamPool``   — shared reader per pane with subscriber fan-out

See docs/008-direct-pty-streaming.md for full design.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path

from orchestrator.terminal.control import _strip_tmux_sequences

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FIFO_DIR = "/tmp/orchestrator_pty"
STARTUP_TIMEOUT = 3.0  # seconds to wait for first byte from pipe-pane
EAGER_RESTART_DELAY = 0.5  # seconds before attempting eager restart after EOF
ZOMBIE_TIMEOUT = 30.0  # seconds without data before reader is considered zombie

# ---------------------------------------------------------------------------
# tmux version detection
# ---------------------------------------------------------------------------

_tmux_version: tuple[int, int] | None = None  # cached (major, minor)


async def get_tmux_version() -> tuple[int, int]:
    """Detect and cache the tmux version.

    Parses ``tmux -V`` output (e.g. ``tmux 3.4`` or ``tmux next-3.5``).
    Returns ``(major, minor)`` tuple.  Falls back to ``(0, 0)`` on error.
    """
    global _tmux_version
    if _tmux_version is not None:
        return _tmux_version

    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "-V",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        version_str = stdout.decode("utf-8", errors="replace").strip()
        _tmux_version = _parse_tmux_version(version_str)
    except Exception as e:
        logger.warning("Failed to detect tmux version: %s", e)
        _tmux_version = (0, 0)

    logger.info("Detected tmux version: %d.%d", *_tmux_version)
    return _tmux_version


def _parse_tmux_version(version_str: str) -> tuple[int, int]:
    """Parse a tmux version string into ``(major, minor)``.

    Handles: ``tmux 3.4``, ``tmux 3.3a``, ``tmux next-3.5``,
    ``tmux master`` (→ 999.0).
    """
    if "master" in version_str or "next" in version_str:
        # Development build — assume latest features
        m = re.search(r"(\d+)\.(\d+)", version_str)
        if m:
            return int(m.group(1)), int(m.group(2))
        return (999, 0)

    m = re.search(r"(\d+)\.(\d+)", version_str)
    if m:
        return int(m.group(1)), int(m.group(2))

    return (0, 0)


def reset_tmux_version_cache() -> None:
    """Reset the cached tmux version (for testing)."""
    global _tmux_version
    _tmux_version = None


def set_tmux_version_cache(major: int, minor: int) -> None:
    """Manually set the cached tmux version (for testing)."""
    global _tmux_version
    _tmux_version = (major, minor)


# ---------------------------------------------------------------------------
# PtyStreamReader
# ---------------------------------------------------------------------------


class _FifoReadProtocol(asyncio.Protocol):
    """asyncio Protocol for reading from a FIFO file descriptor."""

    def __init__(self, reader: PtyStreamReader):
        self._reader = reader
        self.transport: asyncio.BaseTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport

    def data_received(self, data: bytes) -> None:
        # Schedule the async callback without blocking the protocol
        asyncio.ensure_future(self._reader._on_data(data))

    def connection_lost(self, exc: Exception | None) -> None:
        asyncio.ensure_future(self._reader._on_eof())


class PtyStreamReader:
    """Read raw PTY bytes from a tmux pane via ``pipe-pane -O``.

    Lifecycle:

    1. ``start()`` — creates FIFO, starts pipe-pane, opens read end
    2. Invokes the registered callback with raw bytes as they arrive
    3. ``stop()`` — closes pipe-pane, removes FIFO
    """

    def __init__(self, session: str, window: str, pane_id: str):
        self.session = session
        self.window = window
        self.pane_id = pane_id
        # Sanitize pane_id for filesystem use: "%5" → "5"
        self._safe_pane_id = pane_id.lstrip("%")
        self._fifo_path: str | None = None
        self._transport: asyncio.BaseTransport | None = None
        self._fd: int | None = None
        self._running = False
        self._eof = False
        self._callback: Callable[[bytes], Awaitable[None]] | None = None
        self._eof_callback: Callable[[], Awaitable[None]] | None = None
        self._startup_timer: asyncio.TimerHandle | None = None
        self._got_first_byte = False
        self._last_data_time: float = 0.0  # 0 = pre-start (grace period)
        # Stateful ESC k stripping (reuse existing logic)
        self._strip_state: dict[str, bool] = {
            "in_title": False,
            "pending_esc": False,
        }

    @property
    def is_alive(self) -> bool:
        """True if reader is running and has not received EOF."""
        return self._running and not self._eof

    def is_stale(self, timeout: float = ZOMBIE_TIMEOUT) -> bool:
        """True if reader is running but has received no data for *timeout* seconds.

        Returns False if the reader is not running, already EOF, or hasn't
        been started yet (``_last_data_time == 0``).
        """
        if not self._running or self._eof or self._last_data_time == 0.0:
            return False
        now = asyncio.get_running_loop().time()
        return (now - self._last_data_time) > timeout

    async def start(
        self,
        callback: Callable[[bytes], Awaitable[None]],
        eof_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> bool:
        """Start streaming.  Calls ``callback(raw_bytes)`` for each chunk.

        Returns ``True`` if pipe-pane started successfully, ``False`` on
        failure (tmux version too old, pane doesn't exist, etc.).
        """
        # Check tmux version
        version = await get_tmux_version()
        if version < (2, 6):
            logger.warning("tmux %d.%d < 2.6 — pipe-pane -O not supported", *version)
            return False

        self._callback = callback
        self._eof_callback = eof_callback

        # Ensure FIFO directory exists with restricted permissions
        try:
            os.makedirs(FIFO_DIR, mode=0o700, exist_ok=True)
        except OSError as e:
            logger.error("Cannot create FIFO directory %s: %s", FIFO_DIR, e)
            return False

        # Build FIFO path with sanitized pane ID and PID
        fifo_name = f"{self._safe_pane_id}_{os.getpid()}.fifo"
        self._fifo_path = os.path.join(FIFO_DIR, fifo_name)

        # Remove stale FIFO if it exists
        try:
            os.unlink(self._fifo_path)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning("Failed to unlink stale FIFO %s: %s", self._fifo_path, e)

        # Create FIFO with restricted permissions
        try:
            os.mkfifo(self._fifo_path, 0o600)
        except OSError as e:
            logger.error("Failed to create FIFO %s: %s", self._fifo_path, e)
            return False

        logger.info("Created FIFO %s for pane %s", self._fifo_path, self.pane_id)

        # Start pipe-pane -O
        target = f"{self.session}:{self.window}"
        cmd_str = f"exec cat > {self._fifo_path}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux",
                "pipe-pane",
                "-O",
                "-t",
                target,
                cmd_str,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                logger.error(
                    "pipe-pane failed for %s (rc=%d): %s",
                    target,
                    proc.returncode,
                    err_msg,
                )
                self._cleanup_fifo()
                return False
        except Exception as e:
            logger.error("Failed to start pipe-pane for %s: %s", target, e)
            self._cleanup_fifo()
            return False

        logger.info("Started pipe-pane -O for pane %s", self.pane_id)

        # Open FIFO with O_RDWR | O_NONBLOCK to avoid the macOS race where
        # O_RDONLY returns immediate EOF if the writer (cat) hasn't opened yet.
        # O_RDWR keeps the fd as both reader and writer, preventing premature
        # EOF.  Data from pipe-pane's `cat > FIFO` still arrives normally.
        try:
            self._fd = os.open(self._fifo_path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as e:
            logger.error("Failed to open FIFO %s: %s", self._fifo_path, e)
            await self._stop_pipe_pane()
            self._cleanup_fifo()
            return False

        # Register with asyncio event loop for async reads
        try:
            loop = asyncio.get_running_loop()
            transport, _ = await loop.connect_read_pipe(
                lambda: _FifoReadProtocol(self),
                os.fdopen(self._fd, "rb", 0),
            )
            self._transport = transport
            # fd is now owned by the transport; don't close it separately
            self._fd = None
        except Exception as e:
            logger.error("Failed to register FIFO reader: %s", e)
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            await self._stop_pipe_pane()
            self._cleanup_fifo()
            return False

        self._running = True
        self._got_first_byte = False
        self._last_data_time = asyncio.get_running_loop().time()

        # Start startup timeout — if no data within STARTUP_TIMEOUT, log warning
        loop = asyncio.get_running_loop()
        self._startup_timer = loop.call_later(STARTUP_TIMEOUT, self._on_startup_timeout)

        return True

    async def stop(self) -> None:
        """Stop streaming and clean up."""
        if not self._running:
            return
        self._running = False

        # Cancel startup timer
        if self._startup_timer:
            self._startup_timer.cancel()
            self._startup_timer = None

        # Close the transport (which closes the fd)
        if self._transport:
            self._transport.close()
            self._transport = None

        # Close fd if transport was never set up
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

        # Stop pipe-pane
        await self._stop_pipe_pane()

        # Remove FIFO
        self._cleanup_fifo()

        logger.info("Stopped PtyStreamReader for pane %s", self.pane_id)

    async def _on_data(self, data: bytes) -> None:
        """Handle incoming raw bytes from the FIFO."""
        if not self._running:
            return

        self._last_data_time = asyncio.get_running_loop().time()

        if not self._got_first_byte:
            self._got_first_byte = True
            if self._startup_timer:
                self._startup_timer.cancel()
                self._startup_timer = None

        # Strip tmux ESC k title sequences
        cleaned = _strip_tmux_sequences(data, self._strip_state)
        if not cleaned:
            return

        if self._callback:
            try:
                await self._callback(cleaned)
            except Exception:
                logger.exception(
                    "Error in PtyStreamReader callback for pane %s",
                    self.pane_id,
                )

    async def _on_eof(self) -> None:
        """Handle EOF on the FIFO (pipe-pane stopped, pane destroyed, etc.)."""
        if self._eof:
            return
        self._eof = True
        logger.info(
            "PtyStreamReader EOF for pane %s (pipe-pane stopped or pane destroyed)",
            self.pane_id,
        )
        if self._eof_callback:
            try:
                await self._eof_callback()
            except Exception:
                logger.exception(
                    "Error in PtyStreamReader EOF callback for pane %s",
                    self.pane_id,
                )

    def _on_startup_timeout(self) -> None:
        """Called if no data arrives within STARTUP_TIMEOUT seconds."""
        if not self._got_first_byte and self._running:
            logger.warning(
                "PtyStreamReader: no data within %.1fs for pane %s "
                "(pipe-pane may have failed to connect)",
                STARTUP_TIMEOUT,
                self.pane_id,
            )

    async def _stop_pipe_pane(self) -> None:
        """Tell tmux to stop piping for this pane."""
        target = f"{self.session}:{self.window}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux",
                "pipe-pane",
                "-t",
                target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except Exception as e:
            logger.debug("Failed to stop pipe-pane for %s: %s", target, e)

    def _cleanup_fifo(self) -> None:
        """Remove the FIFO from disk."""
        if self._fifo_path:
            try:
                os.unlink(self._fifo_path)
                logger.debug("Removed FIFO %s", self._fifo_path)
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.warning("Failed to remove FIFO %s: %s", self._fifo_path, e)
            self._fifo_path = None


# ---------------------------------------------------------------------------
# PtyStreamPool
# ---------------------------------------------------------------------------


class PtyStreamPool:
    """One ``PtyStreamReader`` per pane, multiple consumers fan out.

    Thread-safe via ``asyncio.Lock`` — all public methods acquire the lock
    before mutating ``_readers`` / ``_consumers``.
    """

    _instance: PtyStreamPool | None = None

    def __init__(self):
        self._readers: dict[str, PtyStreamReader] = {}
        self._consumers: dict[str, set[Callable]] = {}
        self._lock = asyncio.Lock()
        self._cleanup_stale_fifos()

    @classmethod
    def get_instance(cls) -> PtyStreamPool:
        """Return the singleton pool instance."""
        if cls._instance is None:
            cls._instance = PtyStreamPool()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        cls._instance = None

    async def subscribe(
        self,
        pane_id: str,
        session: str,
        window: str,
        callback: Callable[[bytes], Awaitable[None]],
    ) -> bool:
        """Subscribe to raw PTY bytes for *pane_id*.

        Starts a new ``PtyStreamReader`` on the first subscriber.
        Returns ``True`` on success, ``False`` if pipe-pane could not start
        (caller should fall back to ``%output``).
        """
        async with self._lock:
            reader = self._readers.get(pane_id)
            if reader and reader.is_alive:
                if reader.is_stale():
                    logger.warning(
                        "PtyStreamReader for pane %s is zombie "
                        "(alive but no data for %.0fs) — restarting",
                        pane_id,
                        ZOMBIE_TIMEOUT,
                    )
                    await reader.stop()
                    del self._readers[pane_id]
                    reader = None  # fall through to create new reader
                else:
                    self._consumers.setdefault(pane_id, set()).add(callback)
                    return True

            # Need a new reader (first subscriber or previous reader died)
            if reader:
                # Clean up dead reader
                await reader.stop()
                del self._readers[pane_id]

            reader = PtyStreamReader(session, window, pane_id)

            async def on_eof():
                await self._on_reader_eof(pane_id)

            started = await reader.start(
                callback=lambda data: self._dispatch(pane_id, data),
                eof_callback=on_eof,
            )
            if not started:
                return False

            self._readers[pane_id] = reader
            self._consumers.setdefault(pane_id, set()).add(callback)
            return True

    async def unsubscribe(
        self,
        pane_id: str,
        callback: Callable[[bytes], Awaitable[None]],
    ) -> None:
        """Remove *callback* from *pane_id* notifications.

        Stops the reader when the last subscriber leaves.
        """
        async with self._lock:
            subs = self._consumers.get(pane_id)
            if not subs:
                return
            subs.discard(callback)
            if not subs:
                reader = self._readers.pop(pane_id, None)
                if reader:
                    await reader.stop()
                del self._consumers[pane_id]

    async def _dispatch(self, pane_id: str, data: bytes) -> None:
        """Fan out data to all subscribers without blocking on slow ones.

        Optimized fast path: when there's exactly one subscriber (the common
        case — one WebSocket per pane), call it directly without acquiring the
        lock or creating a task.  This eliminates ~0.5ms of overhead per chunk.
        """
        # Fast path: read without lock for the common single-subscriber case.
        # The consumers dict is only mutated under _lock during subscribe/
        # unsubscribe (rare), so this read is safe for dispatch.
        subs = self._consumers.get(pane_id)
        if not subs:
            return
        if len(subs) == 1:
            cb = next(iter(subs))
            try:
                await cb(data)
            except Exception:
                logger.exception("Error in PtyStreamPool subscriber for pane %s", pane_id)
            return
        # Multiple subscribers: snapshot under lock, fan out via tasks
        async with self._lock:
            subs_copy = set(self._consumers.get(pane_id, ()))
        for cb in subs_copy:
            asyncio.create_task(self._safe_callback(cb, data, pane_id))

    @staticmethod
    async def _safe_callback(
        cb: Callable[[bytes], Awaitable[None]],
        data: bytes,
        pane_id: str,
    ) -> None:
        """Invoke a subscriber callback with error handling."""
        try:
            await cb(data)
        except Exception:
            logger.exception("Error in PtyStreamPool subscriber for pane %s", pane_id)

    async def _on_reader_eof(self, pane_id: str) -> None:
        """Called when a PtyStreamReader receives EOF.

        Removes the dead reader so drift correction can re-subscribe
        with a fresh one.  Attempts eager restart if consumers remain.
        """
        async with self._lock:
            reader = self._readers.pop(pane_id, None)
            if reader:
                await reader.stop()
            has_consumers = bool(self._consumers.get(pane_id))

        if has_consumers:
            # Attempt eager restart after a brief delay
            await asyncio.sleep(EAGER_RESTART_DELAY)
            async with self._lock:
                if pane_id in self._readers:
                    return  # Already restarted by drift correction
                subs = self._consumers.get(pane_id)
                if not subs:
                    return

                # Need session/window info from the old reader
                if reader:
                    new_reader = PtyStreamReader(reader.session, reader.window, pane_id)

                    async def on_eof():
                        await self._on_reader_eof(pane_id)

                    started = await new_reader.start(
                        callback=lambda data: self._dispatch(pane_id, data),
                        eof_callback=on_eof,
                    )
                    if started:
                        self._readers[pane_id] = new_reader
                        logger.info("Eager restart succeeded for pane %s", pane_id)
                    else:
                        logger.warning(
                            "Eager restart failed for pane %s — drift correction will retry",
                            pane_id,
                        )

    async def stop_all(self) -> None:
        """Stop all readers and clear state."""
        async with self._lock:
            for reader in self._readers.values():
                await reader.stop()
            self._readers.clear()
            self._consumers.clear()

    def _cleanup_stale_fifos(self) -> None:
        """Remove stale FIFOs and regular files from previous server crashes.

        Orphan ``cat`` processes can create regular files when the FIFO
        doesn't exist yet, so we clean up both FIFOs and regular files.
        """
        fifo_dir = Path(FIFO_DIR)
        if not fifo_dir.exists():
            return
        pid = os.getpid()
        for entry in fifo_dir.iterdir():
            if not entry.name.endswith(".fifo"):
                continue
            mode = entry.stat().st_mode
            if stat.S_ISFIFO(mode) or stat.S_ISREG(mode):
                # Only remove entries not owned by our PID
                # (format: <pane_id>_<pid>.fifo)
                parts = entry.stem.rsplit("_", 1)
                if len(parts) == 2:
                    try:
                        fifo_pid = int(parts[1])
                        if fifo_pid == pid:
                            continue  # Our own, keep it
                    except ValueError:
                        pass
                try:
                    entry.unlink()
                    logger.debug("Cleaned up stale FIFO/file: %s", entry)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Orphan process cleanup
# ---------------------------------------------------------------------------


def cleanup_orphaned_pipe_pane_processes() -> int:
    """Kill orphaned ``cat`` processes from previous pipe-pane sessions.

    When the orchestrator server restarts without cleanly stopping its
    pipe-pane ``cat > FIFO`` processes, these ``sh -c exec cat > FIFO``
    children linger — parented to tmux (not PID 1) since tmux spawned
    them via ``pipe-pane``.  They accumulate across restarts and can
    number in the thousands.

    Strategy: kill any ``cat > orchestrator_pty/*.fifo`` process whose
    FIFO PID suffix doesn't match *our* PID.  This covers both
    reparented (ppid=1) and tmux-parented orphans from previous servers.

    Returns the number of orphaned processes killed.
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
            if "orchestrator_pty" not in line or "cat" not in line:
                continue
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            pid = int(parts[0])
            if pid == our_pid:
                continue
            # Extract the PID encoded in the FIFO filename (<pane>_<pid>.fifo)
            # to distinguish our own cat processes from stale ones.
            cmd = parts[2]
            fifo_match = re.search(r"orchestrator_pty/\d+_(\d+)\.fifo", cmd)
            if fifo_match:
                fifo_pid = int(fifo_match.group(1))
                if fifo_pid == our_pid:
                    continue  # Belongs to this server instance
            try:
                os.kill(pid, 15)  # SIGTERM
                killed += 1
                logger.info(
                    "Killed orphaned pipe-pane cat process pid=%d",
                    pid,
                )
            except ProcessLookupError:
                pass
    except Exception:
        logger.exception("Failed to clean up orphaned pipe-pane processes")
    if killed:
        logger.info("Cleaned up %d orphaned pipe-pane cat process(es)", killed)
    return killed
