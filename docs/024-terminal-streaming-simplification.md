---
title: "Terminal Streaming Simplification — Remove Control-Mode Output Fallback & Fix Sync Stall"
author: Claude
created: 2026-03-07
status: Implemented
---

# Terminal Streaming Simplification

> **Goal**: Remove the control-mode `%output` streaming fallback, fix the sync
> stall bug, and simplify drift correction — fewer code paths, fewer bugs.

## 1. Background & Motivation

The terminal streaming system currently has **two output streaming mechanisms**:

1. **pipe-pane** (primary): Raw PTY bytes via FIFO. Fast, no encoding overhead,
   no line-level fragmentation. Implemented in `pty_stream.py`.
2. **control-mode `%output`** (fallback): Octal-encoded, line-fragmented output
   via `tmux -C`. Implemented in `control.py` subscriber system.

The control-mode fallback was added for safety during the pipe-pane rollout
(doc 008, section 6.13). It activates when:
- `TERMINAL_STREAM_MODE=control-mode` env var is set
- tmux < 2.6 (pipe-pane `-O` not available)
- Pipe-pane fails to start (FIFO error, startup timeout)

**Why remove it now:**

- Pipe-pane has been stable in production. The fallback has never been triggered
  in normal operation.
- The dual-mode architecture is the **root cause** of the sync stall bug — the
  `stream_healthy` check treats control-mode as perpetually unhealthy, causing
  drift correction to misbehave.
- The `%output` subscriber system in `TmuxControlConnection` (~100 lines) and
  the fallback path in `ws_terminal.py` add complexity for zero practical
  benefit.
- Control-mode is still **required** for I/O operations (`send_keys`, `resize`,
  `capture-pane`). Only the *output streaming* role is removed.

**tmux version concern**: tmux 2.6 was released in 2017. All modern systems
ship tmux 3.x+. If pipe-pane fails to start, the terminal still works via
drift correction syncs (capture-pane every 2s) — a degraded but functional
experience, same as when both modes fail today.

---

## 2. The Sync Stall Bug — Root Cause Analysis

### 2.1 The reported symptom

When pipe-pane stream is idle (no output) and the terminal content hasn't
changed, the display freezes — drift correction syncs are suppressed by the
hash check, and there's no other update path.

### 2.2 The actual root causes (three bugs, not one)

**Bug A: `stream_healthy` conflates "pipe-pane" with "any streaming"**

```python
# ws_terminal.py:314-319
stream_healthy = (
    using_pipe_pane          # ← False for ALL control-mode connections
    and stream_active
    and last_flush_time > 0
    and (now - last_flush_time) < 5.0
)
```

For control-mode connections, `using_pipe_pane = False`, so `stream_healthy`
is **always False**. This means:
- Drift correction always uses the 2s interval (never 5s)
- The `stream_healthy` gate at line 340 never fires
- Every 2s cycle falls through to `_send_sync()` at line 374

With the original plan's `force=True` fix, this would cause **30 unnecessary
full-screen syncs per minute** for every control-mode terminal. The comment on
line 205 warns against exactly this: "avoids expensive client-side full-screen
rewrite that blocks the browser main thread."

**Bug B: `sync_requested` cleared before `_send_sync()` reads it**

```python
# ws_terminal.py:344-349 (drift_correction)
if sync_requested:
    sync_requested = False       # ← cleared HERE
    try:
        await _send_sync()       # ← sees sync_requested=False
```

```python
# ws_terminal.py:207 (_send_sync)
if content_hash == last_sync_hash and not sync_requested:
    return                       # ← hash skip applies!
```

The snapshot recovery path sets `sync_requested = True` (line 153), but drift
correction clears it to `False` before `_send_sync()` can see it. If the hash
matches, the recovery sync is suppressed — the exact recovery mechanism is
defeated by a sequencing bug.

**Bug C: `last_sync_hash` not reset on pane re-subscribe**

```python
# ws_terminal.py:356-368 (drift_correction pane change detection)
if new_pane_id and new_pane_id != pane_id:
    await _stop_streaming()
    pane_id = new_pane_id
    await _start_streaming()
    sync_requested = True     # ← sets flag, but hash is stale
    continue
```

When a pane is destroyed and recreated, `last_sync_hash` retains the old
pane's hash. If the new pane happens to show the same content (e.g., a fresh
shell prompt), the hash matches and the sync is skipped (compounded by Bug B).

---

## 3. The Plan

### Phase 1: Remove control-mode output streaming

Remove `%output` as an output streaming mechanism. Keep control-mode for I/O.

**Files to modify:**

#### `orchestrator/terminal/control.py`

Remove the output subscriber system from `TmuxControlConnection`:
- Delete `_output_subscribers` dict and `_strip_states` dict from `__init__`
- Delete `_read_output()` method (the `%output` parsing loop)
- Delete `subscribe()` and `unsubscribe()` methods
- Delete `_reader_task` lifecycle (start/cancel in `start()`/`stop()`)
- Keep: `send_keys()`, `resize()`, `is_alive`, `start()` (just the process),
  `stop()` (just the process)

Remove top-level functions that only serve `%output`:
- Delete `_unescape_tmux_output()` (only used by `_parse_output_line`)
- Delete `_parse_output_line()` (only used by `_read_output`)

Keep (still used by pipe-pane):
- `_strip_tmux_sequences()` — used by `PtyStreamReader._on_data()`
- `TmuxControlPool` — used for `send_keys_async` / `resize_async`
- `cleanup_stale_control_clients()` — still needed (control-mode process for I/O)
- All `*_async` helper functions (`send_keys_async`, `resize_async`,
  `capture_pane_*`, `check_alternate_screen_async`, `get_pane_id_async`)

#### `orchestrator/api/ws_terminal.py`

Remove control-mode streaming fallback from `_start_streaming()`:
- Remove the `%output` fallback branch (lines 255-265)
- If pipe-pane fails, log a warning and continue with drift-correction-only
  mode (stream_active stays False, drift correction provides updates every 2s)
- Remove `TmuxControlPool` / `TmuxControlConnection` imports for output
  streaming (keep for `send_keys_async` etc.)

Remove `using_pipe_pane` flag:
- Currently used to distinguish pipe vs control streaming mode
- After removal, streaming = pipe-pane or nothing. Simplify `stream_healthy`
  check (see Phase 2)

Remove `TERMINAL_STREAM_MODE` env var and import:
- No longer needed — pipe-pane is the only streaming mode

#### `orchestrator/terminal/pty_stream.py`

- Remove `TERMINAL_STREAM_MODE` variable and its env var read
- Remove `suppress_control_mode_output()` function — was only needed to
  suppress `%output` when pipe-pane is active; with `%output` gone, nothing
  to suppress

Update `_start_streaming()` to not call `suppress_control_mode_output()`.

#### `tests/test_terminal_sync.py`

- Remove `_CONTROL_MODE_PATCH` and `_force_control_mode` fixture
- Update all tests to work with pipe-pane-only mode (mock `PtyStreamPool`
  instead of `TmuxControlPool` for subscription)

### Phase 2: Fix the sync stall (all three bugs)

#### Fix Bug A: Simplify `stream_healthy`

With `using_pipe_pane` gone, simplify the health check:

```python
# BEFORE:
stream_healthy = (
    using_pipe_pane
    and stream_active
    and last_flush_time > 0
    and (now - last_flush_time) < 5.0
)

# AFTER:
stream_healthy = (
    stream_active
    and last_flush_time > 0
    and (now - last_flush_time) < 5.0
)
```

This correctly reflects: "is the stream delivering data?" regardless of which
mechanism is active (there's only one now).

#### Fix Bug B: Pass `force` flag instead of relying on `sync_requested`

Add a `force` parameter to `_send_sync()`:

```python
async def _send_sync(force: bool = False):
    ...
    if content_hash == last_sync_hash and not force:
        return
    ...
```

Remove the `sync_requested` check from inside `_send_sync()` — it was unreliable
due to the clearing order. Instead, callers pass `force=True` explicitly:

```python
# Snapshot recovery — must always send
if sync_requested:
    sync_requested = False
    await _send_sync(force=True)   # force bypasses hash check
    continue

# Unhealthy stream — force on transition only (see below)
await _send_sync(force=force_next_sync)
```

#### Fix Bug C: Reset hash on pane re-subscribe

```python
if new_pane_id and new_pane_id != pane_id:
    await _stop_streaming()
    pane_id = new_pane_id
    last_sync_hash = None          # ← reset stale hash
    await _start_streaming()
    sync_requested = True
    continue
```

### Phase 3: Targeted sync forcing on health transition

The original plan proposed `force=True` for **every** drift correction sync when
stream is unhealthy. This is wasteful — most unhealthy-stream ticks have
identical content (idle terminal, no output).

Instead, detect the **healthy → unhealthy transition** and force only the first
sync after the transition:

```python
was_stream_healthy = False   # track previous state

while True:
    ...
    stream_healthy = (
        stream_active
        and last_flush_time > 0
        and (now - last_flush_time) < 5.0
    )
    interval = 5.0 if stream_healthy else 2.0
    await asyncio.sleep(interval)

    # Re-check after sleep
    stream_healthy = ...

    # Detect transition: was healthy, now isn't
    force_next_sync = was_stream_healthy and not stream_healthy
    was_stream_healthy = stream_healthy

    if stream_healthy and not sync_requested:
        continue

    if sync_requested:
        sync_requested = False
        await _send_sync(force=True)
        continue

    # Pane ID change detection...

    # Drift correction sync — force on transition only
    await _send_sync(force=force_next_sync)
```

**Why this works:** Display drift can only accumulate while the stream was
delivering data (garbled bytes, partial frames). The first sync after the stream
dies corrects any drift. Subsequent syncs with unchanged content are redundant —
the client already has the correct display from the forced sync.

**Edge case: stream was never healthy.** If pipe-pane fails on startup,
`stream_active` is False, `was_stream_healthy` starts False, so
`force_next_sync` is False. This is correct — if stream never delivered data,
there's no accumulated drift to correct. The early 150ms sync already provided
ground truth.

### Phase 4: Tests

1. **Regression test: sync sent on health transition**
   - Mock pipe-pane delivering data (stream healthy), then stop (stream
     unhealthy). Verify the first sync after transition is sent even when
     content hash matches `last_sync_hash`.

2. **Regression test: snapshot recovery with hash match**
   - Trigger snapshot recovery (`sync_requested = True`). Mock capture-pane
     returning content with same hash as last sync. Verify sync is still sent
     (`force=True` bypasses hash check).

3. **Regression test: pane re-subscribe resets hash**
   - Simulate pane ID change. New pane has same content as old. Verify sync
     is sent (hash was reset to `None`).

4. **Test: drift correction skips when stream is healthy**
   - Keep stream active. Verify no drift correction syncs fire (existing test,
     update for pipe-pane-only).

5. **Test: pipe-pane failure degrades to drift-only mode**
   - Mock `PtyStreamPool.subscribe()` returning `False`. Verify terminal still
     works via drift correction syncs (no crash, no control-mode fallback).

6. **Update existing tests** to remove control-mode patching and mocks.

### Phase 5: Cleanup

1. Remove `TERMINAL_STREAM_MODE` references from:
   - `pty_stream.py` (definition)
   - `ws_terminal.py` (import and usage)
   - `test_terminal_sync.py` (patching)
   - `docs/008-direct-pty-streaming.md` (update to note removal)

2. Update `docs/008-direct-pty-streaming.md` section 8 (Fallback Strategy):
   - Remove per-connection auto-fallback to `%output`
   - Note that pipe-pane failure degrades to drift-correction-only

3. Update module docstrings in `ws_terminal.py` to remove references to
   control-mode streaming and `%output` fallback.

---

## 4. What Changes vs. What Stays

| Component | Status |
|-----------|--------|
| `pipe-pane` output streaming | **Unchanged** (now the only path) |
| `%output` output streaming | **Removed** |
| `TmuxControlConnection.send_keys/resize` | Unchanged |
| `TmuxControlPool` | Unchanged (serves I/O) |
| `capture_pane_*` / `resize_async` / `send_keys_async` | Unchanged |
| `_strip_tmux_sequences()` | Unchanged (used by pipe-pane) |
| `_unescape_tmux_output()` / `_parse_output_line()` | **Removed** |
| `suppress_control_mode_output()` | **Removed** |
| `TERMINAL_STREAM_MODE` env var | **Removed** |
| `using_pipe_pane` flag in `ws_terminal.py` | **Removed** |
| `stream_healthy` check | **Simplified** (no `using_pipe_pane` condition) |
| `_send_sync()` hash check | **Fixed** (force param, no sync_requested read) |
| `last_sync_hash` | **Reset on pane re-subscribe** |
| Drift correction intervals | Unchanged (5s healthy, 2s unhealthy) |
| Frontend WebSocket protocol | Unchanged |
| tmux session management | Unchanged |

---

## 5. Risk Assessment

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| 1 | tmux < 2.6 loses real-time streaming | Low | tmux 2.6 is from 2017; drift correction (2s) provides acceptable fallback |
| 2 | Pipe-pane startup failure has no streaming fallback | Low | Drift correction syncs every 2s provide ground truth; same as current behavior when both modes fail |
| 3 | Removing code that was never triggered | None | No production impact; reduces maintenance burden |
| 4 | Test breakage from removing control-mode mocks | Low | Update tests in same PR; verified by CI |

---

## 6. Known Limitations

- ~~**`is_any_session_active()` blocks all drift correction**~~ **Fixed:**
  The activity check now only defers drift correction when the stream is
  healthy. When the stream is down (dead reader, pipe-pane failure), drift
  correction runs immediately regardless of user activity — it's the only
  update path and a single capture-pane is worth the minor tmux contention.
  Additionally, drift correction now re-subscribes to pipe-pane on each
  unhealthy tick, automatically restarting dead readers.

- **Race window in `_send_sync()`**: Stream data arriving between the two
  `stream_buffer.clear()` calls (lines 192-198) is silently dropped. This is
  inherent to the sync mechanism and unchanged by this plan.

- **Stream bytes dropped during capture-pane**: The `sync_in_progress` flag
  prevents the flusher from sending while sync runs, but bytes accumulate in
  the buffer. If a sync fires frequently, accumulated bytes are cleared. This
  is the existing tradeoff.
