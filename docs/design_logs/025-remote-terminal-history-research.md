# 025 — Remote Terminal History & RWS as Universal Remote Control Plane

> **Goal**: Investigate easier ways to access remote terminal scrollback history, evaluate eliminating GNU Screen, and design RWS as the single control plane for all remote operations — setup, file sync, pasting, Claude session management, and reconnect.

## 1. The Problem Today

Remote terminal sessions have two UX pain points:

1. **Scrollback is disabled** — `disableScrollback={isRemote}` in `SessionDetailPage.tsx:721` sets xterm.js scrollback to 0 for remote sessions, and mouse wheel events are blocked entirely. Users cannot scroll up to see previous output.

2. **Copy mode is unintuitive** — To view history, users must enter GNU Screen's copy mode (`Ctrl+A [`), navigate with arrow keys, then exit with `Escape`. This is confusing for users unfamiliar with screen keybindings.

### Why scrollback was disabled

The reason is architectural: remote sessions run inside GNU Screen on the remote host. Screen has its own scrollback buffer that conflicts with xterm.js's buffer. When the browser receives data through the `tmux pane → SSH → screen → PTY` chain, the data includes screen's escape sequences for its own viewport management. Enabling xterm.js scrollback on top of this creates rendering artifacts — two competing scrollback systems with no synchronization.

## 2. Current Architecture (What Exists Today)

### Data flow for remote terminal sessions

```
Browser (xterm.js)
    ↕ WebSocket /ws/terminal/{sessionId}
Local Backend (ws_terminal.py → stream_pane)
    ↕ tmux pipe-pane (raw PTY bytes via FIFO)
Local tmux pane
    ↕ SSH connection (text I/O)
Remote host: GNU Screen session
    ↕ screen manages PTY
Remote PTY → /bin/bash → Claude Code
```

### Data flow for remote interactive CLI (RWS-based)

```
Browser (xterm.js)
    ↕ WebSocket /ws/terminal/{sessionId} (via ws_interactive_cli)
Local Backend (stream_remote_pty in ws_terminal.py)
    ↕ TCP socket via SSH forward tunnel (-L)
RWS daemon on remote (port 9741)
    ↕ PTY master_fd
Remote PTY → /bin/bash → interactive CLI
```

### The two parallel systems

| Aspect | Main Terminal (Claude session) | Interactive CLI |
|--------|-------------------------------|-----------------|
| Remote persistence | GNU Screen (`screen -S claude-{id}`) | RWS daemon (daemonized, survives SSH) |
| Data path | tmux → SSH → screen → PTY | WebSocket → TCP tunnel → RWS → PTY |
| Scrollback | Disabled (screen conflicts) | Enabled (64KB ringbuffer on RWS) |
| History on reconnect | None (re-enters screen) | Ringbuffer replay on attach |

The interactive CLI already solved this problem — it uses RWS's PTY management with a 64KB ringbuffer, and scrollback works naturally in the browser.

### Current setup flow: tmux send_keys chaos

The current `setup_remote_worker()` in `session.py` runs ~20 shell commands by typing them into a tmux pane via `send_keys()`. This is inherently fragile:

```python
# Current: typing commands into SSH session via tmux
tmux.send_keys(tmux_session, name, f"screen -S {screen_name}", enter=True)
time.sleep(1)
tmux.send_keys(tmux_session, name, f"chmod +x {remote_tmp_dir}/bin/*", enter=True)
time.sleep(0.3)
tmux.send_keys(tmux_session, name, path_export, enter=True)
time.sleep(0.5)
tmux.send_keys(tmux_session, name, f"cd {work_dir}", enter=True)
time.sleep(0.3)
tmux.send_keys(tmux_session, name, _PW_INSTALL_CMD, enter=True)
time.sleep(3)
tmux.send_keys(tmux_session, name, claude_cmd, enter=True)
```

Each `send_keys` + `time.sleep()` is a blind hope that the command completed. There's no error handling, no output validation, and the entire setup can silently fail if any command takes longer than expected.

## 3. Key Insight: RWS Makes Everything Else Redundant

### What screen provides (only one thing)

GNU Screen's sole purpose is **session persistence** — keeping the Claude Code process alive when SSH drops. From `session.py:520-522`:

> Claude Code runs inside a GNU Screen session to survive SSH disconnections.

### What RWS already provides (everything and more)

| Capability | Screen | tmux send_keys | RWS Daemon |
|-----------|--------|---------------|------------|
| Survives SSH disconnect | Yes | No | Yes (fork + setsid) |
| PTY management | Yes | No | Yes (pty.openpty + fork) |
| File operations | No | Via shell cmds | Yes (list_dir, read_file, write_file, mkdir, etc.) |
| Output streaming | No | No | Yes (raw bytes pushed to stream_conns) |
| Command execution | No | Blind typing | Yes (pty_create with arbitrary cmd + cwd) |
| Error handling | No | No | Yes (JSON response with error field) |
| Reattach with history | Yes (screen -r) | No | Yes (64KB ringbuffer replay) |
| Terminal resize | Yes | Via tmux | Yes (ioctl TIOCSWINSZ + SIGWINCH) |

**RWS can replace both screen AND the tmux send_keys setup pipeline.**

### What the current approach costs us

1. **No browser scrollback** — screen conflicts with xterm.js
2. **Copy mode UX** — Ctrl+A [ is the only way to view history
3. **Complex reconnect** — 12 scenarios in `docs/009-reconnect-redesign.md`, most dealing with screen state
4. **Fragile setup** — Blind `send_keys` + `sleep` with no error handling
5. **Slow setup** — `yum install screen` adds 10-30s; serial `send_keys` with sleeps adds latency
6. **Health check complexity** — Multi-step SSH commands to detect screen state with fallbacks for SCREENDIR mismatches, uppercase SCREEN process names, etc.
7. **Orphan cleanup** — `_kill_orphaned_screen()` for duplicate screen sessions
8. **State machine complexity** — `screen_detached` status exists solely for screen

## 4. Vision: RWS as the Universal Remote Control Plane

### Architecture change

Replace:

```
                              Setup path (fragile)
Local tmux pane ──send_keys──→ SSH → screen → PTY → Claude
                              ↑ blind typing, no error handling

                              Runtime path
Browser ──WS──→ stream_pane ──→ tmux pipe-pane → SSH → screen → PTY
                              ↑ screen escape codes contaminate stream
```

With:

```
                              Setup path (reliable)
Orchestrator ──TCP/JSON──→ RWS daemon ──→ file ops + PTY create
                          ↑ structured commands, JSON responses, error handling

                              Runtime path
Browser ──WS──→ stream_remote_pty ──TCP──→ RWS daemon ──→ PTY master_fd
                                          ↑ raw PTY bytes, no intermediary
```

### What RWS handles (new responsibilities)

| Operation | Current approach | RWS approach |
|-----------|-----------------|--------------|
| Deploy files to remote | `_copy_dir_to_remote_ssh()` (tar pipe through SSH subprocess) | `rws.write_file()` for each file, or new `rws.upload_tar()` action |
| Make scripts executable | `send_keys("chmod +x ...")` | `rws.execute("chmod +x ...")` or new `rws.chmod()` action |
| Set PATH | `send_keys("export PATH=...")` | Pass env vars to `rws.create_pty(env={...})` |
| cd to work_dir | `send_keys("cd /path")` | Pass `cwd` to `rws.create_pty(cwd="/path")` |
| Install Playwright plugin | `send_keys("claude plugin install playwright")` | Pre-setup command in PTY env, or new `rws.exec_cmd()` action |
| Launch Claude Code | `send_keys("claude --settings ...")` | `rws.create_pty(cmd="claude --settings ...", cwd=work_dir, env={...})` |
| Deploy skills to remote | `send_keys("cp ... ~/.claude/commands/")` | `rws.write_file("~/.claude/commands/skill.md", content)` |
| Install Node 24 | `send_keys("volta install node@24")` | `rws.exec_cmd("volta install node@24")` (new action) |
| Health check | Multi-step SSH subprocess probing screen state | `rws.list_ptys()` + `rws.pty_capture()` |
| Paste long system prompt | `tmux.paste_to_pane()` via screen | `rws.pty_input()` directly to PTY master_fd |
| Reconnect after SSH drop | 6-step pipeline with screen detection + reattach | Reconnect tunnel → RWS stream reconnect (PTY never died) |

### Setup flow (new)

```python
def setup_remote_worker(...):
    # 1. Start SSH forward tunnel (for RWS access)
    # 2. Start reverse SSH tunnel (for API callback)
    # 3. Ensure RWS daemon is running (deploy if needed)
    #    - rws.start() handles version check + deploy + connect
    #    - If version mismatch, graceful upgrade (see Section 6)
    # 4. Deploy all files via RWS file operations
    #    - rws.mkdir("/tmp/orchestrator/workers/{name}")
    #    - rws.write_file("/tmp/orchestrator/workers/{name}/bin/report.sh", content)
    #    - rws.write_file("~/.claude/commands/skill.md", content)
    #    - ... (all files deployed via structured API, not blind shell commands)
    # 5. Pre-setup commands via RWS exec (Node install, plugin install)
    #    - rws.exec_cmd("volta install node@24")
    #    - rws.exec_cmd("claude plugin install playwright")
    # 6. Create Claude PTY with full environment
    #    - pty_id = rws.create_pty(
    #        cmd="claude --settings /tmp/.../settings.json --dangerously-skip-permissions ...",
    #        cwd=work_dir,
    #        env={"PATH": "...", "PLAYWRIGHT_MCP_CDP_ENDPOINT": "http://localhost:9222"},
    #      )
    # 7. Store pty_id on session record
    # 8. tmux pane is optional (could be killed or left idle)
```

Key improvements:
- **Every step has error handling** — RWS returns JSON with `{"error": "..."}` on failure
- **No blind sleeps** — Each operation completes before the next starts
- **No tmux dependency for remote operations** — The tmux pane was only needed to type shell commands into SSH
- **Idempotent** — File write operations are naturally idempotent; re-running setup is safe

### Reconnect flow (new)

```python
def reconnect_remote_worker(...):
    # 1. Ensure SSH tunnel is alive (for RWS access)
    # 2. Ensure RWS daemon is running and connected
    #    - rws.start() or reconnect_tunnel()
    # 3. Check PTY status: rws.list_ptys()
    #    - Find PTY matching session_id
    #    - If alive: done! Browser reconnects WebSocket → ringbuffer replay
    #    - If dead: re-deploy files if needed, create new PTY, launch Claude
    # 4. Ensure reverse tunnel is alive (for API callback)
```

Compare this to the current 12-scenario reconnect design doc (009). **The entire screen detection/reattach state machine disappears.** The reconnect reduces to: "is the tunnel alive? is the PTY alive?"

### Pasting and long prompts

Currently, pasting long text into a remote terminal goes through:
```
Browser → WS input → stream_pane → tmux send_keys → SSH → screen → PTY
```

This path has known reliability issues with large payloads (tmux buffer limits, screen interpretation, bracketed paste mode). The new path:
```
Browser → WS input → stream_remote_pty → TCP socket → RWS pty_stream → os.write(master_fd)
```

RWS writes directly to the PTY master file descriptor. No tmux, no screen, no escape sequence reinterpretation. The data arrives exactly as sent.

## 5. Critical Design Challenge: RWS Graceful Upgrade

### The problem

When the orchestrator code changes, the RWS daemon script hash changes. On next `rws.start()`, the version check detects a mismatch:

```python
# In check_existing_daemon():
if old_version != SCRIPT_VERSION:
    _kill_pid(old_pid)  # Kills old daemon
    return None          # Forces fresh start
```

The `shutdown_handler` then runs `cleanup_pty(pty_id)` for every PTY, which **kills all child processes** (including Claude Code):

```python
def cleanup_pty(pty_id):
    # ...
    os.killpg(os.getpgid(session.child_pid), signal.SIGTERM)  # Kills Claude!
    os.close(session.master_fd)  # Closes PTY master, causes EIO on slave
```

If Claude sessions are running inside RWS PTYs (our proposed change), an RWS version upgrade would kill all Claude sessions. This is unacceptable.

### Solution: exec-based hot reload

Instead of kill-and-restart, the RWS daemon should **replace itself in-place** using `os.execvp()`, preserving all file descriptors:

```python
def handle_upgrade(cmd):
    """Hot-reload: serialize state, exec new daemon code, restore state."""

    # 1. Serialize PTY state to a temp file
    state = {
        "ptys": {
            pty_id: {
                "master_fd": session.master_fd,
                "child_pid": session.child_pid,
                "cmd": session.cmd,
                "cwd": session.cwd,
                "cols": session.cols,
                "rows": session.rows,
                "session_id": session.session_id,
                "created_at": session.created_at,
                "ringbuffer_b64": base64.b64encode(bytes(session.ringbuffer)).decode(),
            }
            for pty_id, session in pty_sessions.items()
        },
        "browsers": {
            sid: {"pid": info["pid"], "port": info["port"]}
            for sid, info in browser_processes.items()
        },
    }
    state_file = f"/tmp/orchestrator-rws-{LISTEN_PORT}.state"
    with open(state_file, "w") as f:
        json.dump(state, f)

    # 2. Clear FD_CLOEXEC on all master_fds so they survive exec()
    for session in pty_sessions.values():
        flags = fcntl.fcntl(session.master_fd, fcntl.F_GETFD)
        fcntl.fcntl(session.master_fd, fcntl.F_SETFD, flags & ~fcntl.FD_CLOEXEC)

    # 3. Close server socket and client connections (new daemon will re-bind)
    # PTY master_fds are NOT closed — they survive exec()
    server.close()
    for info in command_conns.values():
        info["conn"].close()
    for info in pty_stream_conns.values():
        info["conn"].close()

    # 4. Write new daemon code to temp file
    new_code_file = f"/tmp/orchestrator-rws-{LISTEN_PORT}.upgrade.py"
    # (new code is sent in the upgrade command payload)
    with open(new_code_file, "w") as f:
        f.write(cmd["new_code"])

    # 5. exec() new daemon — replaces process image, keeps FDs
    os.environ["_RWS_VERSION"] = cmd["new_version"]
    os.environ["_RWS_RESTORE_STATE"] = state_file
    os.execvp(sys.executable, [sys.executable, new_code_file])
```

On startup, the new daemon checks for `_RWS_RESTORE_STATE`:

```python
# At daemon startup:
state_file = os.environ.pop("_RWS_RESTORE_STATE", None)
if state_file and os.path.exists(state_file):
    with open(state_file) as f:
        state = json.load(f)
    # Re-adopt PTY sessions
    for pty_id, info in state["ptys"].items():
        session = PtySession(
            pty_id, info["master_fd"], info["child_pid"],
            info["cmd"], info["cwd"], info["cols"], info["rows"],
            info["session_id"],
        )
        session.ringbuffer = bytearray(base64.b64decode(info["ringbuffer_b64"]))
        pty_sessions[pty_id] = session
        sel.register(session.master_fd, selectors.EVENT_READ,
                     data=("pty_output", pty_id))
    os.unlink(state_file)
```

### Why this works

- `os.execvp()` replaces the process image but preserves open file descriptors (unless `FD_CLOEXEC` is set)
- PTY master_fds are regular file descriptors — they survive exec
- Claude Code processes are in separate sessions (`os.setsid()` in child) — they don't know the daemon restarted
- The PTY slave side (Claude's stdin/stdout/stderr) is unaffected because the master_fd stays open
- Stream connections (browser WebSockets) will break and need to reconnect, but the PTY data is preserved in the ringbuffer

### Simpler alternative: defer upgrade

If exec-based hot reload is too complex for v1, a simpler approach:

```python
def check_existing_daemon():
    if old_version != SCRIPT_VERSION:
        if pty_sessions_exist():
            # Don't upgrade while PTYs are active — wait for idle
            log("Version mismatch but PTYs active, deferring upgrade")
            return old_pid  # Reuse old daemon
        else:
            _kill_pid(old_pid)
            return None  # Upgrade now (no PTYs to lose)
```

This defers upgrade until all PTYs have exited naturally. The old daemon continues running with the old version, which is acceptable since the protocol is backward-compatible. The upgrade happens on next session creation when no PTYs are active.

### Recommended approach

**Phase 1**: Deferred upgrade (simple, safe). Don't kill the daemon if PTYs are active. Add a new `exec_cmd` action so the orchestrator can check if the daemon supports needed features.

**Phase 2**: Exec-based hot reload (zero-downtime). Implement when we're confident in the approach and need seamless upgrades during active sessions.

## 6. New RWS Actions Needed

To support the full setup flow via RWS (replacing send_keys), we need a few new actions:

### `exec_cmd` — Run a shell command and return output

```python
# Request
{"action": "exec_cmd", "cmd": "volta install node@24", "timeout": 30}

# Response
{"status": "ok", "exit_code": 0, "stdout": "...", "stderr": "..."}
```

This replaces blind `send_keys` + `sleep` for setup commands. The orchestrator gets actual exit codes and output.

### `upload_tar` — Extract a tar archive to a directory

```python
# Request
{"action": "upload_tar", "dest_dir": "/tmp/orchestrator/workers/foo",
 "data_b64": "H4sIAAAAAAAAA+3B..."}

# Response
{"status": "ok", "files_extracted": 15}
```

This replaces `_copy_dir_to_remote_ssh()` (tar pipe through SSH subprocess). The tar data is sent via the RWS command socket, extracted on the remote side.

### Enhanced `pty_create` — Support environment variables

```python
# Request
{"action": "pty_create",
 "cmd": "claude --settings /tmp/.../settings.json --dangerously-skip-permissions",
 "cwd": "/home/user/myrepo",
 "cols": 120, "rows": 40,
 "env": {
   "PATH": "/tmp/.../node-bin:/tmp/.../bin:$PATH",
   "PLAYWRIGHT_MCP_CDP_ENDPOINT": "http://localhost:9222"
 },
 "session_id": "abc123"}

# Response
{"status": "ok", "pty_id": "x7k9m2a1b3c4"}
```

Currently `pty_create` only accepts `cmd` and `cwd`. Adding `env` lets the orchestrator set PATH, PLAYWRIGHT_MCP_CDP_ENDPOINT, and other environment variables without typing `export` commands.

## 7. Scrollback Sizing Analysis

### How much history do users need?

| Content type | Typical size | Lines (80-col) |
|-------------|-------------|-----------------|
| Claude Code thinking output | 2-10 KB per response | 50-200 lines |
| Build output (npm, cargo, etc.) | 5-50 KB | 100-1000 lines |
| Test suite output | 10-100 KB | 200-2000 lines |
| `git diff` output | 1-20 KB | 30-500 lines |
| One Claude conversation turn | 5-30 KB | 100-600 lines |

### Recommended ringbuffer size

Current: 64KB (RINGBUFFER_MAX). This stores roughly 1000-2000 lines of terminal output, which covers 2-5 Claude conversation turns.

Recommendation: **512KB**. This provides:
- ~8000-16000 lines of history
- Covers a typical 30-60 minute session
- Memory cost per PTY is negligible (0.5 MB)
- Replay on reconnect takes <100ms over the tunnel

For comparison, xterm.js's default scrollback of 1000 lines at ~100 bytes/line is ~100KB. A 512KB ringbuffer provides 5x that, ensuring the browser never runs out of history.

### xterm.js scrollback configuration

Currently set to `1000` lines for local sessions and `0` for remote. With screen eliminated, remote sessions should use the same `1000` (or higher — `5000` is common for developer terminals). xterm.js scrollback is cheap (stored in typed arrays in memory).

## 8. Risk Assessment

### Low risk

- **RWS PTY streaming is proven** — Interactive CLI already uses this exact path. The `stream_remote_pty()` function in `ws_terminal.py` handles reconnect, PTY exit, and flow control.
- **No new protocol** — Same TCP + JSON-lines protocol already in production.
- **Backward compatible** — Local sessions are unchanged.

### Medium risk

- **RWS becomes a single point of failure** — Currently, if RWS fails, screen-based setup is the fallback. After eliminating screen, RWS must be reliable. Mitigation: robust bootstrap with retries, health monitoring, auto-restart.
- **Health check via RWS tunnel** — RWS tunnel must be alive for health checks. If the tunnel is down, we need a fallback (SSH subprocess check that Claude process is running, even without screen).
- **Exec_cmd action security** — Running arbitrary commands via RWS is powerful but needs guardrails for the setup-only commands.

### Low risk (but needs care)

- **RWS version upgrade with active PTYs** — Must not kill Claude sessions. Deferred upgrade (Phase 1) is safe and simple.

## 9. Alternative Approaches Considered

### Alternative A: Sync screen scrollback to browser

**Idea**: Periodically capture screen's scrollback buffer and send to the browser.

**Rejected**: Screen processes escape sequences and maintains its own terminal state. Replaying captures into xterm.js creates rendering mismatches. Adds complexity instead of removing it.

### Alternative B: Use tmux on remote instead of screen

**Idea**: tmux has `capture-pane -p -S -1000` which captures scrollback cleanly.

**Rejected**: Still adds a terminal multiplexer layer causing the same scrollback conflict. RWS already provides everything tmux would.

### Alternative C: Keep screen, add a scrollback capture endpoint

**Idea**: SSH into remote, run `screen -X hardcopy -h /tmp/scrollback.txt`, read it via RWS, send to browser.

**Rejected**: Polling-based, latency-heavy, still doesn't solve the dual-scrollback-buffer problem.

### Alternative D: Run Claude directly in SSH without screen (no persistence)

**Idea**: Skip screen entirely, accept that SSH drops kill Claude.

**Rejected**: Unacceptable UX — network blips would destroy hours of work. Persistence is essential.

## 10. Recommendation

**Make RWS the universal remote control plane. Eliminate screen, eliminate send_keys-based setup.**

This is a convergent change — everything points in the same direction:
- The scrollback problem is solved by removing screen (not by adding sync)
- The setup reliability problem is solved by using RWS structured commands (not by adding more sleeps)
- The reconnect complexity problem is solved by reducing state space (not by adding more scenarios)
- The architecture already has the right primitives (RWS PTY, file ops, streaming)

## 11. Migration Path

### Phase 1: Claude in RWS PTY (highest impact)
- Add `env` parameter to `pty_create`
- Modify `setup_remote_worker()` to launch Claude in RWS PTY instead of screen
- Update `terminal_websocket()` routing for remote sessions → `stream_remote_pty()`
- Enable scrollback for remote sessions in frontend (remove `disableScrollback`)
- Implement deferred RWS upgrade (don't kill daemon if PTYs active)
- Keep screen code as dead code (safety net during rollout)

### Phase 2: Setup via RWS (reliability improvement)
- Add `exec_cmd` and `upload_tar` actions to RWS
- Rewrite `setup_remote_worker()` to use RWS for file deployment and pre-setup commands
- Eliminate tmux send_keys for remote setup (tmux pane becomes optional)

### Phase 3: Simplified reconnect
- Replace screen-based health checks with RWS-based checks (`list_ptys`, `pty_capture`)
- Reduce reconnect pipeline to: tunnel check → RWS check → PTY check
- Remove `screen_detached` status from state machine

### Phase 4: Clean up
- Delete all screen-related code
- Delete `disableScrollback` prop and wheel event blocking
- Remove tmux pane requirement for remote sessions
- Implement exec-based hot reload for zero-downtime RWS upgrades
- Update docs

### What stays the same
- Local sessions (tmux-based, unchanged)
- RWS daemon lifecycle (deploy via SSH, forward tunnel)
- Reverse tunnel for API callback
- Browser management on RWS
- Interactive CLI (already on RWS PTY)

## 12. Decision: Drop the Local Tmux Pane for Remote Workers

### What the tmux pane currently does (everything)

For remote workers, the tmux pane is the sole I/O channel. Every operation flows through it:

| Operation | How it uses the tmux pane |
|-----------|--------------------------|
| SSH connection | `send_keys("rdev ssh ...")` typed into pane |
| SSH verification | `capture_output()` polls pane for `$` prompt |
| Node install | `send_keys("volta install node@24")` |
| Screen setup | `send_keys("screen -S claude-{id}")` |
| Claude launch | `send_keys("claude --settings ...")` |
| Terminal I/O | `pipe-pane` → FIFO → WebSocket |
| Health check | `check_tui_running_in_pane()`, `check_worker_ssh_alive()` |
| Reconnect | `_clean_pane_for_ssh()` → re-SSH → reattach screen |

### What replaces each in the RWS architecture

| Operation | Old (tmux pane) | New (RWS) |
|-----------|----------------|-----------|
| SSH connection | `send_keys("rdev ssh ...")` | Not needed — `ssh -N -L` is a subprocess |
| RWS deploy | N/A | `subprocess.run(["ssh", host, "python3 -c ..."])` |
| File deployment | `send_keys("chmod +x...")` | `rws.write_file()` / `rws.upload_tar()` |
| Node install | `send_keys("volta install...")` | `rws.exec_cmd("volta install...")` |
| Claude launch | `send_keys("claude ...")` | `rws.create_pty(cmd="claude ...", env=...)` |
| Terminal I/O | `pipe-pane` → FIFO → WS | TCP socket → `stream_remote_pty()` |
| Health check | `check_tui_running_in_pane()` | `rws.list_ptys()` + SSH subprocess fallback |
| Reconnect | clean pane → re-SSH → reattach | reconnect tunnel → check RWS PTY |

**Nothing goes through the tmux pane.** The pane would be an empty local bash shell sitting at a `$` prompt doing nothing.

### Why not keep it for "consistency" with local workers?

**Argument for keeping**: "All workers have a tmux pane. Consistent mental model."

**Counterarguments**:

1. **The consistency is fake** — The local pane runs Claude directly in its PTY. The remote "pane" would be an idle local bash shell while Claude runs in a completely different process on a different machine. They look the same in the UI but work completely differently.

2. **It adds confusion** — Users see a tmux pane and think they can interact with it. They can't. Typing into it does nothing useful. It's a vestigial shell.

3. **Health checks get confused** — Currently `check_and_update_worker_health()` starts with `window_exists()`. If we keep the pane, it returns `True` even though the pane has nothing to do with Claude's health. We'd need to bifurcate the health check anyway. If we remove the pane, remote health checks cleanly go through the RWS path.

4. **Two streaming paths per session** — `terminal_websocket()` would need to check `rws_pty_id` to decide between `stream_pane()` and `stream_remote_pty()`. Removing the pane eliminates `stream_pane()` for remote sessions entirely.

5. **Resource waste** — An idle bash process + tmux window per remote session. Harmless but pointless.

6. **Reconnect complexity** — If the pane exists, the reconnect code might try to interact with it (out of habit / code path sharing), leading to subtle bugs.

### Decision: separate architectures for local and remote

```
Local worker:
  tmux pane → PTY → bash → Claude
  Health: check pane process tree
  Stream: pipe-pane → FIFO → WebSocket
  Reconnect: relaunch Claude in pane

Remote worker:
  RWS daemon → PTY → bash → Claude
  Health: rws.list_ptys() → SSH fallback
  Stream: TCP tunnel → stream_remote_pty → WebSocket
  Reconnect: reconnect tunnel → check PTY
  (no tmux pane at all)
```

This is cleaner, simpler, and eliminates an entire category of bugs (any code that accidentally tries to send_keys to a remote session's non-existent pane). The routing is clear: `is_remote_host(session.host)` → RWS path, else → tmux path.

### What changes

| Component | Change |
|-----------|--------|
| `health.py:check_and_update_worker_health()` | Remote: skip `window_exists()`, go straight to RWS/SSH check |
| `ws_terminal.py:terminal_websocket()` | Remote: call `stream_remote_pty()`, skip `ensure_window()` |
| `session.py:setup_remote_worker()` | Don't create tmux window. All setup via subprocess SSH + RWS |
| `reconnect.py:reconnect_remote_worker()` | Don't touch tmux at all. Tunnel + RWS only |
| `manager.py:ensure_window()` | Only called for local sessions |
| Frontend `TerminalView.tsx` | No change — it talks to WebSocket, doesn't know about tmux |
| Session DB model | No `tmux_window` column needed (already dropped in migration 021) |

### During migration: legacy sessions still need the pane

Old-architecture sessions (no `rws_pty_id`) still go through the tmux pane + screen path. The routing is:

```python
# Health check
if session.rws_pty_id:
    return check_via_rws(session)
else:
    return check_via_screen_and_tmux(session)  # legacy

# Terminal WebSocket
if session.rws_pty_id:
    await stream_remote_pty(websocket, session.rws_pty_id, session.host)
else:
    tmux_sess, tmux_win = tmux_target(session.name)
    ensure_window(tmux_sess, tmux_win)
    await stream_pane(websocket, tmux_sess, tmux_win)

# Reconnect
if session.rws_pty_id:
    reconnect_via_rws(session)
else:
    reconnect_via_screen(session)  # legacy, will migrate on next Claude death
```

Once all sessions have `rws_pty_id`, the legacy paths can be deleted.

## 13. Edge Cases, Race Conditions & Robustness Analysis

> Numbering continues from 13.1 for all edge cases below.

### 13.1Old workers: natural migration on reconnect

**Scenario**: User upgrades the orchestrator. Existing remote workers are still running Claude inside GNU Screen (old architecture). On next reconnect, they should transparently migrate to RWS PTY architecture.

**Detection**: The session DB model currently has no `rws_pty_id` field. If a remote session has no `rws_pty_id`, it's an old-architecture worker.

**Migration flow**:

```python
def reconnect_remote_worker(...):
    lock = get_reconnect_lock(session.id)

    # Step 1: Ensure SSH tunnel + RWS daemon
    ensure_tunnel(...)
    rws = ensure_rws_connected(host)

    # Step 2: Check if this is a new-arch session (has rws_pty_id)
    if session.rws_pty_id:
        # New architecture — just check PTY health via RWS
        return _reconnect_rws_pty(session, rws, ...)

    # Step 3: Old architecture — Claude is running inside screen
    # Check if Claude is still alive via SSH subprocess (existing code)
    screen_status, reason = check_screen_and_claude_remote(
        host, session.id, tmux_sess=None, tmux_win=None
    )

    if screen_status == "alive":
        # Claude is running fine inside screen. DON'T migrate mid-session.
        # Just fix tunnel/SSH and reattach to screen (existing flow).
        # Migration happens next time Claude exits + restarts.
        _reconnect_legacy_screen(session, ...)
        return

    # Step 4: Claude is dead — this is our migration opportunity!
    # Instead of re-creating a screen session, launch Claude in RWS PTY.
    _deploy_files_via_rws(rws, session, ...)
    pty_id = rws.create_pty(
        cmd=build_claude_cmd(session),
        cwd=session.work_dir,
        env=build_env(session),
        session_id=session.id,
    )
    repo.update_session(conn, session.id, rws_pty_id=pty_id)
    # From now on, this session uses the new architecture.
```

**Key principle**: Never migrate a running session. Only migrate when Claude needs to be (re)launched. This is zero-risk — the old screen path works fine for active sessions, and migration happens naturally when Claude exits or crashes.

**DB schema change needed**: Add `rws_pty_id TEXT` column to sessions table. `NULL` means legacy screen architecture; non-null means RWS PTY architecture.

### 13.2 RWS daemon crash / OOM kill

**Scenario**: The RWS daemon is killed by the OS (OOM killer, admin `kill -9`, system restart).

**Impact**: All PTY master_fds are closed when the daemon process dies. Claude Code processes receive `EIO` on their next read/write to the slave PTY and terminate.

**Recovery**:
1. Health check detects session is dead (RWS `list_ptys()` fails or returns empty)
2. Reconnect flow deploys fresh RWS daemon
3. Creates new PTY, launches new Claude session
4. If the old Claude session ID still has a valid `.jsonl` file on remote, Claude resumes the conversation with `-r`

**Mitigation**: This is the same failure mode as screen crashing (which happens today). The key improvement: RWS is a single-purpose daemon with simple event loop — much less likely to crash than screen (which has decades of accumulated complexity).

**Additional defense**: Consider having the RWS daemon `fork()` a lightweight watchdog process that monitors the main daemon PID and restarts it on unexpected death, preserving PTY master_fds across the restart via `SCM_RIGHTS` unix socket. This is Phase 2+ complexity.

### 13.3 Forward tunnel dies while PTY is streaming

**Scenario**: The SSH forward tunnel (`ssh -L local:127.0.0.1:9741 host`) dies mid-stream. The browser is actively viewing the terminal.

**Impact**:
- `stream_remote_pty()` TCP socket gets EOF → `stream_closed` event fires
- Browser WebSocket gets closed → shows "Reconnecting..." overlay
- RWS daemon is unaffected (PTY keeps running, ringbuffer keeps recording)

**Recovery**:
1. WebSocket reconnect timer fires (1s, 2s, 5s... backoff)
2. `terminal_websocket()` sees remote session, tries `stream_remote_pty()`
3. Needs RWS connection → `get_remote_worker_server()` detects dead tunnel
4. Background thread starts new tunnel + reconnects command socket
5. On next WebSocket attempt, RWS is available → new PTY stream connection
6. Ringbuffer replay sends all output since disconnect → xterm.js catches up

**Gap to close**: `terminal_websocket()` currently routes all sessions through `stream_pane()` (tmux-based). Need to add routing logic:

```python
async def terminal_websocket(websocket, session_id):
    session = get_session(session_id)
    if session.rws_pty_id:
        # New architecture — stream via RWS
        await stream_remote_pty(websocket, session_id, session.rws_pty_id, session.host)
    else:
        # Legacy — stream via tmux pane
        await stream_pane(websocket, tmux_sess, tmux_win, session_id)
```

### 13.4 RWS version upgrade with active PTYs (deferred upgrade detail)

**Scenario**: Orchestrator is upgraded. New RWS script hash differs from running daemon. `rws.start()` detects version mismatch but PTYs are active.

**Current behavior**: `check_existing_daemon()` kills old daemon → all PTYs die.

**Required change to `check_existing_daemon()`**:

```python
def check_existing_daemon():
    # ... existing PID file + ping check ...

    if old_version != SCRIPT_VERSION:
        # NEW: Check if PTYs are active before killing
        try:
            s = socket.socket(...)
            s.connect((LISTEN_HOST, LISTEN_PORT))
            # ... handshake ...
            s.sendall(json.dumps({"action": "pty_list"}).encode() + b"\n")
            resp = ...
            ptys = json.loads(resp).get("ptys", [])
            active_ptys = [p for p in ptys if p["alive"]]

            if active_ptys:
                # PTYs are active — DO NOT upgrade, reuse old daemon
                log(f"Version mismatch but {len(active_ptys)} active PTYs, "
                    f"deferring upgrade")
                s.close()
                return old_pid  # Reuse old daemon

            s.close()
        except Exception:
            pass  # Can't check — fall through to kill

        # No active PTYs (or couldn't check) — safe to upgrade
        _kill_pid(old_pid)
        return None
```

**Edge case — deferred upgrade never happens**: If Claude sessions run continuously for days, the old daemon version persists. This is acceptable — the protocol is backward-compatible. The orchestrator client can check for missing actions and fall back:

```python
def exec_cmd(self, cmd, timeout=30):
    try:
        return self.execute({"action": "exec_cmd", "cmd": cmd, "timeout": timeout})
    except Exception as e:
        if "Unknown action" in str(e):
            # Old daemon doesn't support exec_cmd — fall back to SSH subprocess
            return _exec_via_ssh(self.host, cmd, timeout)
        raise
```

### 13.5 Two tunnels for the same host

**Scenario**: Two sessions on the same rdev host. Both need RWS access.

**Current design**: `_server_pool` is keyed by host. One `RemoteWorkerServer` per host, shared by all sessions on that host. The forward tunnel is shared.

**This is fine** — RWS daemon supports multiple PTY sessions. The `session_id` field on `pty_create` lets us distinguish which PTY belongs to which orchestrator session. `list_ptys()` returns all PTYs, and the orchestrator filters by `session_id`.

**Race condition to check**: Two sessions triggering reconnect simultaneously for the same host. Both call `get_remote_worker_server()` → both see stale server → both start background threads. The `_starting` dict prevents duplicate starts, so this is safe.

### 13.6 Claude exits normally → PTY cleanup

**Scenario**: User types `/exit` in Claude, or Claude finishes a task and exits. The PTY child process terminates.

**Current RWS behavior**: The main event loop's `is_child_alive()` check detects the dead child → `cleanup_pty()` removes it from `pty_sessions` and closes `master_fd`. Stream connections get EOF.

**What happens in the browser**:
1. `stream_remote_pty()` gets EOF on the TCP socket → sets `pty_exited = True`
2. Sends `{"type": "pty_exit"}` to browser WebSocket
3. Browser shows terminal is disconnected, suppresses reconnect attempts (`ptyExitedRef.current = true`)
4. `onExit` callback fires → parent component can handle (show "session ended" UI)

**This is correct.** Same behavior as interactive CLI exit today.

**Additional consideration**: Should the orchestrator detect Claude exit and update session status? Currently, health check loop would eventually notice. But with RWS, we can be more proactive — the `stream_remote_pty` handler already gets `pty_exit` events. On receiving this:

```python
if pty_exited:
    # Update session status in DB
    repo.update_session(conn, session.id, status="idle", rws_pty_id=None)
```

### 13.7 PTY ID mismatch after daemon restart

**Scenario**: Old daemon is killed (no active PTYs). New daemon starts. Orchestrator still has old `rws_pty_id` in session DB.

**Impact**: `stream_remote_pty()` calls `rws.connect_pty_stream(old_pty_id)` → RWS returns `{"error": "PTY not found"}` → WebSocket gets error.

**Recovery**: The reconnect flow should detect this:

```python
def _reconnect_rws_pty(session, rws):
    ptys = rws.list_ptys()
    our_pty = next((p for p in ptys if p["pty_id"] == session.rws_pty_id), None)

    if our_pty and our_pty["alive"]:
        # PTY still running — just reconnect stream
        return  # Browser will auto-reconnect WebSocket

    if our_pty and not our_pty["alive"]:
        # PTY exists but process exited — destroy and recreate
        rws.destroy_pty(session.rws_pty_id)

    # PTY gone (daemon restarted or PTY exited)
    # Clear stale pty_id, deploy files, create new PTY
    repo.update_session(conn, session.id, rws_pty_id=None)
    _deploy_files_and_launch_claude(session, rws, ...)
```

### 13.8 Health check with RWS unavailable

**Scenario**: RWS tunnel is down. Health check needs to determine if Claude is alive.

**Current approach**: `check_screen_and_claude_remote()` uses SSH subprocess directly — independent of RWS.

**New approach**: Primary check via RWS (`list_ptys`). Fallback to SSH subprocess:

```python
def check_worker_health_remote(session, rws=None):
    # Try RWS first (fast, reliable when tunnel is alive)
    if rws:
        try:
            ptys = rws.list_ptys()
            our_pty = next(
                (p for p in ptys if p["session_id"] == session.id),
                None
            )
            if our_pty and our_pty["alive"]:
                return "alive", "RWS PTY running"
            elif our_pty:
                return "dead", "RWS PTY process exited"
            else:
                return "dead", "No RWS PTY found for session"
        except Exception:
            pass  # RWS unavailable — fall through

    # Fallback: SSH subprocess check (works even without RWS tunnel)
    # Check if a Claude process with this session_id is running
    check_cmd = (
        f"ps aux | grep -v grep | grep -E 'claude (-r|--|--settings)' "
        f"| grep -q '{session.id}' && echo ALIVE || echo DEAD"
    )
    result = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
         session.host, check_cmd],
        capture_output=True, text=True, timeout=15,
    )
    if "ALIVE" in result.stdout:
        return "alive", "Claude process running (SSH check)"
    return "dead", "Claude process not found (SSH check)"
```

**Key insight**: The SSH subprocess fallback doesn't need screen detection anymore. It just checks if a Claude process with the session ID is running. This is simpler and more reliable than the current `check_screen_and_claude_remote()` with its SCREENDIR/SCREEN uppercase/socket vs process fallbacks.

### 13.9 Race: WebSocket connects before PTY is ready

**Scenario**: Browser opens terminal WebSocket immediately after session creation. The RWS PTY hasn't been created yet.

**Impact**: `stream_remote_pty()` calls `rws.connect_pty_stream(pty_id)` but `pty_id` is `None` (session doesn't have one yet).

**Fix**: `terminal_websocket()` should check for `rws_pty_id` being set. If not, send an error and let the browser retry:

```python
if session.rws_pty_id is None and is_remote_host(session.host):
    await websocket.send_json({
        "type": "error",
        "message": "Terminal not ready yet"
    })
    # Don't close — let the browser retry on reconnect timer
    await asyncio.sleep(2)
    # Re-check...
```

Or better: the WebSocket handler can poll for `rws_pty_id` with a timeout (similar to how `stream_pane` waits for initial resize before sending history).

### 13.10 Simultaneous setup and reconnect for same session

**Scenario**: Setup is in progress (creating RWS PTY). Health check fires, sees no screen session (old check), triggers reconnect.

**Current mitigation**: Per-session reconnect lock (`get_reconnect_lock`). Setup should also acquire this lock.

**Gap**: `setup_remote_worker()` currently doesn't acquire the reconnect lock. Need to add:

```python
def setup_remote_worker(...):
    lock = get_reconnect_lock(session_id)
    if not lock.acquire(timeout=5):
        return {"ok": False, "error": "Setup/reconnect already in progress"}
    try:
        # ... setup flow ...
    finally:
        lock.release()
```

### 13.11 Screen session leaks on migration

**Scenario**: Old worker has Claude in screen. Claude exits. Reconnect migrates to RWS PTY. The old screen session is still alive (empty, but consuming resources).

**Fix**: After successful migration, kill the orphaned screen session:

```python
def reconnect_remote_worker(...):
    # ... migration to RWS PTY ...

    # Clean up legacy screen session
    screen_name = get_screen_session_name(session.id)
    try:
        rws.exec_cmd(
            f"screen -ls 2>/dev/null | grep -w '{screen_name}' "
            f"| awk '{{print $1}}' "
            f"| while read sid; do screen -X -S \"$sid\" quit 2>/dev/null; done"
        )
    except Exception:
        pass  # Best-effort cleanup
```

Or simpler — let screen's idle timeout clean it up naturally (though GNU Screen doesn't have an idle timeout by default).

### 13.12 Browser disconnect during long ringbuffer replay

**Scenario**: PTY has 512KB of history in ringbuffer. On reconnect, the initial replay takes a moment. Browser closes WebSocket during replay.

**Impact**: `stream_remote_pty()` sends `initial_data` bytes, then starts background tasks. If WebSocket closes during `send_bytes(initial_data)`, the exception is caught and `stream_sock.close()` is called.

**Current code handles this correctly** — see `ws_terminal.py:595-600`:
```python
if initial_data:
    try:
        await websocket.send_bytes(initial_data)
    except Exception:
        stream_sock.close()
        return
```

The PTY is unaffected. Next reconnect will replay from the current ringbuffer state.

### 13.13 `pty_create` with shell command that needs login shell environment

**Scenario**: Claude Code needs PATH, VOLTA_HOME, and other env vars that are set in `.bashrc` / `.bash_profile`. When `pty_create` runs `os.execvp("/bin/bash", ["bash", "-l"])`, the login shell reads these.

**But wait** — the current `pty_create` starts `/bin/bash -l` (login shell), and Claude is launched by typing a command. In the new flow, we want to launch Claude directly: `pty_create(cmd="claude --settings ...")`.

**Problem**: If `cmd` is set to the claude command directly, `os.execvp("claude", [...])` won't have the login shell environment (PATH, etc.).

**Fix**: The `pty_create` implementation should wrap custom commands in a login shell:

```python
if shell_cmd != "/bin/bash":
    # Wrap in login shell to get proper environment
    os.execvp("/bin/bash", ["bash", "-l", "-c", shell_cmd])
else:
    os.execvp("/bin/bash", ["bash", "-l"])
```

Or better — launch `/bin/bash -l` and have the shell execute the command:

```python
# In pty_create handler:
if cmd != "/bin/bash":
    shell_cmd = f'/bin/bash -l -c {shlex.quote(cmd)}'
```

Combined with the `env` parameter, the full environment is: login shell defaults + explicit env overrides.

### 13.14 Terminal resize race on initial connect

**Scenario**: Browser opens WebSocket, sends resize. For RWS path, the resize needs to be forwarded to the RWS PTY stream. But the stream connection hasn't been established yet (we're still in handshake).

**Current behavior for interactive CLI**: `stream_remote_pty()` establishes the PTY stream first, sends initial_data, then enters the main loop that handles resize messages. The first resize from the browser arrives in the main loop after the stream is connected. This is correct.

**For the main terminal**: Same flow applies. No gap.

### 13.15 Multiple browser tabs viewing the same remote session

**Scenario**: User opens the same session in two browser tabs. Both connect WebSocket → both call `stream_remote_pty()` → both open PTY stream connections to RWS.

**Current RWS behavior**: Multiple stream connections per PTY are supported. Each connection in `session.stream_conns` receives all PTY output bytes. Both tabs see the same terminal.

**Input conflict**: Both tabs can send input. Keystrokes from both tabs arrive at `os.write(master_fd)`. This is the same as two terminals attached to the same tmux pane — the last writer wins, which can cause interleaved input.

**This is acceptable** — same behavior as tmux today. Users shouldn't have two tabs controlling the same session.

### 13.16 RWS daemon inactivity timeout with active PTY

**Current code** (`run_server()` main loop):
```python
if (time.time() - last_activity > INACTIVITY_TIMEOUT
        and not pty_sessions
        and not browser_processes
        and not command_conns
        and not pty_stream_conns):
    break  # Shutdown
```

**This is safe** — the daemon only shuts down when `pty_sessions` is empty. As long as a Claude PTY is running, the daemon stays alive. No change needed.

### 13.17 SSH reconnect → forward tunnel on different local port

**Scenario**: SSH reconnect creates a new forward tunnel. The new tunnel may bind to a different local port (if the old port is still in TIME_WAIT).

**Impact**: `RemoteWorkerServer._local_port` is set during `_start_tunnel()` and used by `connect_pty_stream()`. If the tunnel reconnects to a different port, the old `_local_port` is stale.

**Current handling**: `reconnect_tunnel()` calls `_start_tunnel()` which finds a free port. Then `_connect_command_socket()` uses the new port. The `stream_remote_pty()` call goes through `rws.connect_pty_stream()` which reads `_local_port` at call time.

**Timing gap**: Between tunnel reconnect and the next `connect_pty_stream()` call, any in-flight stream connection on the old port will break. This is fine — the TCP socket will get EOF, triggering reconnect.

## 14. Summary: Connect and Reconnect Flows (Final Design)

### New Session Connect Flow

```
1. User creates session (API: POST /sessions)
2. Backend: setup_remote_worker()
   a. Start reverse SSH tunnel (for API callbacks)
   b. Start SSH forward tunnel (for RWS)
   c. Deploy/ensure RWS daemon (via SSH bootstrap)
      - check_existing_daemon() reuses or deploys
      - Deferred upgrade if PTYs active
   d. Deploy files via RWS (write_file, upload_tar)
   e. Pre-setup via RWS exec_cmd (node, playwright)
   f. Create PTY: rws.create_pty(cmd=claude_cmd, env=..., cwd=work_dir)
   g. Store rws_pty_id on session DB record
   h. Session status → "working"
3. Browser opens /ws/terminal/{sessionId}
4. terminal_websocket() detects rws_pty_id → stream_remote_pty()
5. Ringbuffer replay → live streaming → scrollback works
```

### Reconnect Flow (new-architecture session)

```
1. Health check detects issue (tunnel dead, stream EOF, etc.)
   OR user clicks Reconnect
2. reconnect_remote_worker():
   a. Acquire per-session lock
   b. Ensure SSH forward tunnel alive → rws tunnel reconnect if needed
   c. Ensure reverse tunnel alive → restart if needed
   d. Check PTY via rws.list_ptys():
      - PTY alive → done! Update status, browser auto-reconnects
      - PTY dead → deploy files, create new PTY, launch Claude
   e. Release lock
3. Browser WebSocket auto-reconnects → stream_remote_pty()
4. Ringbuffer replay catches up → seamless experience
```

### Reconnect Flow (legacy screen session — migration path)

```
1. Health check detects issue on session with rws_pty_id=NULL
2. reconnect_remote_worker():
   a. Acquire lock
   b. Ensure tunnels
   c. No rws_pty_id → legacy path
   d. Check screen + Claude via SSH subprocess:
      - Screen + Claude alive → reattach screen (existing flow)
        (DON'T migrate running session)
      - Claude dead → MIGRATION OPPORTUNITY:
        1. Deploy files via RWS
        2. Create PTY via rws.create_pty()
        3. Set rws_pty_id on session
        4. Kill orphaned screen session (best-effort)
        5. From now on, this session is new-architecture
   e. Release lock
```

### Health Check Flow (unified)

```
def check_worker_health_remote(session):
    if session.rws_pty_id:
        # New architecture: check via RWS
        rws = get_rws_or_none(session.host)
        if rws:
            ptys = rws.list_ptys()
            pty = find_by_id(ptys, session.rws_pty_id)
            if pty and pty.alive: return "alive"
            if pty and not pty.alive: return "dead"  # process exited
            return "dead"  # PTY gone (daemon restarted?)
        # RWS unavailable — fallback to SSH process check
        return check_claude_via_ssh(session)
    else:
        # Legacy architecture: existing screen check
        return check_screen_and_claude_remote(session)
```
