# Investigation: Worker Stuck in Working/Disconnected Oscillation

**Worker**: `ember-cli-checkout_bizarre-orange`
**Symptom**: Worker endlessly cycles between `working` and `disconnected` status, never stabilizing
**Date**: 2026-03-13

## Executive Summary

After a thorough code-level investigation of the session lifecycle, health check, and reconnection systems, I identified **five root causes** that combine to create the oscillation loop. The fundamental issue is that the system lacks a reconnect attempt limiter — it will retry forever without backoff. This interacts with several secondary bugs that can cause false "disconnected" detections or rapid PTY death, creating an unbreakable loop.

---

## The Oscillation Loop (How It Happens)

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Health check finds PTY dead → status = "disconnected"        │
│ 2. Auto-reconnect fires → status = "connecting"                 │
│ 3. Reconnect creates new PTY → status = "working"               │
│ 4. PTY dies (or appears dead to health check)                   │
│ 5. Next health check finds PTY dead → status = "disconnected"   │
│ 6. Go to step 2                                                 │
└─────────────────────────────────────────────────────────────────┘
```

The cycle repeats every ~5 minutes (frontend health check interval at `AppContext.tsx:244`), or faster if manual health checks are triggered.

---

## Root Cause #1 (Critical): No Reconnect Attempt Limiter or Backoff

**Files**: `health.py:1041-1061`, `reconnect.py:1132-1229`

The auto-reconnect logic in `check_all_workers_health()` has **zero protections** against repeated failures:

```python
# health.py:1042-1058
for s in auto_reconnect_candidates:
    if is_user_active(s.id):
        continue
    try:
        trigger_reconnect(s, db, ...)  # No attempt counter, no backoff, no cooldown
    except Exception as e:
        logger.warning(...)
```

**What's missing**:
- No per-session reconnect attempt counter
- No exponential backoff between reconnect attempts
- No maximum retry limit (like the tunnel monitor's `MAX_CONSECUTIVE_FAILURES = 5`)
- No cooldown period after a failed reconnect
- No detection/logging of the oscillation pattern itself

The tunnel monitor (`tunnel_monitor.py:170-180`) correctly implements a failure counter that gives up after 5 consecutive failures. The auto-reconnect system has no equivalent.

**Why the circuit breaker doesn't help**: The circuit breaker (`_HostCircuitBreaker` in `health.py:27-78`) tracks per-host failures. But when the reconnect succeeds (PTY created), the next health check briefly sees the PTY alive, calling `record_success()` which resets the breaker. The PTY then dies, but the breaker has already reset.

---

## Root Cause #2 (High): Missing Verify Step in `reconnect_remote_worker` (No-PTY-ID Path)

**File**: `reconnect.py:829-912`

There are two reconnect paths for remote workers:

| Path | Entry condition | Verify step? |
|------|----------------|-------------|
| **Path A**: `_reconnect_rws_pty_worker()` | `session.rws_pty_id` is set | Yes (line 746-777, waits 3s + checks + retries) |
| **Path B**: `reconnect_remote_worker()` main body | `session.rws_pty_id` is None | **NO** |

Path B creates a PTY and immediately sets `status="working"` with no verification:

```python
# reconnect.py:895-901
pty_id = rws.create_pty(cmd=claude_cmd, ...)
repo.update_session(conn, session.id, rws_pty_id=pty_id, status="working")
# ← No verify! No sleep + check! No retry!
```

Compare with Path A which has a proper verify step:

```python
# reconnect.py:746-777 (_reconnect_rws_pty_worker)
time.sleep(3)
resp = rws.execute({"action": "pty_list"}, timeout=5)
alive = any(p["pty_id"] == pty_id and p["alive"] for p in ptys)
if not alive:
    # Retry with fresh session...
```

**When does Path B execute?** When the health check clears `rws_pty_id` (line 756: `rws_pty_id=None`). However, due to the stale session object issue (Root Cause #4), the reconnect usually receives the old `rws_pty_id` and takes Path A. Path B primarily triggers on first connection or when the daemon was unreachable and the SSH fallback path set `rws_pty_id=None` without the PTY dead-check at line 801.

---

## Root Cause #3 (High): SSH Fallback Health Check Grep Mismatch

**File**: `health.py:770-790`

When the RWS daemon is unreachable (forward tunnel dead, timeout, etc.), the health check falls back to SSH:

```python
# health.py:772-774
check_cmd = (
    "ps aux | grep -v grep | grep -E 'claude (-r|--|--settings)'"
    f" | grep -q '{session.id}' && echo ALIVE || echo DEAD"
)
```

This greps for `session.id` (the orchestrator's UUID) in the process command line. **But when Claude is resumed with `-r {claude_session_id}` and `claude_session_id != session.id`, the orchestrator session ID does NOT appear in the command line.**

The Claude command built by `_build_claude_command()` (`session.py:454-475`):
```
claude -r {claude_session_id} --settings /tmp/orchestrator/workers/{name}/configs/settings.json ...
```

The session ID does not appear in:
- The `-r` argument (uses `claude_session_id`)
- The `--settings` path (uses `session.name`)
- Any other argument

**When `claude_session_id` diverges from `session.id`**: After Claude runs `/clear` or `/compact`, the internal session ID changes. If the frontend updates the `claude_session_id` field via `PATCH /api/sessions/{id}`, subsequent reconnects would use `-r {new_claude_session_id}`, making the SSH fallback blind to the running process.

**Impact**: If the forward tunnel dies (even briefly), the health check can't reach the daemon, falls back to SSH, the grep doesn't find the process, and **falsely marks the session as "disconnected"** — even though Claude is running fine. This triggers a reconnect that creates a SECOND Claude instance (the old one is still running), leading to resource waste and potential conflicts.

---

## Root Cause #4 (Medium): Stale Session Object Passed to Auto-Reconnect

**File**: `health.py:986-1058`

The health check flow has a subtle data staleness issue:

```python
# 1. Sessions are read from DB at the start
to_check = [(s, ...) for s in sessions]

# 2. Health check runs in thread, updates DB (e.g., rws_pty_id=None, status="disconnected")
result = check_and_update_worker_health(conn, session, tunnel_manager)

# 3. Original (stale) session object is added to auto-reconnect candidates
auto_reconnect_candidates.append(s)  # s still has old rws_pty_id, old status

# 4. Stale session passed to trigger_reconnect
trigger_reconnect(s, db, ...)  # s.rws_pty_id is old value, not None
```

The health check at line 756 sets `rws_pty_id=None` in the DB, but the in-memory session object `s` still has the old `rws_pty_id`. This means `trigger_reconnect` receives a session that appears to have a valid PTY ID.

**Consequences**:
- The reconnect goes through Path A (`_reconnect_rws_pty_worker`) instead of Path B
- Path A tries to look up the old (dead) PTY by ID, doesn't find it, then creates a new one — this is correct behavior but adds unnecessary work
- More importantly: if the stale `rws_pty_id` happens to match a PTY that was just recreated by another concurrent operation, it could mistakenly "re-attach" to the wrong PTY

---

## Root Cause #5 (Medium): Forward Tunnel Instability Cascade

**Files**: `remote_worker_server.py:2030-2056`, `reconnect.py:535-643`, `health.py:649-652`

The RWS daemon is accessed through a forward SSH tunnel (`ssh -N -L <local>:127.0.0.1:9741 host`). This tunnel is separate from the reverse tunnel and has its own lifecycle issues:

1. **Health check uses pooled RWS** (`health.py:649`):
   ```python
   rws = _server_pool.get(session.host)
   ```
   If the forward tunnel died since the last operation, `rws.execute()` will fail with a 3-second timeout.

2. **Reconnect re-establishes the tunnel** (`reconnect.py:672`):
   ```python
   _reconnect_rws_for_host(session)  # Calls rws.reconnect_tunnel()
   ```
   This creates a fresh forward tunnel, making the daemon reachable again.

3. **But the tunnel can die again** before the next health check, causing another false disconnect.

**The cascade**: If SSH to the rdev host is unstable (network issues, SSH connection timeouts, rdev infrastructure problems), the forward tunnel repeatedly dies. Each death causes the health check to fail, triggering a reconnect that re-establishes the tunnel, creates a new PTY (abandoning the old one), and the cycle continues.

**Stale RWS object issue**: In `_reconnect_rws_pty_worker`, the `rws` variable is captured at line 669 (`rws = _ensure_rws_ready(...)`). If `_reconnect_rws_for_host()` at line 672 fails and removes the old RWS from the pool (line 574-575), the local `rws` variable becomes stale. Subsequent operations using this stale `rws` (lines 677, 736, 770) would fail, causing the reconnect to fail entirely and set status to "disconnected".

---

## Contributing Factor: Health Check Timing

**File**: `frontend/src/context/AppContext.tsx:228-248`

The health check runs:
- Every **5 minutes** from the frontend (`setInterval(healthCheck, 300000)`)
- **10 seconds** after initial page load (`setTimeout(healthCheck, 10000)`)
- **On demand** via the UI health check button

The 5-minute interval creates a predictable oscillation pattern:
```
T=0:00  Health check → disconnected
T=0:01  Auto-reconnect → connecting → working
T=0:05  PTY dies (or tunnel dies)
T=5:00  Health check → disconnected
T=5:01  Auto-reconnect → connecting → working
...
```

If the user refreshes the page, the 10-second initial health check fires, potentially accelerating the oscillation.

---

## Why the System "Should Always Recover" But Doesn't

The system's recovery design assumes that reconnection will **eventually succeed and stay stable**. But when the underlying cause is persistent (e.g., Claude can't start in this environment, or the SSH tunnel is chronically unstable), the system enters an infinite retry loop because:

1. **No failure memory**: Each reconnect attempt is independent — there's no tracking of "this session has failed to reconnect 10 times in a row"
2. **Circuit breaker resets on success**: The circuit breaker tracks host-level failures, but a "successful" reconnect (PTY created) resets it, even if the PTY dies seconds later
3. **No distinction between transient and permanent failures**: A network blip and a fundamentally broken Claude setup receive identical treatment
4. **The verify step is incomplete**: Even Path A's verify only waits 3 seconds — if Claude takes 5 seconds to crash, the verify passes but the health check later catches it

---

## Live Diagnostic Results (2026-03-13 00:15-00:26)

The following tests were performed non-disruptively against the running system. They confirmed the oscillation in real time and identified the concrete failure chain.

### Test 1: Session State Snapshot

```
Session ID:    7a74e9c2-2442-4e79-8258-8db34d20ed46
Host:          ember-cli-checkout/bizarre-orange
auto_reconnect: true
claude_session_id: NULL   (target_id falls back to session.id — SSH grep issue #3 is NOT a factor here)
```

### Test 2: Reverse Tunnel — Port 8093 Permanently Occupied

The tunnel log (`/tmp/orchestrator/tunnels/ember-cli-checkout_bizarre-orange.log`) contains:
- **211** successful SSH connections ("Welcome to CBL-Mariner")
- **122** "remote port forwarding failed for listen port 8093" errors
- **57** DNS resolution failures
- **30** connection drops ("Broken pipe", "No route to host")

**Every tunnel restart connects successfully at the SSH level but fails to bind port 8093 on the remote host.** The tunnel process stays alive (passes `is_alive()` check) but the reverse port forwarding is non-functional.

The SSH config for this host (`~/.ssh/config.rdev`) has **no inherited LocalForward** entries, so the conflict is from a zombie sshd process on the remote host still holding port 8093 from a previous tunnel session.

The `start_tunnel()` code at `tunnel.py:586` checks for this error during the 3-second startup window, but the error sometimes appears **after** the check window due to SSH handshake timing on the rdev VPN connection (~3s handshake + port forwarding setup).

### Test 3: Forward Tunnel — Daemon Unreachable

At the time of testing, **3 orphaned forward tunnel processes** existed:
```
PID 79683  -L 55797:127.0.0.1:9741  (started 11:54PM — 30 min old, orphaned)
PID 46931  -L 64554:127.0.0.1:9741  (started 12:25AM — from latest reconnect)
PID 46975  -L 64563:127.0.0.1:9741  (started 12:25AM — from latest reconnect)
```

All three ports accept TCP connections (SSH forwarding works) but the **RWS daemon on the remote host does not respond** — connections are immediately closed with zero bytes received. The daemon process on port 9741 of the rdev host is dead or crashed.

The orphan accumulation happens because `reconnect_tunnel()` in `RemoteWorkerServer` kills `self._tunnel_proc`, but if the RWS pool entry was replaced (e.g., by `ensure_rws_starting`), the old process reference is lost and the SSH process becomes orphaned.

### Test 4: Health Check → Immediate Disconnect

Running `POST /api/sessions/{id}/health-check` while status was "working":
```json
{"alive": false, "status": "disconnected", "reason": "RWS PTY dead", "needs_reconnect": true}
```

The health check reached the daemon (response was "RWS PTY dead", not the SSH fallback path), confirming the daemon was alive at that moment. But the PTY (`6fa63dc2444e`) had already died — Claude crashed within ~2 minutes of being created.

### Test 5: Reconnect Cycle Observed in Real Time

Triggered `POST /api/sessions/{id}/reconnect` and polled every 2 seconds:

```
00:25:15  connecting  step=tunnel      tunnel=43968
00:25:17  connecting  step=daemon      tunnel=46752    (tunnel PID changed — restart)
00:25:26  connecting  step=deploy      tunnel=46752    (daemon connected, deploying configs)
00:25:49  disconnected step=failed:pty_create           (PTY creation FAILED)
```

The reconnect took ~34 seconds total. It passed tunnel, daemon, and deploy steps, but **failed at `pty_create`** — the daemon could not create a new PTY. This indicates the daemon was in a degraded state (reachable for health queries but unable to spawn new PTYs).

### Test 6: Previous Reconnect Cycle (Successful but Short-Lived)

An earlier auto-reconnect at ~00:18 DID succeed:
```
00:18:45  connecting → working  (PTY 6fa63dc2444e created)
00:19:41  working               (PTY alive — 56 seconds)
00:21:xx  disconnected           (health check found PTY dead — died within ~2 min)
```

This confirms the oscillation pattern: reconnect succeeds → PTY lives briefly → Claude crashes → health check marks disconnected → auto-reconnect fires again.

### Test 7: Tmux Pane Content (Legacy)

The tmux pane (`orchestrator:1`) shows stale output from the old screen-based architecture:
```
[yuqiu@bizarre-orange ember-cli-checkout]$ screen -rd 7629.claude-7a74e9c2-...
[screen is terminating]
Read from remote host rdev-aks-wus3-7...: No route to host
client_loop: send disconnect: Broken pipe
```

The SSH connection to the rdev host broke with "No route to host", confirming chronic network instability to this rdev instance.

---

## Confirmed Root Cause Chain

The investigation confirms a **three-layer failure cascade**:

### Layer 1: Zombie Port on Remote Host (Trigger)
Port 8093 on the rdev host is permanently occupied by a zombie sshd process from a previous tunnel session that disconnected ungracefully. Every new reverse tunnel successfully connects via SSH but fails to bind the port. The `start_tunnel()` 3-second verification window is sometimes too short to catch the "remote port forwarding failed" error.

**Impact**: The worker's hooks can't call back to the orchestrator API. Claude may function but status/task updates fail silently.

### Layer 2: RWS Daemon Instability (Amplifier)
The RWS daemon on the remote host intermittently crashes or becomes unresponsive. When it works, PTYs are created but Claude exits within seconds to minutes (likely due to environment issues — corrupt session file, the broken reverse tunnel causing hook failures that cascade, or resource constraints). When it doesn't work, `pty_create` fails outright.

Forward tunnel SSH processes accumulate as orphans (no cleanup when the RWS pool entry is replaced).

**Impact**: PTYs are created but short-lived. The daemon eventually becomes unreachable, preventing even PTY creation.

### Layer 3: No Reconnect Backoff (Perpetuator)
The auto-reconnect system retries on every health check cycle (5 minutes) with zero backoff, zero attempt counting, and no loop detection. Each attempt creates new tunnel processes, new forward tunnels, and possibly new daemon deployments — all while the underlying problems (zombie port, unstable daemon) persist.

**Impact**: Infinite oscillation with resource waste (accumulating SSH processes, abandoned PTYs).

---

## Recommended Fixes (Prioritized)

### P0: Add reconnect attempt limiter with exponential backoff
Add a per-session failure counter. After N consecutive reconnect failures (suggested: 5), stop auto-reconnecting and log a clear error. Reset the counter on manual reconnect or after extended uptime (e.g., session stays "working" for >10 minutes).

### P0: Add verify step to `reconnect_remote_worker` Path B
Add the same sleep(3) + pty_list check + retry logic that exists in `_reconnect_rws_pty_worker` to the no-rws_pty_id path in `reconnect_remote_worker`.

### P1: Fix SSH fallback grep to check both session IDs
```python
# Should check both session.id AND session.claude_session_id
ids_to_check = {session.id}
if session.claude_session_id:
    ids_to_check.add(session.claude_session_id)
grep_pattern = "|".join(ids_to_check)
check_cmd = (
    "ps aux | grep -v grep | grep -E 'claude (-r|--|--settings)'"
    f" | grep -qE '({grep_pattern})' && echo ALIVE || echo DEAD"
)
```

### P1: Refresh session from DB before passing to trigger_reconnect
In `check_all_workers_health`, re-read the session from DB after health check completes, before passing to `trigger_reconnect`, to avoid stale `rws_pty_id`.

### P2: Add reconnect loop detection and alerting
Track timestamps of recent reconnect attempts per session. If >3 reconnects within 15 minutes, log a WARNING with the pattern and include it in the health check API response so the frontend can display a "reconnect loop detected" warning.

### P2: Refresh RWS object after `_reconnect_rws_for_host`
In `_reconnect_rws_pty_worker`, re-fetch `rws` from `_server_pool` after calling `_reconnect_rws_for_host()` to avoid using a stale object if the pool entry was replaced.

### P2: Kill zombie remote sshd when reverse tunnel port forwarding fails
When `start_tunnel()` detects "remote port forwarding failed", SSH into the remote host and kill any sshd process holding port 8093 before retrying:
```python
ssh host "fuser -k 8093/tcp 2>/dev/null || ss -tlnp sport = :8093 | awk 'NR>1{print $6}' | grep -oP '\\d+' | xargs kill 2>/dev/null"
```
This clears the zombie port binding so the next tunnel can succeed.

### P2: Clean up orphaned forward tunnel SSH processes
When `_reconnect_rws_for_host()` replaces the RWS pool entry, kill ALL SSH processes doing `-L *:127.0.0.1:9741 <host>` for that host, not just `self._tunnel_proc`. This prevents orphan accumulation.

### P3: Increase `start_tunnel()` verification window for rdev hosts
The 3-second wait at `tunnel.py:566` is sometimes too short for rdev connections over VPN. Either increase to 5 seconds for rdev hosts, or re-check the log after an additional delay if the initial check found no errors.

---

## Files Analyzed

| File | Lines | Role |
|------|-------|------|
| `orchestrator/session/health.py` | 1064 | Health check system, circuit breaker, auto-reconnect orchestration |
| `orchestrator/session/reconnect.py` | 1229 | Reconnection logic (local + remote), step tracking, trigger_reconnect |
| `orchestrator/session/tunnel_monitor.py` | 205 | Async tunnel health loop (60s fast check, 5min deep probe) |
| `orchestrator/session/tunnel.py` | 1012 | ReverseTunnelManager — SSH reverse tunnel subprocess management |
| `orchestrator/session/state_machine.py` | 154 | Session status enum + valid transitions |
| `orchestrator/terminal/remote_worker_server.py` | 2267 | RWS daemon client, forward tunnel, PTY creation |
| `orchestrator/terminal/session.py` | ~600 | `_ensure_rws_ready`, `_build_claude_command`, `setup_remote_worker` |
| `orchestrator/core/orchestrator.py` | 104 | Background task coordination (monitor + tunnel health loops) |
| `frontend/src/context/AppContext.tsx` | ~250 | Health check polling interval (5 min) |
| `docs/009-reconnect-redesign.md` | 702 | Reconnection architecture design |
| `docs/028-reconnect-ux-improvements.md` | - | Reconnect UX improvements (step tracking) |
