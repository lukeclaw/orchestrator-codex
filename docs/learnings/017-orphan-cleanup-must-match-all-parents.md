# 017: Orphan cleanup must not assume ppid=1

**Date**: 2026-03-21
**Area**: PTY streaming, process lifecycle
**Files**: `orchestrator/terminal/pty_stream.py`, `orchestrator/launcher.py`

## Bug

After stopping the dev server, `ps` showed **5,271 orphaned `sh -c exec cat > /tmp/orchestrator_pty/*.fifo`** processes and a zombie Python multiprocessing child holding port 8093.

## Root Causes

### 1. ppid=1 assumption in orphan cleanup

`cleanup_orphaned_pipe_pane_processes()` only killed `cat` processes with `ppid=1` (reparented to init/launchd). But `pipe-pane` spawns `cat` as a child of the **tmux server process**, so orphaned processes kept tmux's ppid and were never matched.

### 2. Custom SIGTERM handler bypassed lifespan teardown

`launcher.py` installed `signal.signal(signal.SIGTERM, lambda: sys.exit(0))`, which raised `SystemExit` and skipped uvicorn's graceful shutdown. The ASGI lifespan teardown (PTY stream cleanup, state manager stop, DB close) never ran, leaving orphaned processes and sockets.

## Fix

1. **Match by FIFO PID, not ppid**: The FIFO filename encodes the server PID (`<pane>_<pid>.fifo`). Kill any `cat` process whose FIFO PID doesn't match the current server — works regardless of parent process.

2. **Remove custom SIGTERM handler**: Uvicorn already handles SIGTERM gracefully, running the full lifespan teardown. The custom handler was actively harmful.

## Rules

- **Never assume orphaned processes have ppid=1.** Child processes inherit the ppid of whatever spawned them (tmux, shell, etc.), not necessarily the orchestrator. Use application-level identifiers (encoded PIDs, PID files) to distinguish own vs stale processes.
- **Never override uvicorn's signal handlers with sys.exit().** It bypasses ASGI lifespan shutdown. If you need custom shutdown logic, put it in the lifespan teardown.
- **On hot reload, the new worker gets a new PID.** Startup cleanup using PID-based matching naturally covers reload — old PID's processes get cleaned up.
