---
title: "Direct PTY Streaming — Solving Terminal Rendering Once and For All"
author: Yudong Qiu
created: 2026-02-16
last_modified: 2026-02-17
status: Implemented
---

# Direct PTY Streaming — Solving Terminal Rendering Once and For All

> **Goal**: Eliminate terminal rendering corruption during Claude Code TUI sessions

## 1. Problem Summary

Claude Code uses Ink (React for CLI) to render a TUI with cursor repositioning,
box-drawing characters, animated spinners, and scrollback-clearing redraws. Our
current terminal streaming pipeline introduces **frame tearing** — the browser's
xterm.js renders partially-written frames, producing garbled box-drawing lines,
fragmented progress indicators, and corrupted UI chrome.

### Root cause

The pipeline currently uses tmux control mode (`tmux -C`) for output streaming.
Control mode delivers pane output as `%output` text notifications — one per
newline in the control mode stdout. This introduces two fatal fragmentation
points:

1. **Line-level fragmentation**: A single TUI frame (cursor moves + draws) is
   split across multiple `%output` lines. Each line is dispatched independently.
2. **Batch-window fragmentation**: The 16ms stream batching (`stream_flusher`)
   can split a frame across two WebSocket sends if `%output` lines straddle the
   batch boundary.

The result: xterm.js renders mid-frame state → visible tearing.

### Previous fixes and why they weren't enough

| Fix | What it solved | What remains |
|-----|---------------|-------------|
| Stateful ESC k stripping | Title sequence bytes leaking as garbage | Core tearing |
| Deferred subscription | Startup gap between history and stream | Steady-state tearing |
| 16ms batching | Reduced frame count, improved throughput | Still splits TUI frames |
| Snapshot recovery (256KB) | Buffer overflow on slow clients | Doesn't prevent tearing |
| Drift correction sync | Ground-truth recovery every 2s | Disruptive full-screen redraw |

---

## 2. Solution: Direct PTY Streaming via `pipe-pane`

Replace the `%output` control mode subscription with `tmux pipe-pane -O`, which
provides a **raw byte stream** — the exact bytes the application writes to the
PTY, delivered as a continuous stream without line-level fragmentation or octal
encoding.

### Architecture comparison

**Current (control mode):**
```
App → PTY → tmux → control mode (%output, octal-encoded, line-by-line)
  → Python (decode octal + strip ESC k) → 16ms batch → WebSocket → xterm.js
```

**Proposed (pipe-pane):**
```
App → PTY → tmux → pipe-pane -O (raw bytes, continuous stream)
  → Python (strip ESC k + optional 2026 batching) → WebSocket → xterm.js
```

### Why this works

- **No octal encoding/decoding**: `pipe-pane -O` delivers raw binary bytes.
  No `_unescape_tmux_output()` needed.
- **No line-level fragmentation**: Output arrives as kernel-buffered chunks
  (typically 4KB), not one `%output` per tmux line. A complete TUI frame
  is far more likely to arrive in a single chunk.
- **DEC 2026 sequences pass through**: If Claude Code emits synchronized output
  markers (`CSI ? 2026 h` / `CSI ? 2026 l`), they appear in the raw stream.
  We can detect them server-side and hold bytes until the frame is complete,
  then flush atomically.
- **Same bytes as `%output`**: `pipe-pane -O` gives the same raw application
  output that `%output` delivered (just without encoding and fragmentation).

### tmux version requirements

| Feature | Minimum version | Notes |
|---------|----------------|-------|
| `pipe-pane -O` (output-only) | **tmux 2.6** | Core requirement |
| `refresh-client -f no-output` | **tmux 3.2** | Optional optimization (section 6.8) |
| DEC 2026 passthrough | **tmux 3.3** | Needed for synchronized output |

**Minimum supported**: tmux 2.6. **Recommended**: tmux 3.3+.

The server should detect the tmux version at startup (`tmux -V`) and:
- **< 2.6**: Do not use pipe-pane. Fall back to `%output` control mode (existing path).
- **2.6–3.1**: Use pipe-pane for streaming. Skip `refresh-client -f no-output`.
- **≥ 3.2**: Full feature set including `%output` suppression.

Version detection is a one-time check cached for the process lifetime.

---

## 3. Key Guarantees

### 3.1 tmux remains fully independent

**tmux is NOT replaced.** It still owns and manages all terminal sessions.
`pipe-pane` is a **read-only tap** — it copies output to a consumer without
affecting tmux's own rendering or state.

| Scenario | Behavior |
|----------|----------|
| Server killed | tmux sessions continue running. `pipe-pane`'s consumer process exits, pipe closes. Claude Code keeps running unaffected. User can `tmux attach -t orchestrator` and interact normally. |
| Server not running | tmux sessions are fully interactive via `tmux attach`. No pipe-pane active, no overhead. |
| User attaches directly | `tmux attach` works alongside pipe-pane. Both see the same output. No conflict. |
| Multiple windows | Each window gets its own pipe-pane tap. Independent lifecycle. |

**Bottom line: tmux is the session manager. The server is just an observer.**

### 3.2 Auto-reconnect on server restart

The existing WebSocket reconnection flow handles this seamlessly:

1. Server restarts → WebSocket endpoint becomes available
2. Frontend detects disconnect → starts backoff reconnect (1s, 2s, 5s, 10s)
3. Frontend reconnects → sends `resize` message
4. Backend receives resize → captures history snapshot via `capture-pane`
5. Backend sends history to frontend (ground-truth state)
6. Backend starts new `pipe-pane -O` for the pane → streaming resumes
7. Frontend is now fully synced with zero data loss

**No special reconnect logic needed.** The pipe-pane is established fresh on
each WebSocket connection. Old pipes are cleaned up automatically (the consumer
process exits when the FIFO/socket closes).

---

## 4. Detailed Design

### 4.1 New class: `PtyStreamReader`

Location: `orchestrator/terminal/pty_stream.py` (new file)

```python
class PtyStreamReader:
    """Read raw PTY bytes from a tmux pane via pipe-pane -O.

    Lifecycle:
    1. start() — creates FIFO, starts pipe-pane, opens read end
    2. Calls the registered callback with raw bytes as they arrive
    3. stop() — closes pipe-pane, removes FIFO
    """

    def __init__(self, session: str, window: str, pane_id: str):
        self.session = session
        self.window = window
        self.pane_id = pane_id
        # Sanitize pane_id for filesystem use: "%5" → "5"
        self._safe_pane_id = pane_id.lstrip("%")
        self._fifo_path: str | None = None
        self._transport = None
        self._running = False
        self._eof = False  # Set True when reader gets EOF
        # Stateful ESC k stripping (reuse existing logic)
        self._strip_state: dict[str, bool] = {
            "in_title": False,
            "pending_esc": False,
        }

    async def start(self, callback: Callable[[bytes], Awaitable[None]]) -> bool:
        """Start streaming. Calls callback(raw_bytes) for each chunk.

        Returns True if pipe-pane started successfully, False on failure
        (e.g., tmux version too old, pane doesn't exist).
        """
        ...

    async def stop(self):
        """Stop streaming and clean up."""
        ...

    @property
    def is_alive(self) -> bool:
        """True if reader is running and has not received EOF."""
        return self._running and not self._eof
```

**FIFO lifecycle:**
1. Create FIFO at `/tmp/orchestrator_pty/<safe_pane_id>_<pid>.fifo`
   (`safe_pane_id` = pane_id stripped of `%` prefix; `pid` = server PID)
2. Run `tmux pipe-pane -O -t <target> 'exec cat > <fifo_path>'`
3. Open FIFO with `O_RDONLY | O_NONBLOCK` (avoids blocking if writer not yet connected)
4. Register with `asyncio.get_running_loop().connect_read_pipe()` for async reads
5. Read loop: `reader.read(8192)` → `_strip_tmux_sequences()` → `callback()`
6. On EOF: set `self._eof = True`, notify pool for cleanup
7. On stop: `tmux pipe-pane -t <target>` (no args = stop piping), unlink FIFO

### 4.2 Deferred: DEC 2026 synchronized output batching

> **Note**: This phase is **deferred until after Phase 1+3 are validated**.
> xterm.js 6.0.0 natively handles DEC 2026 on the client side (section 6.4).
> Pipe-pane alone may eliminate tearing without server-side 2026 batching.
> If tearing persists after Phase 1+3, implement this phase. See discussion
> in section 6.4.

Add a stateful byte-level parser that detects BSU/ESU markers in the raw
stream and batches bytes between them into atomic sends:

```
BSU = b'\x1b[?2026h'   (CSI ? 2026 h — begin synchronized update, 8 bytes)
ESU = b'\x1b[?2026l'   (CSI ? 2026 l — end synchronized update, 8 bytes)
```

**State machine transitions:**

```
State: PASSTHROUGH (default)
  byte == 0x1b (ESC) → save position, goto PARTIAL_MATCH(matched=1)
  otherwise → forward byte immediately

State: PARTIAL_MATCH(matched=N)
  byte matches BSU[N] or ESU[N] →
    if N+1 == len(BSU) and last byte == 'h' → emit buffered partial, goto BUFFERING
    if N+1 == len(ESU) and last byte == 'l' → flush frame buffer, goto PASSTHROUGH
    else → goto PARTIAL_MATCH(matched=N+1)
  byte does NOT match →
    emit the accumulated partial bytes, goto PASSTHROUGH, re-process byte

State: BUFFERING (inside BSU..ESU frame)
  byte == 0x1b → save position, goto PARTIAL_MATCH_IN_FRAME(matched=1)
  otherwise → append to frame buffer

State: PARTIAL_MATCH_IN_FRAME(matched=N)
  (same as PARTIAL_MATCH but returns to BUFFERING on mismatch)
```

BSU and ESU share the 7-byte prefix `\x1b[?2026`; only the final byte
(`h` vs `l`) distinguishes them. The parser tracks how many bytes of this
prefix have been matched and carries state across `read()` chunk boundaries.

**Safety bounds:**
- **Timeout**: If ESU does not arrive within **100ms** of BSU, flush the
  frame buffer immediately and return to PASSTHROUGH.
- **Size cap**: If the frame buffer exceeds **256KB** (matching
  `SNAPSHOT_RECOVERY_THRESHOLD`), flush immediately regardless of ESU.
  This prevents unbounded memory growth from a misbehaving application
  that emits BSU without ESU.
- **Nested BSU**: If a second BSU is received while already in BUFFERING,
  ignore it (treat as regular data in the frame buffer). The outermost
  BSU/ESU pair defines the frame boundary.

When BSU is detected:
- Accumulate bytes in a frame buffer instead of forwarding immediately
- When ESU is detected: flush entire frame buffer as one callback invocation
- BSU/ESU bytes themselves are **included** in the output (passed through
  to xterm.js for client-side synchronized rendering)

This ensures TUI frames are sent atomically to xterm.js, completely
eliminating tearing for apps that use synchronized output.

### 4.3 Changes to `ws_terminal.py`

Replace the `on_pane_output` / `stream_flusher` / control-mode subscription
flow with `PtyStreamReader`:

```python
# BEFORE (control mode):
conn = await pool.get_connection(tmux_sess)
await conn.subscribe(pane_id, on_pane_output)
# ... stream_flusher batches and sends

# AFTER (pipe-pane):
pty_reader = PtyStreamReader(tmux_sess, tmux_win, pane_id)

async def on_pty_data(raw_bytes: bytes):
    await websocket.send_bytes(raw_bytes)

await pty_reader.start(on_pty_data)
```

**What stays the same:**
- Input path: `send_keys_async()` via control mode (unchanged)
- Resize: `resize_async()` via control mode (unchanged)
- History/sync: `capture_pane_with_history_async()` (unchanged)
- Drift correction: periodic `capture-pane` sync (unchanged, but less needed)
- Frontend: WebSocket protocol unchanged (binary frames = raw PTY bytes)
- Reconnection: frontend backoff logic unchanged

**What changes:**
- Output streaming source: `%output` subscription → `pipe-pane` FIFO read
- Remove: `stream_buffer`, `flush_event`, `stream_flusher` task
- Remove: `on_pane_output` callback and SNAPSHOT_RECOVERY_THRESHOLD logic
- Add: `PtyStreamReader` lifecycle management in the WebSocket handler
- `TmuxControlConnection._output_subscribers` / `_strip_states` become unused
  for output (keep the class for input/resize)

### 4.4 Changes to `TmuxControlConnection`

The control mode connection is still needed for:
- `send_keys` (input)
- `resize` (window resize)
- Metadata queries

But we can remove the `%output` subscriber system (`_read_output`,
`_output_subscribers`, `subscribe`, `unsubscribe`) since output now comes
from `pipe-pane`. This simplifies the class significantly.

**However**, to minimize risk we can keep the subscriber system intact and
simply not use it. Remove it in a follow-up cleanup.

---

## 5. Original Implementation Plan (superseded by section 11)

> **Note**: This was the initial plan before edge case review. See
> **section 11** for the revised plan that incorporates all mitigations.

### Phase 1: `PtyStreamReader` (core) — ~0.5 day

1. Create `orchestrator/terminal/pty_stream.py`
2. Implement FIFO creation, `pipe-pane -O` start, async read loop
3. Implement `_strip_tmux_sequences` integration (reuse existing function)
4. Implement cleanup: stop pipe-pane, unlink FIFO
5. Handle edge cases: FIFO already exists (stale from crash), pane destroyed

### Phase 2: DEC 2026 batching — ~0.5 day

1. Add BSU/ESU detection state machine in `PtyStreamReader`
2. Frame buffer accumulation between BSU and ESU
3. Safety timeout (100ms) for incomplete frames
4. Flush complete frames as single WebSocket binary sends

### Phase 3: Integrate into `ws_terminal.py` — ~0.5 day

1. Replace `on_pane_output` + `stream_flusher` with `PtyStreamReader`
2. Start `PtyStreamReader` after initial history is sent (same deferred pattern)
3. Stop `PtyStreamReader` on WebSocket disconnect
4. Keep drift correction as safety net (can reduce frequency since stream is
   more reliable)
5. Handle pane ID changes (drift correction re-creates `PtyStreamReader`)

### Phase 4: Tests — ~0.5 day

1. Unit tests for `PtyStreamReader` (mock tmux commands, test FIFO lifecycle)
2. Unit tests for DEC 2026 batching (BSU/ESU detection, timeout)
3. Update `test_terminal_sync.py` to use new streaming path
4. Integration test: start tmux pane, run a TUI-like output, verify bytes arrive

### Phase 5: Cleanup — ~0.25 day

1. Remove unused `stream_buffer` / `flush_event` / `stream_flusher` from
   `ws_terminal.py`
2. Remove `SNAPSHOT_RECOVERY_THRESHOLD` (no longer needed — pipe-pane doesn't
   buffer on our side)
3. Optionally simplify `TmuxControlConnection` by removing subscriber system

**Total estimated effort: ~2 days**

---

## 6. Edge Cases & Gaps Found During Review

### 6.1 CRITICAL: Multiple WebSocket connections to the same pane

**Problem**: `tmux pipe-pane` allows only **one pipe per pane**. A second
`pipe-pane -O` call on the same pane **replaces** the first — the first
reader gets EOF immediately. (Verified experimentally on tmux 3.6a.)

This breaks when:
- User opens the same session in two browser tabs
- Page refresh (new WS connects before old WS has cleaned up)
- Frontend reconnect (old connection still in `finally` block)

The current `%output` system handles this via subscriber sets — multiple
callbacks are registered per pane, all receiving the same bytes.

**Solution**: Use a **shared `PtyStreamReader` per pane** with fan-out.

```python
class PtyStreamPool:
    """One PtyStreamReader per pane, multiple consumers fan out.

    Thread-safe via asyncio.Lock (all public methods acquire before
    mutating _readers/_consumers).
    """
    _readers: dict[str, PtyStreamReader]    # pane_id -> reader
    _consumers: dict[str, set[Callable]]    # pane_id -> callbacks
    _lock: asyncio.Lock

    async def subscribe(self, pane_id, session, window, callback):
        """Start reader on first subscriber, fan out to all."""
        async with self._lock:
            if pane_id not in self._readers:
                reader = PtyStreamReader(session, window, pane_id)
                started = await reader.start(
                    lambda data: self._dispatch(pane_id, data)
                )
                if not started:
                    return False  # caller should fall back to %output
                self._readers[pane_id] = reader
            self._consumers.setdefault(pane_id, set()).add(callback)
            return True

    async def unsubscribe(self, pane_id, callback):
        """Stop reader when last subscriber leaves."""
        async with self._lock:
            subs = self._consumers.get(pane_id)
            if subs:
                subs.discard(callback)
                if not subs:
                    await self._readers[pane_id].stop()
                    del self._readers[pane_id]
                    del self._consumers[pane_id]

    async def _dispatch(self, pane_id, data: bytes):
        """Fan out data to all subscribers without blocking on slow ones."""
        async with self._lock:
            subs = set(self._consumers.get(pane_id, ()))  # snapshot
        # Fire-and-forget per subscriber so a slow WebSocket send
        # doesn't block delivery to other subscribers.
        for cb in subs:
            asyncio.create_task(cb(data))

    async def _on_reader_eof(self, pane_id):
        """Called when a PtyStreamReader receives EOF (pane destroyed,
        cat killed, etc.). Removes the dead reader so drift correction
        can re-subscribe with a fresh one."""
        async with self._lock:
            if pane_id in self._readers:
                await self._readers[pane_id].stop()
                del self._readers[pane_id]
            # Keep _consumers — subscribers are still connected and
            # will receive data once a new reader is created.
```

This mirrors the existing `TmuxControlConnection` subscriber pattern,
with two improvements: (1) `asyncio.Lock` protects all dict mutations,
and (2) `_dispatch` uses `create_task` per callback so a slow subscriber
cannot block others.

### 6.2 CRITICAL: WebSocket backpressure / slow client

**Problem**: The plan calls `websocket.send_bytes(raw_bytes)` directly in
the callback. If the WebSocket is congested (slow network, background tab),
`send_bytes()` awaits, which blocks the read loop. Meanwhile:
1. pipe-pane's `cat` keeps writing to the FIFO
2. Kernel FIFO buffer fills (64KB on macOS)
3. `cat` blocks on write
4. tmux buffers output internally, potentially slowing the pane

The current system handled this with `SNAPSHOT_RECOVERY_THRESHOLD` (256KB
buffer → discard + sync). We need an equivalent.

**Solution**: Keep a bounded send buffer + snapshot recovery:

```python
async def on_pty_data(raw_bytes: bytes):
    send_buffer.extend(raw_bytes)
    if len(send_buffer) > SNAPSHOT_RECOVERY_THRESHOLD:
        send_buffer.clear()
        sync_requested = True  # drift correction will send capture-pane
        return
    flush_event.set()

async def send_flusher():
    while True:
        await flush_event.wait()
        await asyncio.sleep(0.016)  # batch window
        flush_event.clear()
        if send_buffer and not sync_in_progress:
            data = bytes(send_buffer)
            send_buffer.clear()
            await websocket.send_bytes(data)
```

Alternatively, use a **separate asyncio task** for sending so the FIFO read
loop never blocks. The read task writes to a bounded queue; the send task
drains the queue. Overflow → discard + request sync.

### 6.3 FIFO open race condition

**Problem**: Ordering matters:
1. `os.mkfifo()` creates the FIFO
2. `tmux pipe-pane -O ... 'exec cat > fifo'` starts the writer (async —
   tmux spawns `cat` in its own process space)
3. We open the read end

Opening a FIFO with `O_RDONLY` blocks until a writer connects. The writer
(`cat`) is spawned by tmux asynchronously after `pipe-pane` returns.

**Solution**: Open with `O_RDONLY | O_NONBLOCK`. Verified on macOS — this
returns immediately even without a writer. Then use
`asyncio.get_running_loop().connect_read_pipe()` for async reads. Data flows
once `cat` connects. (Note: avoid the deprecated `get_event_loop()` form.)

Edge case: if `cat` never starts (tmux error), the reader sees no data
forever. **Add a startup timeout**: if no bytes arrive within 3 seconds of
starting pipe-pane, log a warning and fall back to drift-correction-only.

### 6.4 xterm.js DEC 2026 support — RESOLVED

**Previously**: Our xterm.js was v5.5.0 which did not support DEC mode 2026.

**Resolution**: Upgraded to **xterm.js 6.0.0** + **addon-fit 0.11.0** on
Feb 16, 2026. DEC mode 2026 (synchronized output) is now natively supported
(PR #5453). Build verified clean — zero type errors, zero breaking changes
affecting our code.

**Impact on Phase 2**: We no longer need to strip BSU/ESU sequences. They
should be **passed through** to xterm.js, which will natively buffer
rendering between BSU and ESU. Server-side batching between BSU/ESU is still
beneficial (sends complete frames as single WebSocket binary messages) but
xterm.js provides a second layer of protection on the client side.

**Phase 2 deferral rationale**: Because xterm.js 6.0.0 handles DEC 2026
natively on the client, pipe-pane streaming (Phase 1+3) alone may be
sufficient to eliminate tearing. Phase 2 adds significant complexity (a
cross-chunk byte-level state machine with timeout and size bounds). We
should **validate Phase 1+3 first** — if tearing is gone, Phase 2 becomes
a nice-to-have optimization (fewer WebSocket sends) rather than a
correctness requirement.

### 6.5 DEC 2026 sequence split across read chunks

**Problem**: BSU is `\x1b[?2026h` (8 bytes). A `read(8192)` chunk boundary
could split it: e.g. chunk 1 ends with `\x1b[?20`, chunk 2 starts with
`26h`. A naive `if BSU in data` check would miss it.

**Solution**: The BSU/ESU parser must be a proper **byte-level state
machine** that carries state across chunks (same pattern as the existing
`_strip_tmux_sequences` stateful parser). BSU and ESU share a 7-byte prefix
(`\x1b[?2026`) and differ only in the final byte (`h` vs `l`). The state
machine transitions are specified in section 4.2.

### 6.6 Pane destruction → EOF → recovery

**Problem**: When a tmux window is killed, pipe-pane's `cat` gets SIGPIPE
and the FIFO reader gets EOF. (Verified experimentally.)

The drift correction loop already detects pane ID changes and re-subscribes.
With pipe-pane, it needs to **re-create the `PtyStreamReader`** for the new
pane.

**Solution**: The shared `PtyStreamPool` should detect reader EOF and remove
the dead reader. The drift correction loop detects the new pane ID and calls
`pool.subscribe()` again, which starts a fresh reader.

### 6.7 Gap between history capture and pipe-pane start

**Problem**: Same gap as current system — output produced between
`capture_pane_with_history_async()` and the pipe-pane `cat` connecting to
the FIFO is lost.

**Solution**: Keep the early drift sync (150ms after initial history) as the
current system does. This sends a ground-truth capture-pane to correct any
missed bytes.

### 6.8 Control mode still processes unused `%output` lines

**Problem**: `TmuxControlConnection._read_output()` continues reading
`%output` lines from control mode stdout. With no subscribers, these are
parsed and discarded — wasted CPU, especially during high-output TUI
rendering.

**Solution**: After switching to pipe-pane, send `refresh-client -f
no-output` to the control mode connection. This tells tmux to stop sending
`%output` notifications entirely, reducing tmux-side and Python-side
overhead. The control mode connection stays alive for `send_keys` and
`resize` commands.

**Version guard**: `refresh-client -f no-output` requires tmux ≥ 3.2.
On older tmux, skip this optimization — `%output` lines are still parsed
and discarded by `_read_output()`, which is harmless (just wastes some CPU).

**Duplicate data safety**: Even if `refresh-client -f no-output` fails
silently or the tmux version doesn't support it, there is no risk of
duplicate output reaching the frontend. The pipe-pane path uses
`PtyStreamPool` callbacks; the `%output` path uses
`TmuxControlConnection._output_subscribers`. Since the new code does not
register any `%output` subscribers, stray `%output` lines are parsed and
discarded by `_read_output()` with no effect.

### 6.9 Server hang (not killed) → FIFO backpressure

**Problem**: If the Python server hangs (deadlock, GC pause, etc.) but
doesn't exit, nobody reads the FIFO. `cat` blocks on write. tmux's internal
buffer for that pane fills up.

**Impact**: Low. tmux handles pipe-pane backpressure internally — it stops
reading from the pane's PTY temporarily. The pane's application blocks on
write. When the server recovers and drains the FIFO, everything resumes.
This is the same behavior as a terminal emulator falling behind.

**Mitigation**: No action needed. This is inherent to any streaming system.
The snapshot recovery mechanism (6.2) handles the aftermath.

### 6.10 FIFO cleanup after crash (SIGKILL)

**Problem**: If the server is `kill -9`'d, `finally` blocks don't run.
FIFOs remain on disk at `/tmp/orchestrator_pty/*.fifo`. pipe-pane's `cat`
exits because the read end is gone.

**Solution**:
1. On startup, `PtyStreamPool.__init__()` scans `/tmp/orchestrator_pty/`
   and unlinks all stale FIFOs.
2. Use PID in FIFO names (`<safe_pane_id>_<pid>.fifo`, where
   `safe_pane_id` strips the `%` prefix) to avoid conflicts if two server
   instances somehow run simultaneously.
3. `PtyStreamReader.start()` always tries `os.unlink()` before `os.mkfifo()`.

### 6.11 FIFO permissions on shared systems (rdev)

**Problem**: On multi-user systems, `/tmp/orchestrator_pty/` could be
accessed by other users.

**Solution**: Create directory with `os.makedirs(dir, mode=0o700,
exist_ok=True)`. Create FIFOs with `os.mkfifo(path, 0o600)`.

### 6.12 Silent `cat` death → undetected output loss

**Problem**: pipe-pane's `cat` process could be killed by OOM, signal, or
other external cause. When this happens, pipe-pane stops delivering output
but no error is reported to the server — the FIFO reader simply gets EOF.

Between `cat` dying and the next drift correction check (up to 5s in the
revised plan), output is silently lost.

**Solution**: `PtyStreamReader` detects EOF and immediately notifies
`PtyStreamPool` via `_on_reader_eof()`. The pool removes the dead reader.
Two recovery paths:

1. **Drift correction** (existing): detects the missing reader on its
   next iteration and re-subscribes, creating a fresh `PtyStreamReader`.
2. **Eager restart** (new): `PtyStreamPool._on_reader_eof()` can
   immediately attempt to restart the reader for the same pane (if the
   pane still exists). If restart fails, fall back to drift-correction
   recovery.

To minimize the gap, keep drift correction at **2s** (not 5s) during the
initial rollout. Reduce to 5s once pipe-pane stability is confirmed.

### 6.13 Runtime auto-fallback to `%output`

**Problem**: The fallback strategy (section 8) describes manual code swaps.
If pipe-pane fails to start for an individual pane (tmux error, version
mismatch, etc.), there is no automatic recovery within the same connection.

**Solution**: `PtyStreamPool.subscribe()` returns a boolean indicating
success. When it returns `False` (or the startup timeout fires):

```python
success = await pty_pool.subscribe(pane_id, session, window, on_pty_data)
if not success:
    logger.warning("pipe-pane failed for %s, falling back to %%output", pane_id)
    conn = await ctrl_pool.get_connection(session)
    await conn.subscribe(pane_id, on_pane_output)
    # Use existing stream_flusher path
```

This makes the rollout safe per-connection: each WebSocket handler
independently decides whether to use pipe-pane or `%output` based on
whether `PtyStreamReader` started successfully.

---

## 7. Updated Risk Matrix

| # | Risk | Severity | Probability | Mitigation | Section |
|---|------|----------|-------------|------------|-------|
| 1 | Multi-WS pipe-pane exclusivity | **Critical** | High | Shared PtyStreamPool with fan-out + Lock | 6.1 |
| 2 | WebSocket backpressure blocks FIFO read | **High** | Medium | Bounded send buffer + snapshot recovery | 6.2 |
| 3 | FIFO open race (reader before writer) | Medium | Medium | `O_NONBLOCK` + startup timeout | 6.3 |
| 4 | xterm.js 2026 support | **Resolved** | — | Upgraded to 6.0.0 | 6.4 |
| 5 | BSU/ESU split across chunks | Medium | Medium | Stateful byte-level parser (deferred with Phase 2) | 6.5 |
| 6 | Pane kill → EOF → stale reader | Medium | Medium | Pool detects EOF, drift re-creates | 6.6 |
| 7 | History→pipe-pane gap (lost bytes) | Low | High | Early drift sync at 150ms | 6.7 |
| 8 | Wasted %output processing | Low | Certain | `refresh-client -f no-output` (tmux ≥ 3.2 only) | 6.8 |
| 9 | Server hang → FIFO backpressure | Low | Low | Inherent; snapshot recovery handles | 6.9 |
| 10 | Stale FIFOs after crash | Low | Medium | Cleanup on startup, PID in names | 6.10 |
| 11 | FIFO permissions on shared host | Low | Low | 0o700 dir, 0o600 FIFO | 6.11 |
| 12 | Silent `cat` death → output loss gap | Medium | Low | EOF detection + eager restart + 2s drift | 6.12 |
| 13 | pipe-pane fails to start (version, error) | Medium | Low | Runtime auto-fallback to `%output` per connection | 6.13 |

---

## 8. Fallback Strategy

Three levels of fallback, from automatic to manual:

1. **Per-connection auto-fallback** (runtime): If `PtyStreamPool.subscribe()`
   returns `False` (pipe-pane fails to start, startup timeout fires, or tmux
   version < 2.6), the WebSocket handler automatically falls back to `%output`
   subscription for that connection. No user impact. See section 6.13.

2. **Feature flag** (operational): Add a server config flag
   `TERMINAL_STREAM_MODE = "pipe-pane" | "control-mode"`. Default
   `"pipe-pane"`. Setting to `"control-mode"` bypasses pipe-pane entirely
   and uses the existing `%output` path. No code change or restart needed
   if loaded from config file with reload support; otherwise requires
   restart.

3. **Code revert** (emergency): The `%output` subscriber system in
   `TmuxControlConnection` is deliberately preserved (not removed until
   Phase 5 cleanup). Reverting is a small, well-scoped change in
   `ws_terminal.py`.

---

## 9. What Does NOT Change

| Component | Status |
|-----------|--------|
| tmux session/window management (`manager.py`) | Unchanged |
| Input path (`send_keys_async`, control mode) | Unchanged |
| Resize path (`resize_async`, control mode) | Unchanged |
| History capture (`capture_pane_with_history_async`) | Unchanged |
| Drift correction sync (periodic `capture-pane`) | Unchanged (reduced frequency) |
| Frontend WebSocket protocol (binary + JSON frames) | Unchanged |
| Frontend reconnection with backoff | Unchanged |
| Frontend xterm.js rendering | Unchanged |
| `tmux attach` for direct interaction | Unchanged |
| tmux session persistence across server restarts | Unchanged |

---

## 10. Success Criteria

1. **No tearing**: Claude Code TUI renders without box-drawing artifacts or
   partial frame flashes during normal operation
2. **Latency**: Output latency ≤ current system (should be faster due to
   no octal decode overhead)
3. **tmux independence**: `tmux attach -t orchestrator` works when server is
   stopped; interactive session is fully functional
4. **Reconnect**: After server restart, terminal auto-reconnects within 10s
   and shows current state without manual intervention
5. **No data loss**: All output bytes from the application reach xterm.js
   (no drops, no encoding artifacts)
6. **Graceful degradation**: On tmux < 2.6 or pipe-pane failure, falls back
   to `%output` control mode automatically with no user-visible error
7. **Observability**: FIFO lifecycle events (create, EOF, cleanup), fallback
   triggers, and tmux version detection are logged at INFO level for
   debugging production issues

---

## 11. Updated Implementation Plan

Revised after edge case review. Changes from original plan in **bold**.

### Phase 1: `PtyStreamReader` + `PtyStreamPool` (core)

1. Create `orchestrator/terminal/pty_stream.py`
2. **Add tmux version detection** (`tmux -V` → parsed version, cached at
   module level). Gate pipe-pane usage on version ≥ 2.6.
3. Implement `PtyStreamReader`: FIFO creation with **sanitized pane ID**
   (`%5` → `5`), `pipe-pane -O` start, async read loop with `O_NONBLOCK`
   + `get_running_loop().connect_read_pipe()`
4. **Implement `PtyStreamPool`**: shared reader per pane, subscriber fan-out
   via `create_task` per callback, `asyncio.Lock` protecting all dict
   mutations, reference-counted start/stop
5. Implement `_strip_tmux_sequences` integration (reuse existing function)
6. Implement cleanup: stop pipe-pane, unlink FIFO
7. **Handle EOF detection** (pane destroyed, pipe-pane stopped) with
   `_on_reader_eof()` pool notification and **eager restart** attempt
8. **Startup timeout** (3s — `subscribe()` returns `False` on failure)
9. **Stale FIFO cleanup on pool init** (scan + unlink `/tmp/orchestrator_pty/`)
10. **FIFO permissions**: 0o700 dir, 0o600 FIFO, PID in filename
11. **Structured logging**: log FIFO creation, pipe-pane start/stop, EOF,
   startup timeout, version detection results at INFO level

### Phase 2: Integrate into `ws_terminal.py`

1. Replace `on_pane_output` + `stream_flusher` with `PtyStreamPool.subscribe`
2. **Runtime auto-fallback**: if `subscribe()` returns `False`, fall back to
   `%output` control mode subscription within the same WebSocket handler
   (section 6.13)
3. **Add `TERMINAL_STREAM_MODE` config flag** (`"pipe-pane"` default,
   `"control-mode"` to bypass pipe-pane entirely)
4. **Keep bounded send buffer + snapshot recovery** (same pattern as current
   `SNAPSHOT_RECOVERY_THRESHOLD`)
5. **Keep 16ms batching** in a send-flusher task (decouples FIFO read from
   WebSocket send to avoid backpressure blocking reads)
6. Start streaming after initial history is sent (same deferred pattern)
7. Stop streaming on WebSocket disconnect (unsubscribe from pool)
8. Keep drift correction as safety net (**keep at 2s** during initial rollout;
   reduce to 5s once pipe-pane stability is confirmed)
9. **Keep early 150ms sync** to cover history→pipe-pane gap
10. Handle pane ID changes (drift re-creates via pool)
11. **Send `refresh-client -f no-output`** to control mode connection to
    suppress unused `%output` processing (**only on tmux ≥ 3.2**)

### Phase 3: Tests

1. Unit tests for `PtyStreamReader` (mock tmux commands, test FIFO lifecycle)
2. Unit tests for `PtyStreamPool` (multi-subscriber, EOF handling, Lock
   correctness, fan-out isolation)
3. **Unit test for auto-fallback** (pipe-pane fails → `%output` path used)
4. **Unit test for backpressure / snapshot recovery**
5. **Unit test for tmux version detection** (parsing edge cases)
6. Update `test_terminal_sync.py` to use new streaming path
7. Integration test: start tmux pane, run TUI-like output, verify bytes arrive
8. **Integration test on macOS** (FIFO behavior, O_NONBLOCK)

### Phase 4: Cleanup

1. Remove unused `stream_buffer` / `flush_event` / `stream_flusher`
   (only from the pipe-pane code path — `%output` path kept for fallback)
2. Remove `SNAPSHOT_RECOVERY_THRESHOLD` from ws_terminal (moved to new code)
3. **Do NOT remove `TmuxControlConnection` subscriber system yet** — it is
   the fallback path. Remove only after pipe-pane is stable in production.

### Phase 5 (deferred): DEC 2026 server-side batching

> **Trigger**: Implement only if tearing persists after Phase 1+2 are
> deployed and validated. See section 6.4 for rationale.

1. **Stateful byte-level BSU/ESU parser** (section 4.2 state machine) that
   carries state across chunk boundaries
2. Frame buffer accumulation between BSU and ESU
3. Safety timeout (100ms) + **size cap (256KB)** for incomplete frames
4. **Pass BSU/ESU through** to xterm.js 6.0.0 (native 2026 support)
5. Flush complete frames as single callback invocations
6. Unit tests for parser (split sequences, timeout, size cap, nested BSU)
