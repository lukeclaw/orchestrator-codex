---
title: "Reconnection Handling Redesign"
author: Claude
created: 2026-02-19
status: Planning
---

# Reconnection Handling Redesign

> **Goal**: Eliminate the bug where reconnect logic types shell commands into a running Claude Code TUI by redesigning the reconnect flow as a step-by-step pipeline where each step fixes one layer and makes the next step safe.

## 1. Problem Statement

### 1.1 The Bug

When auto-reconnect (or manual reconnect) triggers while a worker's terminal already has Claude Code running, the reconnect logic sends shell commands into the tmux pane via `send_keys()`. Because Claude Code is a TUI application occupying the terminal, these commands are interpreted as user input to Claude — not as shell commands. This corrupts the Claude session.

### 1.2 Root Cause

The current `reconnect_rdev_worker()` in `reconnect.py:453-697` probes terminal state **intrusively** before knowing whether it's safe to do so:

| Step | Function | What it sends via `send_keys()` |
|------|----------|---------------------------------|
| Step 2 (line 519) | `check_ssh_alive()` | `echo __MRK_START_... && hostname && echo __MRK_END_...` + Enter |
| Step 3 (line 543) | `check_inside_screen()` | `echo "$STY"` + Enter |
| Step 4 (line 556) | `check_screen_exists_via_tmux()` | `screen -ls \| grep... && ps aux \| grep...` + Enter |

There is a "Step 0" optimization (lines 476-498) that uses non-intrusive subprocess SSH to check status, but it **only runs when the tunnel is dead** (`if not tunnel_alive:`). If the tunnel is alive but a reconnect is mistakenly triggered (race condition, stale health check), Step 0 is skipped and the intrusive path executes against a live Claude TUI.

### 1.3 Reproduction Scenarios

1. **Health check race**: Health check marks worker as disconnected. Before reconnect thread starts, the tunnel recovers. `tunnel_alive` is True. Step 0 is skipped. Shell commands are typed into Claude.
2. **Auto-reconnect on alive worker**: A transient network blip triggers `screen_detached` status. Auto-reconnect starts. SSH and Claude are actually fine. The full reconnect path sends probe commands into Claude.
3. **Manual reconnect on partially recovered worker**: User clicks Reconnect on a `screen_detached` worker. By the time the background thread runs, everything has recovered. Commands are typed into Claude.

---

## 2. Design: Sequential Step-by-Step Pipeline

Instead of classifying all state upfront into a flat enum and dispatching, the new design uses a **sequential pipeline** where each step fixes one layer, then evaluates the next. This is simpler and naturally handles partial failures.

The critical invariant: **never send commands to a tmux pane that has a TUI (Claude) running.**

### 2.1 Non-Intrusive Probes Available

These methods determine state without touching the tmux pane:

| # | Probe | Location | Mechanism | What it reveals |
|---|-------|----------|-----------|-----------------|
| 1 | `check_tui_running_in_pane()` | **NEW** in `health.py` | `tmux display-message -p "#{alternate_on}"` | Whether a TUI app is active in the pane |
| 2 | `tunnel_manager.is_alive()` | `tunnel.py` | `proc.poll()` on subprocess PID | Tunnel process alive |
| 3 | `check_worker_ssh_alive()` | `health.py:191-222` | BFS through process tree from pane PID via `ps -eo pid,ppid,comm` | SSH process exists in pane |
| 4 | `check_screen_and_claude_rdev(host, id, None, None)` | `health.py:300-387` | Fresh `subprocess.run(["ssh", host, check_cmd])` | Screen + Claude status on remote |

**Key detail**: Probe #4 must be called with `tmux_sess=None, tmux_win=None` to skip the function's internal SSH-alive check (which returns `"dead"` prematurely when SSH is down). Passing `None` goes straight to the subprocess SSH probe.

**New function — `check_tui_running_in_pane()`**:
```python
def check_tui_running_in_pane(tmux_sess: str, tmux_win: str) -> bool:
    """Check if the tmux pane is in alternate screen buffer mode (TUI running).

    This is a tmux QUERY (display-message), NOT send-keys.
    Completely non-intrusive. Safe to call anytime.
    """
    target = f"{tmux_sess}:{tmux_win}"
    result = subprocess.run(
        ["tmux", "display-message", "-p", "-t", target, "#{alternate_on}"],
        capture_output=True, text=True, timeout=5,
    )
    return result.stdout.strip() == "1"
```

Claude Code (built on Ink/React) always uses the alternate screen buffer, as does GNU Screen when attached. If `#{alternate_on}` is "1", a TUI is active and we must not send commands.

### 2.2 The Pipeline

```
┌──────────────────────────────────────────────────────────────┐
│ Step 0: Acquire per-session lock                             │
│   Prevents concurrent reconnects for the same worker         │
└────────────────────────┬─────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────┐
│ Step 1: Is the pane safe? (non-intrusive: TUI + SSH checks)  │
│                                                               │
│   TUI active AND SSH alive?                                   │
│     → Claude is probably fine. Check via subprocess SSH.      │
│     → If remote says "alive": just fix tunnel if needed, done │
│     → If remote says otherwise: something weird, set error    │
│                                                               │
│   TUI active AND SSH dead?                                    │
│     → Stale alternate screen from dead SSH.                   │
│     → _clean_pane_for_ssh() handles this safely.              │
│     → Continue to Step 2.                                     │
│                                                               │
│   No TUI?                                                     │
│     → Pane shows shell prompt or dead SSH message.            │
│     → Safe to interact. Continue to Step 2.                   │
└────────────────────────┬─────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────┐
│ Step 2: Fix tunnel (if dead)                                  │
│   tunnel_manager.restart_tunnel() — never touches pane        │
└────────────────────────┬─────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────┐
│ Step 3: Ensure SSH connection                                 │
│                                                               │
│   SSH process alive (check_worker_ssh_alive)?                 │
│     → Skip, already connected.                                │
│                                                               │
│   SSH dead?                                                   │
│     → _clean_pane_for_ssh() (safe: no TUI or stale TUI)      │
│     → ssh.rdev_connect() + wait_for_prompt()                  │
│                                                               │
│   ✓ After this step: guaranteed at a remote shell prompt      │
└────────────────────────┬─────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────┐
│ Step 4: Ensure configs on remote                              │
│   _ensure_local_configs_exist() — filesystem only             │
│   _copy_configs_to_remote() — subprocess SSH, no pane         │
└────────────────────────┬─────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────┐
│ Step 5: Check screen + Claude status (safe: at shell prompt)  │
│   Use check_screen_exists_via_tmux() — we KNOW we're at      │
│   a shell prompt, so send_keys is safe here.                  │
│                                                               │
│   Returns (screen_exists, claude_running)                     │
└────────────────────────┬─────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────┐
│ Step 6: Act on findings                                       │
│                                                               │
│   screen + Claude alive?                                      │
│     → screen -r {name} (reattach)                             │
│     → Status: "waiting"                                       │
│                                                               │
│   screen alive, Claude dead?                                  │
│     → screen -r {name} (reattach)                             │
│     → _launch_claude_in_screen()                              │
│                                                               │
│   no screen?                                                  │
│     → _install_screen_if_needed()                             │
│     → screen -S {name} (create new)                           │
│     → _launch_claude_in_screen()                              │
└──────────────────────────────────────────────────────────────┘
```

### 2.3 Why This Is Better Than Flat Enum Dispatch

| Aspect | Old (classify-then-dispatch) | New (sequential pipeline) |
|--------|------------------------------|--------------------------|
| State space | 9 enum values, combinatorial explosion | 6 sequential steps, each independent |
| Unknown states | Need `SSH_DEAD_REMOTE_UNKNOWN` fallback | After SSH reconnects, probe from shell — no unknowns |
| Partial recovery | Pre-committed to one action before starting | Each step re-evaluates, adapts to what it finds |
| Code duplication | Each action repeats common patterns | Shared pipeline, actions only diverge at Step 6 |
| Correctness reasoning | Must verify all state×action combinations | Each step has one invariant: "is pane safe?" |

The key insight: after Step 3 reconnects SSH, we're at a shell prompt. From there, we can safely use `send_keys` to probe screen/Claude status — there's no need to determine this remotely via subprocess SSH. The "unknown remote state" problem disappears entirely.

---

## 3. Safety Mechanisms

### 3.1 `safe_send_keys()` Guard

Defense-in-depth. Wraps `send_keys()` with a TUI check:

```python
class TUIActiveError(RuntimeError):
    """Raised when attempting to send keys to a pane with an active TUI."""
    pass

def safe_send_keys(tmux_sess, tmux_win, text, enter=True):
    if check_tui_running_in_pane(tmux_sess, tmux_win):
        raise TUIActiveError(
            f"TUI running in {tmux_sess}:{tmux_win}, refusing send_keys"
        )
    return send_keys(tmux_sess, tmux_win, text, enter=enter)
```

Used by Steps 5 and 6. Not used by `_clean_pane_for_ssh()` (which has its own TUI handling) or `ssh.rdev_connect()` (which we know is safe after Step 1).

### 3.2 Per-Session Reconnect Locking

```python
_reconnect_locks: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()

def get_reconnect_lock(session_id: str) -> threading.Lock:
    with _registry_lock:
        if session_id not in _reconnect_locks:
            _reconnect_locks[session_id] = threading.Lock()
        return _reconnect_locks[session_id]

def cleanup_reconnect_lock(session_id: str):
    with _registry_lock:
        _reconnect_locks.pop(session_id, None)
```

Acquired at Step 0 with `timeout=5`. If another reconnect is running for the same session, the second caller skips.

### 3.3 `_clean_pane_for_ssh()` Helper

Prepares a pane for SSH reconnection. Only called when we've determined SSH is dead, but handles two edge cases:
1. Dead SSH left the pane in alternate screen mode (TUI case)
2. `rdev ssh` hangs and ignores Ctrl-C (normal case, caught by responsiveness check)

```python
def _clean_pane_for_ssh(tmux_sess, tmux_win, cwd=None):
    # Edge case: dead SSH left pane in alternate screen mode
    if check_tui_running_in_pane(tmux_sess, tmux_win):
        send_keys(tmux_sess, tmux_win, "C-c", enter=False)
        time.sleep(0.5)
        send_keys(tmux_sess, tmux_win, "", enter=True)
        time.sleep(0.5)
        # If still stuck, kill and recreate pane
        if check_tui_running_in_pane(tmux_sess, tmux_win):
            kill_window(tmux_sess, tmux_win)
            ensure_window(tmux_sess, tmux_win, cwd=cwd)
            return

    # Normal case: Ctrl-C + Enter to ensure clean shell prompt
    send_keys(tmux_sess, tmux_win, "C-c", enter=False)
    time.sleep(0.3)
    send_keys(tmux_sess, tmux_win, "", enter=True)
    time.sleep(0.5)

    # Verify pane responded (catches stuck `rdev ssh` that ignores Ctrl-C)
    if not _verify_pane_responsive(tmux_sess, tmux_win):
        kill_window(tmux_sess, tmux_win)
        ensure_window(tmux_sess, tmux_win, cwd=cwd)
```

### 3.4 `_verify_pane_responsive()` Helper

Sends a marker echo command and polls for the response within 3 seconds. If the marker never appears, the pane has a stuck process that ignores input:

```python
def _verify_pane_responsive(tmux_sess, tmux_win, timeout=3.0, poll_interval=0.5):
    cmd = MarkerCommand("echo OK", prefix="PANE_CHK")
    send_keys(tmux_sess, tmux_win, cmd.full_command, enter=True)
    # Poll for marker in captured output
    elapsed = 0.0
    while elapsed < timeout:
        time.sleep(poll_interval)
        elapsed += poll_interval
        output = capture_output(tmux_sess, tmux_win, lines=15)
        result = cmd.parse_result(output)
        if result is not None and "OK" in result:
            return True
    return False
```

---

## 4. New `reconnect_rdev_worker()` — Pseudocode

```python
def reconnect_rdev_worker(conn, session, tmux_sess, tmux_win,
                          api_port, tmp_dir, repo, tunnel_manager=None):

    remote_tmp_dir = f"/tmp/orchestrator/workers/{session.name}"
    screen_name = get_screen_session_name(session.id)

    # ── Step 0: Acquire per-session lock ──────────────────────
    lock = get_reconnect_lock(session.id)
    if not lock.acquire(timeout=5):
        logger.warning("Reconnect %s: another reconnect in progress, skipping", session.name)
        return

    try:
        ensure_window(tmux_sess, tmux_win)

        # ── Step 1: Is the pane safe to interact with? ────────
        tui_active = check_tui_running_in_pane(tmux_sess, tmux_win)
        ssh_alive = check_worker_ssh_alive(tmux_sess, tmux_win, session.host)

        if tui_active and ssh_alive:
            # Claude is probably running fine. Verify via subprocess SSH.
            remote_status, reason = check_screen_and_claude_rdev(
                session.host, session.id, tmux_sess=None, tmux_win=None
            )
            if remote_status == "alive":
                # Everything is fine! Just fix tunnel if needed.
                if not (tunnel_manager and tunnel_manager.is_alive(session.id)):
                    _ensure_tunnel(session, tunnel_manager, repo, conn)
                repo.update_session(conn, session.id, status="waiting")
                logger.info("Reconnect %s: already alive, tunnel fixed if needed", session.name)
                return
            else:
                # TUI is active but remote says Claude isn't running?
                # Unusual state. Log it and let the user investigate.
                logger.warning(
                    "Reconnect %s: TUI active + SSH alive but remote says %s (%s). "
                    "Not touching pane to avoid disruption.",
                    session.name, remote_status, reason
                )
                repo.update_session(conn, session.id, status="error")
                return

        # If we get here, either:
        # - No TUI (shell prompt visible) → safe to send commands
        # - TUI active but SSH dead → stale screen, _clean_pane_for_ssh handles it

        # ── Step 2: Fix tunnel if dead ────────────────────────
        if not (tunnel_manager and tunnel_manager.is_alive(session.id)):
            _ensure_tunnel(session, tunnel_manager, repo, conn)

        # ── Step 3: Ensure SSH connection ─────────────────────
        if not ssh_alive:
            _clean_pane_for_ssh(tmux_sess, tmux_win)
            ssh.rdev_connect(tmux_sess, tmux_win, session.host)
            if not ssh.wait_for_prompt(tmux_sess, tmux_win, timeout=60):
                raise RuntimeError(f"Timed out waiting for shell prompt on {session.host}")
            time.sleep(1)

        # ✓ We are now guaranteed at a remote shell prompt.

        # ── Step 4: Ensure configs on remote ──────────────────
        api_base = f"http://127.0.0.1:{api_port}"
        _ensure_local_configs_exist(tmp_dir, session.id, api_base)
        _copy_configs_to_remote(session.host, tmp_dir, remote_tmp_dir, session.name)

        # ── Step 5: Check screen/Claude status (safe: at shell prompt) ──
        screen_exists, claude_running = check_screen_exists_via_tmux(
            tmux_sess, tmux_win, screen_name, session.id
        )
        logger.info("Reconnect %s: screen_exists=%s, claude_running=%s",
                     session.name, screen_exists, claude_running)

        # ── Step 6: Act on findings ───────────────────────────
        if screen_exists and claude_running:
            safe_send_keys(tmux_sess, tmux_win, f"screen -r {screen_name}", enter=True)
            repo.update_session(conn, session.id, status="waiting")
            logger.info("Reconnect %s: reattached to screen with Claude", session.name)

        elif screen_exists and not claude_running:
            safe_send_keys(tmux_sess, tmux_win, f"screen -r {screen_name}", enter=True)
            time.sleep(1)
            _launch_claude_in_screen(
                tmux_sess, tmux_win, session, tmp_dir, remote_tmp_dir, repo, conn
            )

        else:  # no screen
            _install_screen_if_needed(tmux_sess, tmux_win)
            safe_send_keys(tmux_sess, tmux_win, f"screen -S {screen_name}", enter=True)
            time.sleep(2)
            _launch_claude_in_screen(
                tmux_sess, tmux_win, session, tmp_dir, remote_tmp_dir, repo, conn
            )

    except TUIActiveError as e:
        logger.error("Reconnect %s: TUI guard blocked send_keys: %s", session.name, e)
        repo.update_session(conn, session.id, status="error")
    except Exception:
        logger.exception("Reconnect failed for %s", session.name)
        raise
    finally:
        lock.release()
```

---

## 5. Local Worker Reconnect Redesign

Local workers have no SSH/screen/tunnel. The TUI guard is the main concern:

```python
def reconnect_local_worker(session, tmux_sess, tmux_win, api_port, tmp_dir):
    lock = get_reconnect_lock(session.id)
    if not lock.acquire(timeout=5):
        return

    try:
        ensure_window(tmux_sess, tmux_win)

        # Check if Claude is still running
        if check_tui_running_in_pane(tmux_sess, tmux_win):
            alive, _ = check_claude_process_local(session.id)
            if alive:
                logger.info("Reconnect local %s: Claude still running, nothing to do", session.name)
                return
            # TUI showing but Claude dead — exit dead TUI
            send_keys(tmux_sess, tmux_win, "C-c", enter=False)
            time.sleep(0.5)
            send_keys(tmux_sess, tmux_win, "", enter=True)
            time.sleep(0.5)

        # Now at shell prompt — safe to send commands via safe_send_keys
        _ensure_local_configs_exist(tmp_dir, session.id, f"http://127.0.0.1:{api_port}")

        path_export = get_path_export_command(os.path.join(tmp_dir, "bin"))
        safe_send_keys(tmux_sess, tmux_win, path_export, enter=True)
        time.sleep(0.3)

        if session.work_dir:
            safe_send_keys(tmux_sess, tmux_win, f"cd {shlex.quote(session.work_dir)}", enter=True)
            time.sleep(0.3)

        # ... check session exists, build claude_cmd, launch ...
        safe_send_keys(tmux_sess, tmux_win, claude_cmd, enter=True)
    finally:
        lock.release()
```

---

## 6. Scenario Walkthroughs

### Scenario 1: Tunnel dies, everything else fine

```
Step 1: TUI active? YES. SSH alive? YES.
  → Check remote via subprocess SSH → "alive"
  → Fix tunnel only. Return.
Result: Tunnel restarted. Claude undisturbed. Zero terminal interaction.
```

### Scenario 2: SSH dies, screen+Claude alive on remote

```
Step 1: TUI active? NO (SSH exited, no longer in screen). SSH alive? NO.
  → Pane is safe.
Step 2: Fix tunnel (if dead).
Step 3: SSH dead → _clean_pane_for_ssh → rdev ssh → wait for prompt.
  → Now at remote shell prompt.
Step 4: Copy configs to remote.
Step 5: check_screen_exists_via_tmux → screen=YES, claude=YES.
Step 6: screen -r {name} → reattach.
Result: SSH re-established. Reattached to screen. Claude was never touched.
```

### Scenario 3: Everything dead

```
Step 1: TUI? NO. SSH alive? NO. → Pane safe.
Step 2: Fix tunnel.
Step 3: SSH dead → clean pane → rdev ssh → prompt.
Step 4: Copy configs.
Step 5: screen=NO, claude=NO.
Step 6: install screen → screen -S → launch Claude.
Result: Fresh setup from scratch. Safe throughout.
```

### Scenario 4: Reconnect triggered on healthy worker (race condition)

```
Step 1: TUI? YES. SSH alive? YES.
  → Check remote → "alive"
  → Tunnel alive? YES.
  → Nothing to do. Return.
Result: No action taken. Claude undisturbed.
```

### Scenario 5: Two reconnects triggered simultaneously

```
Thread 1: acquires lock (Step 0), proceeds through pipeline.
Thread 2: lock.acquire(timeout=5) times out → logs warning, returns.
Result: Only one reconnect runs.
```

### Scenario 6: SSH dead, can't reach remote

```
Step 1: TUI? NO. SSH alive? NO. → Pane safe.
Step 2: Fix tunnel.
Step 3: SSH dead → clean pane → rdev ssh → wait for prompt → SUCCESS.
  → Now at shell prompt. Remote is reachable.
Step 5: check_screen_exists_via_tmux from shell → whatever the truth is.
Step 6: Act accordingly.
Result: No "unknown remote" state needed. We just reconnect first, then check.
```

### Scenario 7: Screen exists, Claude crashed, SSH alive

```
Step 1: TUI? NO (Claude exited to shell prompt). SSH alive? YES.
  → Pane is safe (at shell prompt inside screen or outside).
Step 2: Fix tunnel if needed.
Step 3: SSH alive → skip.
Step 4: Copy configs.
Step 5: screen=YES, claude=NO.
Step 6: screen -r → launch Claude.
Result: Claude relaunched in existing screen.
```

### Scenario 8: Stale alternate screen from dead SSH

```
Step 1: TUI? YES (stale screen buffer). SSH alive? NO.
  → SSH dead means the TUI is stale (not a live Claude). Pane will be cleaned.
Step 2: Fix tunnel.
Step 3: SSH dead → _clean_pane_for_ssh:
  - Detects TUI (alternate screen on).
  - Sends Ctrl-C → checks again.
  - If still stuck → kill + recreate pane.
  → Then rdev ssh → prompt.
Step 5: Check screen/Claude.
Step 6: Act accordingly.
Result: Stale screen handled safely.
```

### Scenario 9: SSH alive, TUI active, but remote says Claude is NOT running

```
Step 1: TUI? YES. SSH alive? YES.
  → Check remote → "screen_only" (Claude crashed but screen still attached).
  → This is an unusual state: we're inside screen viewing a shell prompt, but
    alternate_on is "1" because screen itself uses alternate buffer.
  → We log a warning and set status to "error".
  → We do NOT send commands because TUI (screen) is active.
Result: Safe. User gets an error status and can investigate manually.

NOTE: This case could also be handled more aggressively by detaching from screen
(C-a d) since we know it's screen and not Claude. But being conservative here
avoids any risk of misidentification.
```

### Scenario 10: SSH alive but NOT in alternate screen, shell prompt on remote

```
Step 1: TUI? NO. SSH alive? YES. → Pane safe.
Step 2: Fix tunnel if needed.
Step 3: SSH alive → skip.
Step 4: Copy configs.
Step 5: Check screen/Claude from shell prompt.
Step 6: Act on findings.
Result: Standard reconnect from shell prompt. Safe.
```

### Scenario 11: `rdev ssh` hangs and doesn't respond to Ctrl-C

```
Step 1: TUI? NO (no alternate screen). SSH alive? NO.
  → Pane is safe but has a stuck `rdev ssh` process.
Step 2: Fix tunnel if needed.
Step 3: SSH dead → _clean_pane_for_ssh:
  - No TUI active → normal case path.
  - Sends Ctrl-C + Enter to try clearing the hung process.
  - Calls _verify_pane_responsive() — sends marker command and polls.
  - Marker never appears (pane is stuck, process ignores Ctrl-C).
  - Escalates: kill_window() + ensure_window() for a clean slate.
  → Then rdev ssh → wait_for_prompt.
  - If wait_for_prompt also times out (second stuck rdev ssh):
    → kill_window() + ensure_window() again, retry once more.
    → If still fails: RuntimeError (host likely unreachable).
Step 4: Copy configs.
Step 5: Check screen/Claude.
Step 6: Act accordingly.
Result: Stuck rdev ssh recovered via pane kill+recreate.
```

**Why this scenario occurs:**
`rdev ssh host --non-tmux` is sent via `send_keys()`, so there's no `proc.kill()`
available. When it hangs after printing "Starting ssh connection to ...", it becomes
unresponsive to Ctrl-C. The `_verify_pane_responsive()` check detects this by sending
a marker echo command and checking if the shell responds. If not, the pane is killed
and recreated, guaranteeing a clean process group.

**Why one retry is enough:**
- `kill_window` sends SIGHUP to the pane's entire process group
- The new pane has a fresh process group with no inherited children
- If the second attempt also fails, the host is likely unreachable
- The health check loop will trigger another reconnect later

---

## 7. Files to Modify

| File | Change | Scope |
|------|--------|-------|
| `orchestrator/session/health.py` | Add `check_tui_running_in_pane()` function | Small |
| `orchestrator/session/reconnect.py` | Rewrite: pipeline steps, locking, guards, helpers | Large |
| `orchestrator/session/__init__.py` | Update exports | Small |
| `orchestrator/api/routes/sessions.py` | Add `cleanup_reconnect_lock()` in `delete_session` | 1-2 lines |

### What stays the same

- `_launch_claude_in_screen()` — called only when at shell prompt inside screen
- `_ensure_local_configs_exist()` — pure filesystem, no terminal
- `_copy_configs_to_remote()` — uses subprocess SSH, no terminal
- `_check_claude_session_exists_remote()` — uses subprocess SSH, no terminal
- `ensure_prompt_on_remote()` — uses send_keys but only inside `_launch_claude_in_screen`
- `reconnect_tunnel_only()` — subprocess only, no terminal
- `check_screen_exists_via_tmux()` — kept, used in Step 5 after confirming shell prompt
- All health check functions in `health.py`
- All API endpoints in `sessions.py` (same `reconnect_rdev_worker` interface)

### What gets removed

- `check_ssh_alive()` — replaced by non-intrusive `check_worker_ssh_alive()` (process tree)
- `check_inside_screen()` — replaced by Step 1 TUI check + `_clean_pane_for_ssh`
- `parse_hostname_from_output()` — only used by `check_ssh_alive`
- `detach_from_screen()` — no longer needed (pipeline handles this naturally)

---

## 8. Implementation Order

1. Add `check_tui_running_in_pane()` to `health.py`
2. Add `TUIActiveError`, `safe_send_keys()` to `reconnect.py`
3. Add per-session lock registry (`get_reconnect_lock`, `cleanup_reconnect_lock`)
4. Add `_clean_pane_for_ssh()` and `_ensure_tunnel()` helpers
5. Rewrite `reconnect_rdev_worker()` as sequential pipeline
6. Rewrite `reconnect_local_worker()` with TUI guard
7. Update `__init__.py` exports
8. Add `cleanup_reconnect_lock()` in `delete_session`
9. Remove deprecated functions (`check_ssh_alive`, `check_inside_screen`, `detach_from_screen`, `parse_hostname_from_output`)

---

## 9. Testing Strategy

### Unit Tests

- **`check_tui_running_in_pane`**: Mock subprocess to return "0" and "1", verify bool result
- **`safe_send_keys`**: Verify `TUIActiveError` raised when TUI detected
- **Locking**: Verify second concurrent reconnect is skipped (lock timeout)
- **`_clean_pane_for_ssh`**: Verify Ctrl-C sequence and pane kill fallback
- **`_verify_pane_responsive`**: Returns True when marker appears, False when stuck
- **`_clean_pane_for_ssh` escalation**: Escalates to kill+recreate when pane is unresponsive after Ctrl-C
- **Reconnect Step 3 retry**: Retries with kill+recreate on first timeout; raises after both fail
- **`setup_remote_worker` retry**: Same retry pattern for initial setup

### Manual Integration Tests

| Test | Steps | Expected |
|------|-------|----------|
| Tunnel-only recovery | Kill tunnel process while Claude is working | Step 1 detects TUI+SSH alive, fixes tunnel only |
| SSH recovery | Kill SSH connection (network blip) | Steps 1-3: detects SSH dead, cleans pane, re-SSHes. Step 5-6: reattaches |
| Full recovery | Kill everything (tunnel + SSH + screen) | Full pipeline: tunnel → SSH → screen → Claude |
| No-op on healthy worker | Trigger reconnect on working worker | Step 1 detects everything alive, returns immediately |
| Race protection | Trigger two reconnects simultaneously | Lock prevents second reconnect |
| Dead Claude in screen | Kill Claude process on remote | Step 5 finds screen+no Claude, Step 6 relaunches |
| Stale alternate screen | SSH dies while screen was attached | `_clean_pane_for_ssh` handles stale TUI |
| Stuck rdev ssh | `rdev ssh` hangs, ignores Ctrl-C | `_verify_pane_responsive` detects, pane killed+recreated, SSH retried |
