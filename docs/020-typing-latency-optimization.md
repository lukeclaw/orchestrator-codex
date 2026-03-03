---
title: "Terminal Typing Latency Optimization"
author: Yudong Qiu
created: 2026-03-03
last_modified: 2026-03-03
status: Implemented
---

# Terminal Typing Latency Optimization

> **Goal**: Eliminate typing latency spikes in the terminal streaming pipeline so
> keystrokes echo instantly regardless of how many workers are connected.

## 1. Problem Summary

Users experienced noticeable typing lag in the terminal detail page, with periodic
latency spikes every 2-3 seconds that worsened as more remote workers connected.

**Baseline measurements** (13 remote workers connected):

| Metric | Value |
|--------|-------|
| avg    | 66ms  |
| p50    | 32ms  |
| p95    | 329ms |
| max    | 329ms |

The p95 of 329ms is perceptible and disruptive during interactive typing.

## 2. Architecture

The typing pipeline has several hops:

```
xterm.js onData → WebSocket text frame → FastAPI handler
  → tmux send-keys (hex) → PTY echo
  → pipe-pane (FIFO) → PtyStreamPool → stream_flusher batching
  → WebSocket binary frame → xterm.js write
```

Every hop adds latency. The tmux server is single-threaded, so any subprocess call
(`capture-pane`, `display-message`, `list-panes`) serializes with `send-keys` and
blocks the typing roundtrip.

## 3. Root Causes Identified

### 3.1 Fixed 16ms stream batch window

The `stream_flusher` task used a fixed `asyncio.sleep(0.016)` before flushing
buffered PTY output to the WebSocket. For single-character echo (~2-10 bytes),
this added 16ms of unnecessary delay on every keystroke.

### 3.2 Task scheduling overhead for send_keys

Every keystroke created an `asyncio.Task` to call `send_keys_async()`. Task
creation and scheduling adds ~1-2ms overhead per keystroke for no benefit when
the input is a simple character (no Enter splitting needed).

### 3.3 PtyStreamPool lock contention

`_dispatch()` acquired an `asyncio.Lock` and created a new `asyncio.Task` for
each subscriber callback, even though the common case is a single subscriber per
pane. This added unnecessary overhead on the hot path.

### 3.4 Drift correction subprocess contention

The drift correction loop ran every 2 seconds per WebSocket connection, calling
`capture_pane_with_cursor_atomic_async()` (tmux subprocess) and
`get_pane_id_async()` (tmux subprocess). With N terminal viewers, this meant
N tmux subprocess calls every 2 seconds, all serializing on the tmux server and
blocking `send-keys`.

### 3.5 Monitor loop blocking the event loop

The passive monitor loop (`terminal/monitor.py`) ran every 2 seconds when any
worker was in "working" status. It called `tmux.capture_output()` — a synchronous
`subprocess.run("tmux capture-pane ...")` — for every non-disconnected session.
With 13 workers, this was 13 sequential blocking subprocess calls every 2 seconds:

- **Blocked the asyncio event loop** (no `run_in_executor`)
- **Serialized on tmux server** (contending with send-keys)
- **No activity awareness** (ran even during active typing)

This was the primary cause of the periodic 2-3 second spikes after VPN reconnection.

### 3.6 Preview capture contention

The `GET /api/sessions?include_preview=true` endpoint (polled every 10 seconds by
the frontend) called `tmux capture-pane` for all 13+ sessions. Each call serialized
on the tmux server.

## 4. Fixes Applied

### Fix 1: Adaptive stream batching

**File**: `orchestrator/api/ws_terminal.py` (stream_flusher)

Replaced the fixed 16ms sleep with an adaptive scheme:
- **1ms** for small buffers (typing echo, ~2-10 bytes)
- **8ms total** (1ms + 7ms) for burst output (>512 bytes)

This reduces the median flush latency from 16ms to 1ms for the typing case while
still batching large output bursts efficiently.

### Fix 2: Inline send_keys for simple keystrokes

**File**: `orchestrator/api/ws_terminal.py` (input handler)

Simple keystrokes (no Enter splitting needed) now `await send_keys_async()` directly
instead of wrapping in `asyncio.create_task()`. Eliminates task scheduling overhead
for the common case.

### Fix 3: Single-subscriber fast path in PtyStreamPool

**File**: `orchestrator/terminal/pty_stream.py`

Added a lock-free fast path in `_dispatch()` for the common case of a single
subscriber per pane:

```python
if len(subs) == 1:
    cb = next(iter(subs))
    await cb(data)  # direct call, no lock, no task
    return
```

The lock and `create_task` path is only used for multi-subscriber fan-out.

### Fix 4: Optimized drift correction

**File**: `orchestrator/api/ws_terminal.py` (drift_correction)

- **Random stagger** (0-2 seconds) at startup to prevent all terminals from running
  drift correction simultaneously
- **Adaptive interval**: 5 seconds when stream is healthy (pipe-pane active, recent
  data), 2 seconds when unhealthy
- **Skip during typing**: `is_any_session_active()` check skips all subprocess-heavy
  work while any terminal has recent user input (within 5 seconds)
- **Stream health gate**: When pipe-pane is streaming normally, skip the full
  capture-pane sync entirely

### Fix 5: Preview cache during typing

**File**: `orchestrator/api/routes/sessions.py`

Added a per-session preview cache (`_preview_cache`) with 3-second TTL. When
`is_any_session_active()` returns True and a cached preview exists within TTL,
the cached value is returned instead of calling `tmux capture-pane`. This avoids
13+ tmux subprocess calls during the frontend's 10-second polling cycle.

### Fix 6: Monitor loop — skip during typing + non-blocking IO

**File**: `orchestrator/terminal/monitor.py`

Two changes to eliminate the primary source of periodic spikes:

1. **Skip during typing**: Added `is_any_session_active()` check at the top of the
   loop. When any terminal has had user input in the last 5 seconds, the entire
   polling cycle is skipped.
2. **Non-blocking subprocess**: Wrapped `tmux.capture_output()` in
   `asyncio.to_thread()` so that even when polling does run, it doesn't block the
   asyncio event loop.

### Fix 7: FIFO open race fix

**File**: `orchestrator/terminal/pty_stream.py`

Changed FIFO open from `O_RDONLY` to `O_RDWR` to prevent macOS premature EOF
race condition where the reader gets EOF before the writer has opened the pipe.

### Fix 8: tmux binary PATH fix

**File**: `orchestrator/launcher.py`

Fixed PATH order to place `/usr/local/bin` before `/opt/homebrew/bin`, ensuring the
working tmux 3.6a (Intel) is used instead of the broken tmux 3.5a (ARM).

## 5. Measurement Methodology

Latency is measured end-to-end in the frontend:

1. **Start**: `performance.now()` recorded in xterm.js `onData` handler when user
   presses a key
2. **End**: `performance.now()` recorded when the first binary WebSocket frame
   arrives after the keypress
3. **Delta**: End - Start = roundtrip latency

Stats are exposed on `window.__terminalLatency` (avg, p50, p95, max, min, count)
for manual inspection and automated Playwright tests.

Automated Playwright test types characters at 100ms intervals for 15-30 seconds
and reads the accumulated stats.

## 6. Results

### After fixes 1-5 (before monitor fix)

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| avg    | 66ms   | 26ms  | 61% lower  |
| p50    | 32ms   | 15ms  | 53% lower  |
| p95    | 329ms  | 63ms  | 81% lower  |
| max    | 329ms  | 209ms | 36% lower  |

The max/p95 still showed occasional spikes from the monitor loop.

### After fix 6 (monitor loop fix)

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| avg    | 66ms   | 12ms  | 82% lower  |
| p50    | 32ms   | 12ms  | 63% lower  |
| p95    | 329ms  | 18ms  | 95% lower  |
| max    | 329ms  | 26ms  | 92% lower  |

30 seconds of continuous typing with zero periodic spikes.

## 7. Design Principles

### Protect the typing hot path

The tmux server is single-threaded. Any subprocess call that touches tmux
(`capture-pane`, `display-message`, `list-panes`) serializes with `send-keys`.
All background operations must check `is_any_session_active()` before issuing
tmux commands:

- Drift correction: checks before `capture_pane` and `get_pane_id`
- Monitor loop: checks before polling any sessions
- Preview cache: returns cached data during typing
- Health checks: already defer during user activity via `is_user_active()`

### Adaptive over fixed

Fixed timers (16ms batch, 2s drift interval, 2s monitor interval) waste time in
the common case and contend in the worst case. Adaptive schemes that respond to
actual conditions (buffer size, stream health, typing activity) perform better
across all scenarios.

### Non-blocking IO in async code

Synchronous `subprocess.run()` inside async functions blocks the event loop.
Always use `asyncio.to_thread()` or `run_in_executor()` for subprocess calls
in async contexts.
