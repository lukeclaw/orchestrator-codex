# 018: O_RDWR FIFO masks writer death — re-issue pipe-pane to recover

**Date**: 2026-03-22
**Area**: PTY streaming, pipe-pane, drift correction
**Files**: `orchestrator/terminal/pty_stream.py`, `orchestrator/api/ws_terminal.py`

## Bug

After fixing the idle-pane killing bug (commit 919ec77), the brain pane's terminal stream would silently die and never recover. The screen degraded to 2-second `capture-pane` polling, causing typing lag across all terminals.

## Root Cause

`PtyStreamReader` opens the FIFO with `O_RDWR | O_NONBLOCK` (necessary to prevent a macOS race where `O_RDONLY` returns immediate EOF before the writer connects). However, `O_RDWR` keeps a write reference on the FIFO. When the pipe-pane `cat` process dies (e.g., orphan cleanup during server hot-reload), the reader **never receives EOF** — the fd is still valid because our own write reference keeps it alive.

The old zombie detection (removed in 919ec77) would restart such readers after 30s of no data, but it also wrongly killed genuinely idle panes. With that removed, dead readers persisted forever.

### Why `#{pane_pipe}` doesn't help

Tested: tmux's `#{pane_pipe}` format variable returns `1` even after the cat process is killed. It only tracks whether pipe-pane was SET, not whether the child process is alive. Cannot be used for detection.

## Fix

**Re-issue `tmux pipe-pane -O` to the same FIFO** when the reader is stale (no data for 15s). Tested and confirmed: a new cat process started by re-issuing pipe-pane correctly writes to the existing FIFO, and the O_RDWR reader receives the data without needing a restart.

Two-phase detection in `drift_correction()`:
1. After 15s of no data: re-issue pipe-pane (one tmux subprocess)
2. If data flows: cat was dead, now recovered (~17s total recovery)
3. If still no data: pane is genuinely idle → `confirmed_idle=True` → skip capture-pane, back off to 10s interval

This eliminates the tmux subprocess contention (capture-pane every 2s) that caused typing lag.

## Rules

- **O_RDWR on a FIFO masks writer death.** The reader's own write reference prevents EOF delivery. If you need to detect writer exit, you need an out-of-band mechanism (re-issue the writer, check process existence, etc.).
- **Don't use timeout-based zombie detection on `subscribe()`.** It conflates "dead pipe-pane" with "idle pane". Detection belongs in drift correction where temporal context (did data flow after a refresh?) can disambiguate.
- **Idle panes don't need capture-pane syncs.** When the stream pipe is alive but producing no data, skip the 2s capture-pane polling. It wastes tmux subprocess slots and causes typing lag across all terminals.
- **tmux `#{pane_pipe}` is unreliable for process liveness.** It only tracks whether the pipe-pane command was set, not whether the child process is alive. Don't use it for health detection.
