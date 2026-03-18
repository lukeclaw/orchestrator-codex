# recover_tunnels Must Include Disconnected Workers

**Date:** 2026-03-18
**Root cause:** `recover_tunnels()` skipped workers with `status="disconnected"`, leaving their SSH tunnel processes orphaned after server restart.

## Symptom

After server restart, remote workers stuck in "disconnected" status permanently. Tunnel restart attempts always failed with "remote port forwarding failed". Workers were fully functional (RWS daemon reachable via forward tunnel) but status never recovered.

## Root Cause Chain

1. Tunnel flaps → tunnel_monitor marks workers "disconnected" in DB
2. Server restarts → `recover_tunnels()` **skips disconnected workers**
3. Old SSH tunnel processes survive (they use `start_new_session=True`)
4. These orphaned processes hold port 8093 on the remote host
5. New tunnel attempts fail: port already bound by orphan
6. `fuser -k` remediation on remote doesn't work (not available on rdev containers)
7. tunnel_monitor also skips disconnected workers → no one re-creates the tunnel
8. auto-reconnect triggers but can't establish tunnel → stays disconnected forever

## Fix

1. **`lifecycle.py:recover_tunnels()`**: Remove `if s.status in ("disconnected",): continue`. Always adopt or clean up tunnels for all remote workers at startup.
2. **`tunnel.py:start_tunnel()`**: Call `_kill_orphan_tunnels(host)` before starting a new SSH process — defense-in-depth against untracked orphans during normal operation.

## Rule

**Never skip tunnel recovery for any remote worker status.** Tunnels are designed to survive restarts (`start_new_session=True`). Skipping recovery for any status creates orphaned processes that block future tunnel creation. The cost of checking a dead PID is negligible; the cost of skipping is permanent disconnection.
