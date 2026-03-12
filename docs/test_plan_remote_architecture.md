# Test Plan: Remote Terminal Architecture (RWS PTY)

**Target worker**: `premium-upsell-openconnect_quirky-eagle`
**Session ID**: `7d43efdf-7060-4e48-a5bb-97a22155894a`
**Host**: `premium-upsell-openconnect/quirky-eagle`

## Overview

The architecture rewrite replaces GNU Screen + tmux send_keys with RWS (Remote Worker Server) PTY management. Key changes:

1. Claude runs in RWS PTY (not Screen) — survives SSH drops, has ringbuffer
2. Terminal streaming via `stream_remote_pty()` (not `stream_pane()`)
3. Scrollback enabled for remote sessions (ringbuffer replay)
4. Simplified reconnect: tunnel alive? PTY alive? (not 12-scenario screen flow)
5. Health check via RWS `pty_list` (not SSH+screen detection)
6. Setup via direct SSH subprocess commands (not tmux send_keys)

---

## 1. RWS Daemon & PTY Core

### TC-1: RWS Daemon Connectivity
**What**: RWS daemon on remote host is running and reachable via forward tunnel.
**How**: Send `ping` action via RWS client.
**Pass**: Returns `{"status": "pong"}` within timeout.
**Fail**: Connection timeout, refused, or no response.
**Code**: `remote_worker_server.py` — `RemoteWorkerServer.execute({"action": "ping"})`

### TC-2: PTY Status Check (pty_list)
**What**: RWS daemon lists PTY sessions with metadata.
**How**: Send `pty_list` action, verify our session's PTY appears.
**Pass**: Returns list with pty_id, alive status, session_id, uptime, cmd.
**Fail**: Error response, or PTY missing when session has rws_pty_id.

### TC-3: PTY Output Capture
**What**: Capture terminal output from the PTY's ringbuffer.
**How**: Send `pty_capture` action for the active PTY.
**Pass**: Returns data (may be 0 chars if idle, but no error).
**Fail**: Error response or connection failure.

### TC-3a: PTY Capture — Output Quality (ANSI Stripping)
**What**: Captured PTY output is clean, human-readable text with no raw escape sequences.
**How**: Call `capture_pty()` via RWS client and inspect the returned text.
**Pass**: Output contains only readable text. No raw escape sequences: no `ESC[` (CSI), no `ESC]` (OSC), no `[?2026l`/`[?2026h` (synchronized output), no `[0m`/`[32m` (color codes), no bare `\r` carriage returns.
**Fail**: Output contains visible escape sequence fragments like `[?2026l][?2026h]`, color codes like `[32m`, or OSC sequences like `]0;title`.
**Code**: `remote_worker_server.py:capture_pty()` — client-side `_ANSI_RE` regex strips CSI/OSC/simple escapes. Daemon-side `handle_pty_capture` also strips. Defense-in-depth: `sessions.py:_strip_terminal_noise()` strips again for preview paths.
**Why this matters**: Worker card previews, brain sync prompts, and interactive CLI capture all display this text in non-terminal contexts (HTML, LLM prompts). Raw escape sequences appear as garbage characters.

### TC-4: PTY Resize
**What**: Resize command changes PTY dimensions.
**How**: Send `pty_resize` with new cols/rows, verify via `pty_list`.
**Pass**: Dimensions updated (e.g., 128x46 → 100x30).
**Fail**: Resize ignored or error returned.

### TC-5: PTY Stream — Ringbuffer Replay
**What**: Connecting to `pty_stream` replays ringbuffer history before live output.
**How**: Open TCP connection with `{"type": "pty_stream", "pty_id": "..."}` handshake.
**Pass**: Receives initial bytes (ringbuffer content) + continues with live stream.
**Fail**: No initial data, or connection rejected.
**Constraint**: Ringbuffer is 512KB (`RINGBUFFER_MAX = 524288`).

### TC-6: PTY Destroy
**What**: `pty_destroy` terminates the PTY and child process.
**How**: Destroy a PTY, verify it disappears from `pty_list`.
**Pass**: PTY removed, master_fd closed, stream connections get EOF.
**Fail**: PTY still appears as alive, or orphaned process.

### TC-7: RWS Daemon Version Check
**What**: Daemon version matches local script hash.
**How**: `server_info` action, compare version to `_SCRIPT_HASH`.
**Pass**: Versions match.
**Fail**: Mismatch → deferred upgrade or redeploy needed.
**Code**: `remote_worker_server.py:_SCRIPT_HASH`

---

## 2. Tunnel Infrastructure

### TC-8: Reverse Tunnel Health
**What**: SSH -R tunnel (remote:8093 → local:8093) is alive and functional.
**How**: Check PID alive via `tunnel_manager.is_alive()`, then active probe via `probe_tunnel_connectivity()` (SSH + curl from remote).
**Pass**: Process alive AND remote can reach local API.
**Fail**: PID dead, or remote curl returns non-200.
**Code**: `tunnel.py:ReverseTunnelManager`, `health.py:probe_tunnel_connectivity()`

### TC-9: Forward Tunnel Health
**What**: SSH -L tunnel (local:port → remote:9741) connects to RWS daemon.
**How**: Send `ping` through the RWS client over the tunnel.
**Pass**: Gets `pong` response.
**Fail**: Connection refused or timeout.

### TC-10: Reverse Tunnel Restart
**What**: Tunnel manager restarts a dead reverse tunnel.
**How**: Kill tunnel PID, call `restart_tunnel()`.
**Pass**: New PID, new SSH process, connectivity restored.
**Fail**: Restart fails (port binding conflict, SSH error).
**Edge case**: Stale TCP binding on remote — port 8093 may remain bound for 30-60s after tunnel death. Retry logic should handle this.

### TC-11: Forward Tunnel Reconnect
**What**: RWS client reconnects forward tunnel after it dies.
**How**: Kill -L tunnel process, trigger any RWS operation.
**Pass**: New tunnel established, RWS commands resume.
**Fail**: Operations fail with connection errors.
**Code**: `remote_worker_server.py:reconnect_tunnel()`

### TC-12: Tunnel PID Adoption on Restart
**What**: `recover_tunnel()` adopts existing tunnel processes after orchestrator restart.
**How**: Restart orchestrator, verify it finds and adopts stored tunnel PIDs.
**Pass**: Existing tunnel reused (no new SSH process spawned).
**Fail**: Orphaned tunnel killed unnecessarily, or wrong PID adopted (PID recycling).
**Code**: `tunnel.py:_try_adopt()`

---

## 3. File Operations via RWS

### TC-13: check_path
**What**: Verify existence of critical paths on remote.
**How**: Send `check_path` with paths: configs/settings.json, bin/lib.sh, prompt.md, hooks dir.
**Pass**: All paths present, `missing_count=0`.
**Fail**: Missing files that should have been deployed.

### TC-14: list_dir
**What**: Directory listing with git status.
**How**: Send `list_dir` for work_dir with depth=1.
**Pass**: Returns entries with name, is_dir, size, mtime; git_available=true.
**Fail**: Error or empty listing for populated directory.

### TC-15: read_file
**What**: Read a text file on remote via RWS.
**How**: Send `read_file` for configs/settings.json.
**Pass**: Returns valid JSON content with expected keys.
**Fail**: Error, empty content, or truncated.

### TC-16: File Explorer API for Remote Sessions
**What**: `GET /api/sessions/{id}/files` returns remote file listing.
**How**: Call files endpoint for RWS PTY session.
**Pass**: Returns file tree from remote work_dir.
**Fail**: Returns empty or errors because API doesn't route to RWS.
**Code**: File explorer panel uses this API; must work for `isRemote` sessions.

---

## 4. Session Lifecycle

### TC-17: Session Creation — Remote Worker Setup
**What**: Full `setup_remote_worker()` pipeline: reverse tunnel → file deploy → RWS daemon → PTY creation → verify alive.
**How**: Create a new remote session via `POST /api/sessions`.
**Pass**: Session created with `rws_pty_id` set, status transitions connecting → working, PTY alive with Claude running.
**Fail**: Stuck in connecting, error status, or rws_pty_id=null.
**Verification**:
  - DB has rws_pty_id set
  - PTY alive in pty_list
  - PTY cmd contains `claude` with `--settings` and session args
  - No `screen -S` in PTY command
  - Login shell wrapping (`bash -l -c`) ensures proper PATH/env
  - Files deployed to `/tmp/orchestrator/workers/{name}/`

### TC-18: Session Creation — Claude Resume vs New
**What**: Correct Claude session argument selection.
**How**: Create session where `.jsonl` exists on remote vs doesn't.
**Pass**: Uses `-r {session_id}` when session file exists, `--session-id` when new.
**Fail**: Uses `-c` (could resume wrong session on shared host), or always creates new.
**Code**: `session.py:_build_claude_command()`, `reconnect.py:_check_claude_session_exists_remote()`

### TC-19: Session Creation — Environment Variables
**What**: PTY created with correct environment (PATH, PLAYWRIGHT_MCP_CDP_ENDPOINT, etc.).
**How**: Check PTY's env via `pty_list` cmd field or by examining process environment.
**Pass**: volta/node in PATH, Playwright CDP endpoint set, custom env vars present.
**Fail**: Missing PATH entries → Claude can't find node/tools.
**Code**: `session.py:_build_claude_command()` — login shell wrapping

### TC-20: Session Deletion — RWS PTY Cleanup
**What**: `DELETE /api/sessions/{id}` destroys RWS PTY, stops tunnels, cleans up.
**How**: Delete an active RWS PTY session.
**Pass**: PTY destroyed (not in pty_list), reverse tunnel stopped, interactive CLI closed, browser stopped, tmux window killed, local files removed.
**Fail**: Orphaned PTY, orphaned tunnel process, or RWS daemon left running.
**Code**: `sessions.py:_delete_session_inner()` — branches on `is_remote and s.rws_pty_id`

### TC-21: Session Deletion — Reconnect Lock Cleanup
**What**: Deleting a session cleans up its reconnect lock.
**How**: Delete session, verify lock no longer in registry.
**Pass**: `cleanup_reconnect_lock()` called, no leaked lock.
**Fail**: Lock stays in registry, could interfere with session ID reuse.

### TC-22: Work Dir Detection
**What**: Remote worker's work_dir is detected asynchronously after setup.
**How**: Create session without explicit work_dir, wait 5s, check session.
**Pass**: `work_dir` populated in DB (detected from remote filesystem).
**Fail**: work_dir stays null; file explorer and brain sync can't locate project.
**Code**: `reconnect.py` — async work_dir detection after 3s delay

---

## 5. Terminal WebSocket

### TC-23: WebSocket Routing — Remote PTY
**What**: WebSocket connects to `/ws/terminal/{session_id}`, routes to `stream_remote_pty()` for RWS PTY sessions.
**How**: Connect WebSocket for an RWS PTY session.
**Pass**: Receives binary frames (PTY output), ringbuffer replay on connect.
**Fail**: Falls back to tmux `stream_pane()` or errors.
**Code**: `ws_terminal.py:terminal_websocket()` — checks `rws_pty_id and is_remote_host(host)`

### TC-24: WebSocket Input Relay
**What**: User keystrokes sent via WebSocket reach Claude in the remote PTY.
**How**: Send `{"type": "input", "data": "echo hello\r"}` through WS.
**Pass**: Output appears in PTY capture.
**Fail**: Input not relayed, or goes to wrong destination.

### TC-25: WebSocket Resize
**What**: Resize events from browser reach remote PTY.
**How**: Send `{"type": "resize", "cols": 100, "rows": 30}`.
**Pass**: PTY dimensions updated (verifiable via pty_list).
**Fail**: Resize ignored.

### TC-26: WebSocket — PTY Exit Notification
**What**: When Claude exits in PTY, WebSocket receives `pty_exit` and DB is updated.
**How**: Let Claude exit (or destroy PTY), observe WebSocket behavior.
**Pass**: Browser receives `{"type": "pty_exit"}`, session's `rws_pty_id` set to null, status → idle.
**Fail**: Browser keeps trying to reconnect indefinitely, or rws_pty_id stays stale.
**Code**: `ws_terminal.py:stream_remote_pty()` — EOF detection → `repo.update_session(rws_pty_id=None, status="idle")`

### TC-27: WebSocket Reconnect After Forward Tunnel Drop
**What**: Forward tunnel dies mid-stream, WebSocket auto-reconnects.
**How**: Kill -L tunnel process while terminal is open in browser.
**Pass**: Browser shows "Reconnecting..." overlay, tunnel restarted, stream resumes with ringbuffer replay filling the gap.
**Fail**: Permanent disconnect, or output gap (missing what Claude produced during disconnect).

### TC-28: Multiple Browser Tabs Same Session
**What**: Two browser tabs viewing the same remote session.
**How**: Open session in two tabs simultaneously.
**Pass**: Both receive PTY output (RWS supports multiple stream_conns per PTY). Last writer wins for input.
**Fail**: Second tab blocked, or output only goes to one tab.

---

## 6. Health Checks

### TC-29: Health Check — RWS PTY Alive
**What**: Health check correctly reports alive for running RWS PTY.
**How**: `POST /api/sessions/{id}/health-check` on healthy session.
**Pass**: Returns `alive=true`, `reason="RWS PTY alive"`, `tunnel_alive=true`.
**Fail**: Returns false, or falls back to screen-based check.

### TC-30: Health Check — RWS PTY Dead
**What**: Health check detects dead PTY and marks session disconnected.
**How**: Kill Claude process in PTY (or destroy PTY), trigger health check.
**Pass**: Returns `alive=false`, `status="disconnected"`, `needs_reconnect=true`, `rws_pty_id` cleared.
**Fail**: Still reports alive, or doesn't clear rws_pty_id.

### TC-31: Health Check — Tunnel Dead, PTY Alive
**What**: Dead reverse tunnel doesn't kill the session if PTY is alive.
**How**: Kill reverse tunnel, trigger health check.
**Pass**: `alive=true`, `tunnel_alive=false`, tunnel_reconnected=true (if restart succeeds).
**Fail**: Session marked dead/disconnected just because tunnel is down.
**Behavioral change**: In old architecture, dead tunnel → screen_detached. In new architecture, dead tunnel + alive PTY → still alive.

### TC-32: Health Check — SSH Fallback
**What**: When RWS daemon is unreachable, health check falls back to SSH subprocess.
**How**: Kill forward tunnel (so RWS client can't connect), trigger health check.
**Pass**: SSH fallback detects Claude process via `ps aux | grep claude`, returns alive.
**Fail**: Immediately marks session dead without trying SSH fallback.
**Code**: `health.py:_check_rws_pty_health()` tier 3

### TC-33: Health Check — Tmp Dir Recovery
**What**: Health check detects and regenerates missing tmp dir files on remote.
**How**: Delete a file from remote `/tmp/orchestrator/workers/{name}/`, trigger health check.
**Pass**: Missing file re-deployed via SCP during health check.
**Fail**: Missing file not detected, or regeneration fails silently.
**Code**: `health.py:ensure_tmp_dir_health()` + manifest verification

### TC-34: Health Check — Status Recovery
**What**: Session in disconnected/error state recovers to "waiting" when PTY is alive.
**How**: Manually set session to "disconnected", trigger health check while PTY is actually alive.
**Pass**: Status updated to "waiting", DB updated.
**Fail**: Stays disconnected despite alive PTY.

### TC-35: Health Check All — Batch Processing
**What**: `check_all_workers_health()` processes multiple remote sessions correctly.
**How**: Have 2+ remote sessions, trigger `/api/sessions/health-check-all`.
**Pass**: All sessions checked, auto_reconnect candidates processed, deferred if user active.
**Fail**: Health check blocks on one slow session, or misses sessions.

---

## 7. Reconnect Scenarios

### TC-36: Reconnect — PTY Still Alive (No-Op)
**What**: Reconnect on healthy session is a no-op.
**How**: Trigger reconnect when tunnel + PTY are alive.
**Pass**: PTY ID unchanged, status → waiting, no new PTY created.
**Fail**: Creates unnecessary new PTY, or crashes.

### TC-37: Reconnect — PTY Dead, Create New
**What**: PTY died, reconnect creates new PTY with Claude.
**How**: Destroy PTY via `pty_destroy`, trigger reconnect.
**Pass**: New PTY created, rws_pty_id updated, Claude starts with `-r` (resume).
**Fail**: Stuck in connecting, or rws_pty_id stays null.

### TC-38: Reconnect — Full Tunnel + PTY Recovery
**What**: Both reverse tunnel and PTY are dead.
**How**: Kill tunnel PID + destroy PTY, trigger reconnect.
**Pass**: New tunnel + new PTY created, session fully recovered.
**Fail**: Partial recovery (tunnel OK but PTY fails, or vice versa).

### TC-39: Reconnect — Concurrent Prevention
**What**: Per-session lock prevents concurrent reconnects.
**How**: Trigger reconnect twice simultaneously on same session.
**Pass**: Second attempt detects lock, returns early.
**Fail**: Two reconnects run in parallel, creating duplicate PTYs.
**Code**: `reconnect.py:get_reconnect_lock()`

### TC-40: Reconnect — RWS Daemon Version Mismatch
**What**: Reconnect detects outdated RWS daemon and handles gracefully.
**How**: Deploy daemon, then update `_SCRIPT_HASH` locally, trigger reconnect.
**Pass**: If no active PTYs → redeploy daemon. If active PTYs → defer upgrade, reuse old daemon.
**Fail**: Kills daemon with active PTYs, losing Claude sessions.
**Code**: `reconnect.py:_reconnect_rws_for_host()` version check

### TC-41: Reconnect — Claude Startup Failure Retry
**What**: If Claude fails to start in new PTY, reconnect retries with fresh session.
**How**: Cause Claude startup failure (e.g., corrupted session file).
**Pass**: First attempt fails → cleanup stale session → retry with `--session-id` (new conversation) → succeeds.
**Fail**: Stuck in error, no retry logic, or orphaned dead PTY.
**Code**: `reconnect.py:_reconnect_rws_pty_worker()` — verify-after-3s → cleanup → retry

### TC-42: Reconnect — Config Redeployment
**What**: Reconnect redeploys configs before creating new PTY.
**How**: Delete remote config files, trigger reconnect.
**Pass**: Configs regenerated locally + SCP'd to remote before PTY creation.
**Fail**: PTY created with missing configs → Claude fails to start.

---

## 8. Brain / Session API Interaction with Remote Workers

These endpoints use tmux operations to interact with workers. For RWS PTY sessions there is **no active tmux pane** — these must route through the RWS daemon instead.

### TC-43: Brain Sync — Terminal Capture
**What**: `POST /brain/sync` captures each active worker's terminal preview.
**How**: Start brain, have active RWS PTY worker, call `/brain/sync`.
**Pass**: Brain receives readable terminal content from remote PTY via `rws.capture_pty()`. No ANSI garbage in the LLM prompt.
**Fail**: Returns "(could not capture terminal)", or returns ANSI-contaminated text.
**Code**: `brain.py:235-238` — branches on `is_remote_host(s.host) and s.rws_pty_id`, calls `rws.capture_pty()` which strips ANSI.
**Status**: **FIXED** — RWS path + ANSI stripping.

### TC-44a: Session Preview — Single Session
**What**: `GET /sessions/{id}/preview` returns terminal snapshot.
**How**: Call preview endpoint for RWS PTY session.
**Pass**: Returns non-empty PTY content via RWS capture. Content is human-readable (no escape sequence garbage).
**Fail**: Returns empty string, or returns text with visible ANSI fragments like `[?2026l]`.
**Code**: `sessions.py:_capture_preview()` → `_capture_rws_pty()` → `rws.capture_pty()` (strips ANSI) → `_strip_terminal_noise()` (defense-in-depth).
**Status**: **FIXED** — RWS path implemented + ANSI stripping.

### TC-44b: Worker Card Preview — Session List
**What**: `GET /api/sessions?include_preview=true` returns terminal preview for each session, used by WorkerCard on the workers page.
**How**: Load workers page, check that remote worker cards show terminal content. Inspect actual text for readability.
**Pass**: Worker card shows last ~20 lines of **readable** terminal output. No escape sequence fragments, no `[?2026l][?2026h]`, no `[0m` color remnants.
**Fail**: Shows garbage like `[?2026l][?2026h]`, or shows "No terminal output yet...".
**Code**: `sessions.py` — iterates sessions, calls `_capture_preview()` → `_capture_rws_pty()` with ANSI stripping.
**Status**: **FIXED** — routing + ANSI stripping.

### TC-44c: Task Worker Preview — Task Detail Page
**What**: TaskWorkerPreview component fetches `GET /api/sessions/{id}/preview` every 5 seconds on the task detail page.
**How**: Open task detail page for a task assigned to a remote worker. Verify preview text is readable.
**Pass**: Worker preview card shows last ~15 lines of readable terminal output, updated every 5s. No escape sequence garbage.
**Fail**: Shows "No terminal output yet..." or shows ANSI garbage.
**Code**: `TaskWorkerPreview.tsx:27` — polls `/api/sessions/{id}/preview`.
**Status**: **FIXED** — same code path as TC-44a.

### TC-44d: Brain Sync Preview — Clean Output
**What**: Brain sync endpoint captures terminal preview for each active worker to include in the LLM monitoring prompt.
**How**: Trigger brain sync (`POST /api/brain/sync`), inspect the preview text embedded in the prompt.
**Pass**: Preview text in brain prompt is readable. No ANSI escape fragments that would confuse the LLM.
**Fail**: Brain prompt contains raw terminal sequences like `\x1b[?2026h` or visible `[32m` color codes.
**Code**: `brain.py:238` — calls `rws.capture_pty()` which now strips ANSI at the method level.

### TC-44e: Interactive CLI Capture — Clean Output
**What**: Interactive CLI capture (`capture_interactive_cli()`) returns clean text for RWS-backed CLIs.
**How**: Capture output from a remote interactive CLI session.
**Pass**: Returned text is readable, no escape sequences.
**Fail**: Raw ANSI sequences in captured output.
**Code**: `interactive.py:239-248` — calls `rws.capture_pty()` which strips ANSI.

### TC-45: Send Message to Remote Worker
**What**: `POST /sessions/{id}/send` delivers message to Claude.
**How**: Send message to RWS PTY session.
**Pass**: Message written to PTY input, Claude processes it.
**Fail**: Goes to empty tmux pane, Claude never sees it.
**Code**: `sessions.py` — routes to `_write_to_rws_pty()` for RWS PTY sessions.
**Status**: **FIXED** — RWS routing implemented.

### TC-46: Type Text to Remote Worker
**What**: `POST /sessions/{id}/type` injects text without Enter.
**How**: Call type endpoint for RWS PTY session.
**Pass**: Text appears in PTY input buffer.
**Fail**: Lost to empty tmux pane.
**Code**: `sessions.py` — routes to `_write_to_rws_pty()` (no newline appended).
**Status**: **FIXED** — RWS routing implemented.

### TC-47: Paste to Pane — Remote Worker
**What**: `POST /sessions/{id}/paste-to-pane` bracketed paste.
**How**: Call paste-to-pane for RWS PTY session.
**Pass**: Text delivered to PTY with bracketed paste wrapping.
**Fail**: Lost to tmux pane.
**Status**: **FIXED** — RWS routing with `\x1b[200~...\x1b[201~` wrapping.

### TC-48: Pause Remote Worker
**What**: `POST /sessions/{id}/pause` sends Escape to stop Claude.
**How**: Call pause on RWS PTY session.
**Pass**: Escape byte (`\x1b`) written to PTY, Claude pauses.
**Fail**: Escape goes to tmux pane, Claude keeps running.
**Status**: **FIXED** — RWS routing implemented.

### TC-49: Continue Remote Worker
**What**: `POST /sessions/{id}/continue` resumes paused worker.
**How**: Call continue on paused RWS PTY session.
**Pass**: "continue\n" written to PTY.
**Fail**: Goes to tmux pane, worker stays paused.
**Status**: **FIXED** — RWS routing implemented.

### TC-50: Stop Remote Worker
**What**: `POST /sessions/{id}/stop` sends Escape + /clear.
**How**: Call stop on active RWS PTY session.
**Pass**: Two writes: `\x1b` then `/clear\n` reach PTY.
**Fail**: Commands go to tmux, Claude keeps working.
**Status**: **FIXED** — RWS routing implemented.

### TC-51: Prepare for Task — Remote Worker
**What**: `POST /sessions/{id}/prepare-for-task` interrupts and clears before new task.
**How**: Call prepare-for-task on active RWS PTY session.
**Pass**: Three writes: `\x1b`, `\x03`, `/clear\n` reach PTY.
**Fail**: Commands go to tmux, Claude still working on previous task.
**Status**: **FIXED** — RWS routing implemented.

### TC-52: Paste Image to Remote Worker
**What**: `POST /sessions/{id}/paste-image` saves and syncs image file.
**How**: Paste base64 image to RWS PTY session.
**Pass**: Image saved locally, SCP'd to remote, path accessible from PTY.
**Fail**: File sync fails, or path not accessible.
**Code**: Uses `sync_file_to_remote()` (SSH scp) — should work independently of tmux.

---

## 9. UI — Frontend Behavior

### TC-53: Scrollback Enabled for Remote Sessions
**What**: Browser terminal has scrollback history for RWS PTY sessions.
**How**: Open terminal for remote worker, scroll up.
**Pass**: Can scroll through past output (up to xterm.js scrollback limit). `disableScrollback` is false.
**Fail**: Scrollback disabled (old behavior: `disableScrollback={isRemote}`).
**Code**: `SessionDetailPage.tsx` — should be `disableScrollback={isRemote && !session.rws_pty_id}` or just `false`.

### TC-54: No Screen Copy-Mode Hint Banner
**What**: The "Ctrl+A [" hint banner is NOT shown for RWS PTY sessions.
**How**: Open terminal for remote RWS PTY worker.
**Pass**: No hint banner visible.
**Fail**: Banner still shows (legacy screen UI not removed).
**Code**: `SessionDetailPage.tsx` — banner should be conditionally hidden.

### TC-55: Status Badge — All States
**What**: Status badges display correctly for all remote session states.
**How**: Trigger each state: idle, connecting, working, waiting, paused, disconnected, error, screen_detached.
**Pass**: Correct color and text for each state. Pulsing animation on "working" only.
**Fail**: Wrong color, missing state, or stale badge after transition.

### TC-56: Action Buttons — Disconnected State
**What**: Reconnect button shown when disconnected/error/screen_detached.
**How**: Set session to disconnected, check UI.
**Pass**: Single "Reconnect" button visible. Pause/Stop hidden.
**Fail**: Wrong buttons shown, or reconnect button missing.

### TC-57: Action Buttons — Connected State
**What**: Pause/Continue, Stop, Check Progress buttons shown when connected.
**How**: Have active working session, check UI.
**Pass**: All action buttons visible and enabled. Disabled when idle.
**Fail**: Buttons missing or wrongly enabled/disabled.

### TC-58: Tunnel Port Badges
**What**: Remote sessions show port forwarding links (localhost:PORT).
**How**: Open session detail for remote worker with tunnels.
**Pass**: Tunnel badges visible, updated every 10s from `/api/sessions/{id}/tunnels`.
**Fail**: Missing badges, or shown for local sessions.

### TC-59: Type Tags — rdev / ssh
**What**: Purple type tags shown for remote sessions.
**How**: View session list and detail page.
**Pass**: "rdev" tag for host with `/`, "ssh" tag for other non-localhost hosts.
**Fail**: Missing tags or wrong classification.

### TC-60: Auto-Reconnect Toggle
**What**: Toggle switch controls auto-reconnect behavior.
**How**: Toggle auto-reconnect off, disconnect session, verify no auto-reconnect.
**Pass**: Toggle persists to DB, health check skips auto-reconnect when disabled.
**Fail**: Toggle doesn't persist, or auto-reconnect ignores the setting.

### TC-61: Terminal Reconnect Overlay
**What**: Browser shows reconnect overlay with countdown when connection drops.
**How**: Kill forward tunnel while viewing terminal.
**Pass**: "Reconnecting in Xs..." overlay with countdown (1s → 2s → 5s → 10s backoff), manual retry button.
**Fail**: No overlay, or stuck reconnecting forever (max 5 attempts).

### TC-62: Worker Card — Compact Display
**What**: Worker card on workers page shows correct info for remote sessions.
**How**: View workers page with remote sessions.
**Pass**: Status dot (correct color, pulsing if working), type tag, status badge, action buttons, tunnel badges.
**Fail**: Missing or incorrect info.

### TC-62a: Worker Card Compact — Preview for Remote Workers
**What**: Compact worker cards (dashboard) show terminal preview for remote sessions.
**How**: Load dashboard page, check compact cards for remote workers. Verify text readability.
**Pass**: Last 8 lines of **readable** terminal output from RWS PTY shown in preview area. No ANSI garbage.
**Fail**: Shows "No output yet..." or shows escape sequence fragments.
**Code**: `WorkerCardCompact.tsx:15` — `session.preview.split('\n').slice(-8)`. Backend: `_capture_preview()` with ANSI stripping.
**Status**: **FIXED** — same code path as TC-44a/b.

### TC-62b: Dashboard — allRdev Badge Suppression
**What**: When all workers are rdev-hosted, the "rdev" type badge is suppressed (adds no info).
**How**: Have only rdev workers, view dashboard.
**Pass**: No "rdev" badges on compact cards. If mix of rdev + local/ssh, badges shown.
**Fail**: Always shows badges, or never shows them.
**Code**: `DashboardPage.tsx:50` — `const allRdev = workers.every(w => w.host.includes('/'))`; `WorkerCardCompact.tsx:36` — `{!allRdev && session.host.includes('/') && ...}`

### TC-62c: Add Session Modal — rdev Instance Selection
**What**: "Add Worker" modal shows available rdev instances and allows creating remote workers.
**How**: Open Add Worker modal, select "rdev" type.
**Pass**: Lists rdev instances with state/cluster, marks in-use instances, allows selection, creates remote session with correct host format (`project/instance`).
**Fail**: Empty rdev list, or session created with wrong host format.
**Code**: `AddSessionModal.tsx` — rdev tab with instance list from `/api/rdev/instances`

### TC-62d: Add Session Modal — SSH Host Input
**What**: SSH worker creation accepts free-form hostname.
**How**: Open Add Worker modal, select "ssh" type, enter hostname.
**Pass**: Session created with provided hostname, setup starts normally.
**Fail**: Hostname validation rejects valid hosts, or doesn't validate at all.

### TC-62e: File Explorer Panel — Connecting Skeleton State
**What**: File explorer shows "Connecting to remote host..." skeleton while RWS is initializing.
**How**: Open session detail for a newly created remote session before RWS is ready.
**Pass**: Shows skeleton/loading state while `connecting=true`. Transitions to file tree once files load.
**Fail**: Shows empty panel, or error state, or never transitions from skeleton.
**Code**: `FileExplorerPanel.tsx:295` — `if (isEmpty && isRemote) setConnecting(true)`; cleared on successful `fetchDir()`.

### TC-62f: Worker Assignment Modal — rdev/ssh Tags
**What**: Worker assignment modal (when assigning task to worker) shows type tags for remote workers.
**How**: Open task assignment modal, check worker list entries.
**Pass**: rdev workers show purple "rdev" tag, SSH workers show "ssh" tag. Status badges correct.
**Fail**: No tags, or wrong tag type.

---

## 10. Interactive CLI & Browser View

### TC-63: Interactive CLI via RWS
**What**: Interactive CLI (picture-in-picture terminal) works for remote sessions.
**How**: Open interactive CLI for RWS PTY session.
**Pass**: PTY created on daemon, terminal output streams to overlay, input relayed.
**Fail**: Falls back to tmux path or errors.
**Code**: `interactive_cli.py:54-60` — branches `is_remote_host()` → `open_interactive_cli_via_rws()`.

### TC-64: Interactive CLI — Send/Capture/Close
**What**: Interactive CLI I/O operations work for remote sessions.
**How**: Send input, capture output, close CLI.
**Pass**: `send_to_interactive_cli()`, `capture_interactive_cli()`, `close_interactive_cli()` all route through RWS.
**Fail**: Operations use tmux path instead of RWS.
**Code**: `interactive.py:259, 227, 115` — properly handle both backends.

### TC-65: Browser View via RWS
**What**: Remote browser (CDP proxy) works through RWS daemon.
**How**: Start browser view for remote session.
**Pass**: Browser starts on remote via RWS, CDP screencast streams to frontend.
**Fail**: Browser commands fail, or CDP connection doesn't work through tunnel.

### TC-66: Browser Tabs — Remote Only
**What**: Browser tab management only shown for remote sessions.
**How**: Open browser view settings for remote vs local session.
**Pass**: Tab list and switch/close controls shown for remote. Hidden for local.
**Fail**: Tab controls shown for local sessions, or missing for remote.

### TC-67: Browser View Start — Auto-Start Browser on Remote
**What**: `POST /browser-view` auto-starts browser via RWS daemon when no browser found.
**How**: Start browser view on remote session without browser running.
**Pass**: RWS `start_browser()` called, browser launched, CDP tunnel created, screencast starts.
**Fail**: Returns 502 "No browser found" without attempting auto-start.
**Code**: `browser_view.py:293-305` — `_auto_start_browser_and_retry()` path.

### TC-68: Browser View Start — Stale View Cleanup
**What**: Starting browser view when a stale (dead CDP) view exists cleans it up first.
**How**: Start view, kill CDP connection, start view again.
**Pass**: Old view cleaned up via `cleanup_stale_view()`, new view created successfully.
**Fail**: Returns 409 "already active" for stale view.
**Code**: `browser_view.py:273-278` — stale view detection via `is_view_alive()`.

### TC-69: Browser View Stop
**What**: `DELETE /browser-view` closes CDP connection and tunnel.
**How**: Stop an active browser view.
**Pass**: CDP WebSocket closed, tunnel cleaned up, `browser_view_closed` event published.
**Fail**: Orphaned tunnel or CDP connection.

### TC-70: Browser View Status
**What**: `GET /browser-view` reports active/inactive status with metadata.
**How**: Query status with and without active view.
**Pass**: Returns `active: true` with page_url/title/viewport when view exists. Returns `active: false` when none. Auto-cleans stale views.
**Fail**: Reports active for dead views, or missing metadata.

### TC-71: Browser View Targets Discovery
**What**: `GET /browser-view/targets` lists CDP page targets on remote browser.
**How**: Query targets after browser is running.
**Pass**: Returns list of pages with id/title/url. Works through active view's tunnel or raw port.
**Fail**: Returns 502 because tunnel not available.

### TC-72: Browser View Minimize/Restore
**What**: Minimize and restore publish SSE events for frontend overlay.
**How**: Call minimize and restore endpoints.
**Pass**: `browser_view_minimized` and `browser_view_restored` events published.
**Fail**: Events not published, or 404 when view is active.

### TC-73: Browser Process Start — Remote via RWS
**What**: `POST /browser-start` starts Chromium on remote host via RWS daemon.
**How**: Call browser-start for remote session.
**Pass**: RWS `start_browser()` called, returns pid/port. Handles stale daemon (force-redeploy on "Unknown action").
**Fail**: Returns 503 "RWS not available" or 500 for daemon errors.
**Code**: `browser_view.py:476-529` — remote branch uses `rws.start_browser()`.

### TC-74: Browser Process Stop — Remote via RWS
**What**: `POST /browser-stop` kills browser on remote via RWS daemon.
**How**: Call browser-stop for remote session.
**Pass**: RWS `stop_browser()` called, browser process killed.
**Fail**: Browser keeps running, or error on stale daemon.
**Code**: `browser_view.py:532-557` — `rws.stop_browser()`.

---

## 11. Tunnel Monitor

### TC-75: Tunnel Monitor — Fast Process Check (60s)
**What**: Background monitor checks tunnel process alive every 60s via `is_alive()`.
**How**: Have active remote session, wait for monitor cycle.
**Pass**: Healthy tunnel → no action. Dead tunnel → auto-restart triggered.
**Fail**: Monitor doesn't detect dead process, or doesn't run.
**Code**: `tunnel_monitor.py:106` — `tunnel_manager.is_alive(s.id)`

### TC-76: Tunnel Monitor — Deep Connectivity Probe (5min)
**What**: Every 5th cycle, monitor runs active connectivity probe (SSH + curl from remote).
**How**: Wait for deep probe cycle with a zombie tunnel (process alive but port forward broken).
**Pass**: Probe detects broken connectivity, triggers restart even though process is alive.
**Fail**: Zombie tunnel stays alive because fast check sees live process.
**Code**: `tunnel_monitor.py:111-112` — `if deep_probe: needs_probe.append(s)`

### TC-77: Tunnel Monitor — Auto-Restart Dead Tunnel
**What**: Monitor restarts dead tunnel and updates DB with new PID.
**How**: Kill tunnel process, wait for monitor cycle.
**Pass**: `restart_tunnel()` called, new PID stored in DB, session remains active.
**Fail**: Tunnel not restarted, or DB not updated.
**Code**: `tunnel_monitor.py:162-203` — `_restart_tunnel()`

### TC-78: Tunnel Monitor — MAX_CONSECUTIVE_FAILURES Escalation
**What**: After N consecutive restart failures, monitor gives up and marks session as error.
**How**: Make tunnel restart always fail (e.g., SSH unreachable), wait for enough cycles.
**Pass**: After `MAX_CONSECUTIVE_FAILURES`, session status set to "error", no more restart attempts.
**Fail**: Keeps retrying forever, or marks error too early.
**Code**: `tunnel_monitor.py:170-178` — `failure_count >= MAX_CONSECUTIVE_FAILURES → status="error"`

### TC-79: Tunnel Monitor — Skip Disconnected/Connecting Sessions
**What**: Monitor skips sessions in disconnected or connecting state.
**How**: Have sessions in various states, check which get tunnel checks.
**Pass**: Only sessions NOT in disconnected/connecting are checked.
**Fail**: Checks all sessions including disconnected (wastes resources / causes errors).
**Code**: `tunnel_monitor.py:100-101` — `if s.status in ("disconnected", "connecting"): continue`

### TC-80: Tunnel Monitor — Concurrent Probes
**What**: Deep probes for multiple sessions run concurrently, not sequentially.
**How**: Have 3+ remote sessions, trigger deep probe cycle.
**Pass**: All probes start concurrently via `asyncio.gather()`, total time ≈ max(individual timeouts) not sum.
**Fail**: Sequential execution (total time = N × timeout).
**Code**: `tunnel_monitor.py:133-159` — `_probe_tunnels_concurrent()`

---

## 12. Robustness & Recovery

### RC-1: RWS Daemon Crash Recovery
**What**: RWS daemon process dies (OOM, crash), all PTYs lost.
**Expected**: Health check detects dead PTYs → marks disconnected → reconnect deploys fresh daemon → creates new PTY → Claude resumes with `-r` if session file exists.
**Verify**: Session recovers automatically within health check interval. No manual intervention needed.
**Risk**: Claude process also dies (receives EIO when master_fd closes). Session file on remote is only recovery path.

### RC-2: Forward Tunnel Accumulation
**What**: Stale -L tunnels accumulate for the same host.
**Expected**: Old tunnels cleaned up when new one created, or on session deletion.
**Verify**: After multiple reconnects, only 1 active forward tunnel per host.
**Risk**: Port exhaustion if tunnels leak. `discover_active_tunnels()` scans ps aux — could find stale entries.

### RC-3: Reverse Tunnel Port Binding Conflict
**What**: Remote port 8093 stays bound after tunnel death (TCP TIME_WAIT).
**Expected**: Tunnel retry logic handles "remote port forwarding failed" and retries until port is freed (30-60s).
**Verify**: Session recovers after port release. No permanent failure.
**Code**: `tunnel.py:start_tunnel()` — log parsing for "remote port forwarding failed"

### RC-4: PTY Orphan After Failed Reconnect
**What**: PTY created on remote but reconnect fails partway (e.g., DB update fails).
**Expected**: Next reconnect finds orphaned PTY via `pty_list` session_id match and reuses it.
**Verify**: No accumulation of dead PTYs after repeated failed reconnects.
**Risk**: If PTY created without session_id tag, it becomes invisible to reconnect logic.

### RC-5: Stale rws_pty_id in Database
**What**: rws_pty_id points to a PTY that no longer exists (daemon restarted).
**Expected**: Health check queries pty_list, PTY not found → clears rws_pty_id → triggers reconnect.
**Verify**: Session doesn't stay stuck with stale rws_pty_id.

### RC-6: Partial Config Deployment
**What**: SCP of configs to remote fails partway (network interruption).
**Expected**: Health check detects missing files via manifest verification → re-deploys.
**Verify**: Session recovers on next health check cycle.
**Code**: `health.py:ensure_tmp_dir_health()` — manifest-based verification

### RC-7: SSH Timeout During Health Check
**What**: Slow SSH causes health check to timeout.
**Expected**: SSH fallback has ConnectTimeout=5s. Timeout → treat as unknown, don't mark dead prematurely.
**Verify**: Session not marked disconnected just because SSH is slow.
**Risk**: False disconnection if timeout too aggressive.

### RC-8: Health Check + Reconnect Race
**What**: Health check and user-triggered reconnect run simultaneously.
**Expected**: Per-session lock ensures only one runs. Second attempt detects lock and returns.
**Verify**: No duplicate PTYs or conflicting DB updates.

### RC-9: User Active Reconnect Deferral
**What**: Auto-reconnect deferred when user is actively typing in the terminal.
**Expected**: `is_user_active(session_id)` returns True → reconnect deferred to next health check cycle.
**Verify**: No disruptive reconnect while user is working.
**Code**: `health.py:check_all_workers_health()` — deferred list

### RC-10: Tunnel Port Exhaustion
**What**: `find_available_port()` can't find a free port.
**Expected**: Returns None, tunnel creation fails gracefully.
**Verify**: Informative error, no crash. Port range is 100 ports — could be exhausted with many workers.
**Code**: `tunnel.py:find_available_port()`

### RC-11: RWS Socket Buffer Corruption
**What**: RWS daemon sends incomplete JSON line or unexpected data.
**Expected**: Client timeout or parse error → reconnect socket.
**Verify**: No hang or crash on malformed response.
**Code**: `remote_worker_server.py:execute()` — reads until `\n`, parses JSON

### RC-12: Shared Host Conflicts
**What**: Multiple orchestrator instances on same rdev host.
**Expected**: Each orchestrator has its own RWS daemon (reused via PID file), own tunnel ports, own session IDs.
**Verify**: No cross-contamination. Claude `-r` uses explicit session_id (never `-c` which picks most recent).
**Code**: `session.py:_build_claude_command()` — always uses explicit session arg

### RC-13: Stale RWS Daemon — "Unknown action" Force Redeploy
**What**: Browser and interactive CLI handle stale RWS daemon that doesn't support new actions.
**Expected**: On "Unknown action" error, force-redeploy daemon and retry once.
**Verify**: Browser start and interactive CLI don't permanently fail after daemon upgrade.
**Code**: `browser_view.py:204-208`, `browser_view.py:506-515` — catch "Unknown action", call `force_restart_server()`.

### RC-14: Tunnel Monitor Crash Recovery
**What**: Tunnel monitor loop handles unexpected exceptions without dying.
**Expected**: Exception logged, monitor sleeps one interval, then resumes checking.
**Verify**: After transient error (DB locked, network hiccup), monitor continues running.
**Code**: `tunnel_monitor.py:72-74` — `except Exception: logger.exception(...); await asyncio.sleep()`

---

## 13. Additional File Operations via RWS

All file API endpoints route through RWS for remote sessions. TC-13 through TC-16 cover `check_path`, `list_dir`, `read_file`, and file listing. These cover the remaining endpoints.

### TC-81: Read File Content API
**What**: `GET /sessions/{id}/files/content?path=...` returns file content from remote.
**How**: Read a known file on remote worker via API.
**Pass**: Returns content with correct encoding, line count, language detection, git status.
**Fail**: Falls back to local filesystem read, or errors because RWS not used.
**Code**: `files.py:701` — routes through RWS `read_file` for `is_remote_host()`.

### TC-82: Write File Content API
**What**: `PUT /sessions/{id}/files/content` writes file content to remote via RWS.
**How**: Write a new file on remote worker via API.
**Pass**: File created on remote with correct content. Returns success.
**Fail**: File written locally instead of on remote, or RWS error.
**Code**: `files.py:861` — routes through RWS `write_file` for remote sessions.

### TC-83: Check Mtimes API
**What**: `POST /sessions/{id}/files/mtime` checks modification times for remote files.
**How**: Request mtimes for multiple paths on remote worker.
**Pass**: Returns mtime dict for each file. Works through RWS `check_path` or equivalent.
**Fail**: Returns local mtimes, or errors for remote sessions.
**Code**: `files.py:805`

### TC-84: Delete File API
**What**: `DELETE /sessions/{id}/files?path=...` deletes file on remote via RWS.
**How**: Delete a file on remote worker via API.
**Pass**: File removed on remote. Returns success.
**Fail**: File deleted locally instead of on remote.
**Code**: `files.py:1067`

### TC-85: Move/Rename File API
**What**: `POST /sessions/{id}/files/move` moves/renames file on remote via RWS.
**How**: Rename a file on remote worker via API.
**Pass**: File renamed on remote. Returns success with new path.
**Fail**: Operation runs locally, or fails for remote.
**Code**: `files.py:1132`

### TC-86: Create Directory API
**What**: `POST /sessions/{id}/files/mkdir` creates directory on remote via RWS.
**How**: Create a new directory on remote worker via API.
**Pass**: Directory created on remote. Returns success.
**Fail**: Directory created locally, or error for remote.
**Code**: `files.py:1209`

### TC-87: Raw File Download
**What**: `GET /sessions/{id}/files/raw?path=...` downloads raw file bytes from remote.
**How**: Download a binary file from remote worker.
**Pass**: Returns raw bytes with correct content-type via RWS.
**Fail**: 404 or error because file read from local path.
**Code**: `files.py:996`

---

## 14. Tunnel Management API

### TC-88: Create Port Forward Tunnel
**What**: `POST /sessions/{id}/tunnel` creates SSH -L tunnel for remote worker.
**How**: Create tunnel with specific port for remote session.
**Pass**: SSH tunnel process started, returns local_port, remote_port, pid.
**Fail**: Returns 400 for local session, 409 for already-tunneled port, 500 for SSH failure.
**Code**: `sessions.py:1020-1045` — `create_tunnel()`, only for `is_remote_host()`.

### TC-89: Close Port Forward Tunnel
**What**: `DELETE /sessions/{id}/tunnel/{port}` closes a specific tunnel.
**How**: Close an active tunnel by port number.
**Pass**: SSH process killed, port released.
**Fail**: Wrong tunnel closed, or tunnel process orphaned.
**Code**: `sessions.py:1048-1060`

### TC-90: List Session Tunnels
**What**: `GET /sessions/{id}/tunnels` lists active tunnels for a session.
**How**: Query tunnels for remote session with active tunnels.
**Pass**: Returns tunnel map with remote_port, pid, host for each local port.
**Fail**: Returns empty for local sessions (expected), or misses active tunnels.
**Code**: `sessions.py:1063-1085` — `get_tunnels_for_host()` scans processes.

### TC-91: List All Active Tunnels
**What**: `GET /tunnels` lists all active SSH tunnels across all sessions.
**How**: Query global tunnel list.
**Pass**: Returns all active -L tunnels discovered via process scan.
**Fail**: Misses tunnels or includes dead processes.
**Code**: `sessions.py:1088-1094` — `discover_active_tunnels(force_refresh=True)`.

---

## 15. App Lifecycle

### TC-92: Startup — Remote Sessions Skip Tmux Reconciliation
**What**: `startup_check()` skips remote sessions when reconciling DB against tmux state.
**How**: Restart orchestrator with active remote sessions.
**Pass**: Remote sessions NOT marked as disconnected despite having no tmux window. Local sessions without tmux windows ARE marked disconnected.
**Fail**: Remote sessions marked disconnected on restart (breaks remote workers).
**Code**: `lifecycle.py:38-39` — `if is_remote_host(s.host): continue`

### TC-93: Startup — Tunnel Recovery
**What**: `recover_tunnels()` adopts existing SSH tunnel processes or starts fresh.
**How**: Restart orchestrator with stored tunnel_pid in DB for remote sessions.
**Pass**: If PID alive → adopted (no new SSH process). If PID dead → new tunnel started. DB updated with new PIDs. Disconnected sessions skipped.
**Fail**: Existing tunnels killed unnecessarily, or dead PIDs adopted (PID recycling).
**Code**: `lifecycle.py:45-77` — `tunnel_manager.recover_tunnel()` per session.

### TC-94: Shutdown — RWS Forward Tunnel Cleanup
**What**: App shutdown calls `shutdown_all_rws_servers()` to clean up forward tunnels.
**How**: Shut down orchestrator.
**Pass**: All RWS client forward tunnels killed. `_server_pool` cleared.
**Fail**: Forward tunnel processes orphaned.
**Code**: `app.py:140-145` — `shutdown_all_rws_servers()`; `remote_worker_server.py:2053-2061`.

### TC-95: Shutdown — Reverse Tunnels Survive Restart
**What**: Reverse tunnels intentionally NOT stopped on shutdown (survive orchestrator restarts).
**How**: Shut down and restart orchestrator.
**Pass**: SSH -R processes still alive after shutdown. Re-adopted on next startup via `recover_tunnels()`.
**Fail**: Tunnels killed on shutdown, forcing full reconnect on restart.
**Code**: `app.py:150-152` — "we do NOT call tunnel_manager.stop_all() here"

---

## 16. Migration & Compatibility

### MC-1: Legacy screen_detached State
**What**: `screen_detached` status still exists in state machine.
**How**: Check if any code still sets this status for RWS PTY sessions.
**Expected**: RWS PTY sessions should never enter `screen_detached` — only disconnected/error.
**Risk**: UI shows reconnect for screen_detached; if RWS PTY session somehow enters this state, reconnect path must handle it.
**Code**: `state_machine.py:RECONNECTABLE_STATES = {DISCONNECTED, SCREEN_DETACHED, ERROR}`

### MC-2: Tmux Window for Remote Sessions
**What**: Tmux window still created during session creation even for RWS PTY sessions.
**How**: Check if tmux window exists after creating remote session.
**Expected**: Window exists but is unused (empty shell). Not harmful but confusing.
**Risk**: Endpoints that send tmux commands succeed silently on this empty pane, making debugging harder.
**Code**: `sessions.py:259` — `ensure_window()` called for all sessions

### MC-3: Legacy Migration — Screen Session to RWS PTY on Reconnect
**What**: A legacy remote session (rws_pty_id=NULL, previously running in GNU Screen + tmux pane) gets disconnected and reconnects. Reconnect should migrate it to the new RWS PTY architecture seamlessly.
**How**: Simulate a legacy session: remote session with rws_pty_id=NULL, screen session dead, then trigger reconnect.
**Expected flow**:
  1. `reconnect_remote_worker()` sees `rws_pty_id=NULL` → takes the "new session or legacy migration" path (line 750)
  2. Ensures RWS daemon deployed and connected
  3. Ensures reverse tunnel alive
  4. **Kills old screen session** on remote (best-effort cleanup via `screen -X quit`)
  5. Deploys configs to remote
  6. Creates new RWS PTY with Claude (using `-r` if session file exists, `--session-id` if not)
  7. Sets `rws_pty_id` on the session DB record
  8. From this point, all routing (WebSocket, health check, reconnect) uses RWS PTY path
**Pass**: Session has rws_pty_id set after reconnect, Claude running in RWS PTY, old screen session killed, terminal streaming works via `stream_remote_pty()`.
**Fail**: rws_pty_id stays NULL, session stuck in error, or old screen not cleaned up.
**Code**: `reconnect.py:746-798` — the `not session.rws_pty_id` branch

### MC-4: Legacy Migration — Tmux Pane Becomes Vestigial
**What**: After migration, the old tmux pane (created at session creation time) still exists but is now unused.
**How**: After MC-3 migration, check what happens to the tmux window.
**Expected**: Tmux window still exists (empty shell). Not harmful. Session API endpoints that send tmux commands hit this empty pane instead of the RWS PTY — this is the root cause of TC-43 through TC-51 bugs.
**Risk**: Confusing for debugging. Commands appear to succeed but go nowhere.

### MC-5: Legacy Migration — WebSocket Routing Switch
**What**: After migration sets rws_pty_id, the next WebSocket connection should route to `stream_remote_pty()` instead of `stream_pane()`.
**How**: Open terminal in browser before and after migration.
**Expected**: Before migration (rws_pty_id=NULL, if somehow viewing): `stream_pane()` via tmux. After migration: `stream_remote_pty()` via RWS daemon with ringbuffer replay.
**Code**: `ws_terminal.py:550` — `if rws_pty_id and is_remote_host(host):` routes to `stream_remote_pty()`

### MC-6: Legacy Migration — Health Check Routing Switch
**What**: After migration, health check should use RWS PTY path instead of screen-based detection.
**How**: Trigger health check after migration completes.
**Expected**: Routes through `_check_rws_pty_health()`, returns `alive=true` with `reason="RWS PTY alive"`.
**Fail**: Falls back to screen-based check (which would return dead since screen was killed).

### MC-7: Failed Setup — rws_pty_id Stays NULL
**What**: Remote session setup fails before setting rws_pty_id (e.g., RWS daemon deploy fails).
**How**: Create remote session where RWS deploy times out.
**Expected**: Session in error/connecting state with rws_pty_id=NULL. Next reconnect attempt retries the full setup including daemon deploy.
**Risk**: If health check can't handle rws_pty_id=NULL remote sessions, the session gets stuck with no recovery path.

---

## Summary

| Category | Test Cases | Status |
|----------|-----------|--------|
| RWS Daemon & PTY Core | TC-1 through TC-7, TC-3a (8) | TC-3a added for output quality |
| Tunnel Infrastructure | TC-8 through TC-12 (5) | - |
| File Operations (Basic) | TC-13 through TC-16 (4) | - |
| Session Lifecycle | TC-17 through TC-22 (6) | - |
| Terminal WebSocket | TC-23 through TC-28 (6) | - |
| Health Checks | TC-29 through TC-35 (7) | - |
| Reconnect Scenarios | TC-36 through TC-42 (7) | - |
| Brain/Session API | TC-43 through TC-52, TC-44d, TC-44e (14) | All 12 bugs **FIXED** + 2 new output quality tests |
| UI — Frontend | TC-53 through TC-62f (16) | TC-62a **FIXED** |
| Interactive CLI & Browser View | TC-63 through TC-74 (12) | - |
| Tunnel Monitor | TC-75 through TC-80 (6) | - |
| Additional File Operations | TC-81 through TC-87 (7) | - |
| Tunnel Management API | TC-88 through TC-91 (4) | - |
| App Lifecycle | TC-92 through TC-95 (4) | - |
| Robustness & Recovery | RC-1 through RC-14 (14) | - |
| Migration & Compatibility | MC-1 through MC-7 (7) | - |
| **Total** | **106 test cases + 14 robustness + 7 migration = 127** | **All 12 known bugs FIXED** |
