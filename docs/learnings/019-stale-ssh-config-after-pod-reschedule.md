# Stale SSH Config After Rdev Pod Reschedule

**Date**: 2026-04-09
**Area**: `terminal/ssh.py`, `terminal/_rws_pool.py`, `session/reconnect.py`, `session/tunnel.py`

## Symptom

After an rdev pod is rescheduled on AKS (node failure, eviction, scaling),
all SSH connections fail with `BrokenPipeError` or `exit_code=255` and
`"Undefined error: 0"`. All workers on the affected host go to
"disconnected" and auto-reconnect keeps failing. Manual `rdev ssh <host>`
from a terminal works because the `rdev` CLI rewrites `~/.ssh/config.rdev`
with the new port.

## Root Cause

`ensure_rdev_ssh_config()` in `ssh.py` only checks if a `Host` entry
**exists** in `~/.ssh/config.rdev`. When a pod is rescheduled, the entry
exists but `HostName` and `Port` are stale (old endpoint). The function
returns `True`, and all SSH commands use the old connection parameters.

The orchestrator only calls `ensure_rdev_ssh_config()` once during
`setup_remote_worker()` (session creation) and never refreshes it.

Additional complication: SSH `ControlMaster=auto` with
`ControlPersist=600` caches the old connection for up to 10 minutes.
Even after the config file is refreshed, stale control sockets can
keep routing traffic to the old endpoint.

## Fix

Added `refresh_rdev_ssh_config(host)` in `ssh.py` which:
1. Kills stale ControlMaster socket (`ssh -O exit`) — before removing config
2. Removes the old Host block from `~/.ssh/config.rdev`
3. Runs `rdev ssh --non-tmux` to regenerate the entry with fresh values

Called from:
- `_start_in_background()` in `_rws_pool.py` — between first failure and retry
- `_refresh_ssh_config_if_stale()` in `reconnect.py` — SSH test at start of reconnect
- `_ensure_rdev_running()` in `reconnect.py` — after pod restart (config guaranteed stale)
- `start_tunnel()` in `tunnel.py` — on SSH exit_code=255 (retry with fresh config)

Guarded by per-host lock and 120-second cooldown to prevent:
- Concurrent threads clobbering each other's refresh
- Rapid refresh loops when the host is genuinely unreachable

## Rule

**SSH config entries can become stale.** When an SSH command fails with
exit_code=255 to an rdev host, consider config staleness as a cause —
don't just retry with the same stale config. The `rdev` CLI is the
source of truth for connection parameters; re-running it refreshes them.

**ControlMaster caches must be invalidated** when the underlying SSH
config changes, or new connections will reuse the stale cached connection.
