# Never Kill the Remote Daemon on Transient Failures

**Date**: 2026-03-16
**Commit**: 7e042f6
**Area**: `terminal/remote_worker_server.py` -- `_start_in_background`

## Symptom

After every server restart (uvicorn `--reload`), all remote workers create new PTYs instead of reattaching to existing Claude sessions. The SessionStart hook fires, overwriting task-aware status. Workers lose their running context.

## Root Cause

`_start_in_background` in `get_remote_worker_server()` had a "final resort" path: when `start()` failed, it called `kill_remote_daemon()` then retried. This destroyed all PTYs (and their running Claude sessions) on that host.

The failure was typically transient -- the SSH forward tunnel hadn't established yet, or a broken pipe occurred during reconnect. The daemon on the remote host was alive and healthy; only the local-side connection was broken.

**Trigger sequence**:
1. Server restarts (code change triggers `--reload`)
2. In-memory `_server_pool` is empty (fresh process)
3. Reconnect calls `_ensure_rws_ready` -> `get_remote_worker_server`
4. Phase 3: `_start_in_background` -> `start()` fails (tunnel timeout)
5. "Final resort": `kill_remote_daemon()` -> daemon dies -> all PTYs die
6. Reconnect finds no alive PTY -> creates new one -> SessionStart hook fires

## Fix

Removed `kill_remote_daemon()` from the retry path. The retry now calls `start()` again, which internally calls `_deploy_daemon()` -> `check_existing_daemon()`. This reuses the running daemon if alive (`reused=True`), preserving all PTYs.

## Rule

**Never destroy remote resources to recover from local connection failures.** The daemon and its PTYs are independent of the orchestrator process. If you can't reach the daemon, retry the connection -- don't kill the daemon.

More generally: when a retry involves destroying state, ask whether the state is the problem or the connection is. If the state (daemon, PTY, process) is fine and only the connection is broken, only retry the connection.

## Confirmation from Logs

```
# Good reconnect (daemon reused, PTYs survive):
09:26:42 Daemon deployed on ...: pid=..., reused=True
09:26:42 Reconnect RWS ...: PTY still alive, nothing to do

# Bad reconnect (daemon killed, PTYs destroyed):
09:42:22 Daemon deployed on ...: pid=..., reused=False
09:42:23 Health check RWS: ... PTY ... is dead/gone, marking disconnected
09:42:39 Reconnect RWS ...: created new PTY ...
```
