# Implementation Plan: Fix 12 Known Bugs (tmux → RWS PTY routing)

## Problem

12 API endpoints silently fail for RWS PTY remote sessions because they use tmux operations (`send_keys`, `capture_pane`, `paste_to_pane`) to communicate with Claude. For RWS PTY sessions there is no active tmux pane — commands go to a vestigial empty pane and are lost.

## Root Cause

All 12 bugs share one root cause: the API routes call tmux functions directly without checking whether the session uses RWS PTY. The fix is a single routing layer — check `rws_pty_id`, and if set, use the RWS daemon's `pty_input` / `pty_capture` actions instead of tmux.

## Existing RWS Primitives (already implemented)

The RWS daemon already supports everything needed:

| Action | Daemon Handler | Client Method | Purpose |
|--------|---------------|---------------|---------|
| `pty_input` | `handle_pty_input(cmd)` at line 818 | None (need to add) | Write bytes to PTY stdin |
| `pty_capture` | `handle_pty_capture(cmd)` at line 779 | None (need to add) | Read ringbuffer, strip ANSI, return last N lines |

The WebSocket terminal (`ws_terminal.py:690`) already writes to the PTY via the stream socket's `{"type": "input", "data": "..."}` JSON-line protocol. The API endpoints need a simpler path — fire-and-forget writes via the command socket.

---

## Step 1: Add client helper methods to `RemoteWorkerServer`

**File**: `orchestrator/terminal/remote_worker_server.py`

Add two methods to the `RemoteWorkerServer` class (after `list_ptys()` at line ~1812):

```python
def write_to_pty(self, pty_id: str, data: str) -> None:
    """Write data to a PTY's stdin via the command socket."""
    resp = self.execute({"action": "pty_input", "pty_id": pty_id, "data": data})
    if "error" in resp:
        raise RuntimeError(f"PTY input failed on {self.host}: {resp['error']}")

def capture_pty(self, pty_id: str, lines: int = 30) -> str:
    """Capture the last N lines of PTY output (ANSI-stripped)."""
    resp = self.execute({"action": "pty_capture", "pty_id": pty_id, "lines": lines})
    if "error" in resp:
        raise RuntimeError(f"PTY capture failed on {self.host}: {resp['error']}")
    return resp.get("output", "")
```

These use the existing `execute()` method which handles reconnection, retries, and timeouts.

---

## Step 2: Add `_write_to_rws_pty()` and `_capture_rws_pty()` helpers in sessions.py

**File**: `orchestrator/api/routes/sessions.py`

Add two private helper functions (near `_capture_preview()` at line ~124):

```python
def _write_to_rws_pty(session, data: str) -> bool:
    """Write data to a remote session's RWS PTY. Returns True on success."""
    from orchestrator.terminal.remote_worker_server import get_remote_worker_server
    try:
        rws = get_remote_worker_server(session.host)
        rws.write_to_pty(session.rws_pty_id, data)
        return True
    except RuntimeError:
        logger.warning("Could not write to RWS PTY for session %s", session.name, exc_info=True)
        return False


def _capture_rws_pty(session, lines: int = 30) -> str:
    """Capture terminal output from a remote session's RWS PTY."""
    from orchestrator.terminal.remote_worker_server import get_remote_worker_server
    try:
        rws = get_remote_worker_server(session.host)
        return rws.capture_pty(session.rws_pty_id, lines=lines)
    except RuntimeError:
        return ""
```

---

## Step 3: Fix `_capture_preview()` (TC-44a, TC-44b, TC-44c, TC-62a)

**File**: `orchestrator/api/routes/sessions.py`, function `_capture_preview()` (line 124)

**Current code**:
```python
def _capture_preview(s) -> str:
    tmux_sess, tmux_win = tmux_target(s.name)
    try:
        content = capture_pane_with_escapes(tmux_sess, tmux_win, lines=0)
        return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", content)
    except Exception:
        return ""
```

**New code**:
```python
def _capture_preview(s) -> str:
    # RWS PTY sessions: capture via daemon (already ANSI-stripped)
    if is_remote_host(s.host) and s.rws_pty_id:
        return _capture_rws_pty(s)

    # Local / legacy: capture from tmux pane
    tmux_sess, tmux_win = tmux_target(s.name)
    try:
        content = capture_pane_with_escapes(tmux_sess, tmux_win, lines=0)
        return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", content)
    except Exception:
        return ""
```

**Fixes**: TC-44a (`GET /preview`), TC-44b (`?include_preview=true`), TC-44c (TaskWorkerPreview polling), TC-62a (WorkerCardCompact). All call `_capture_preview()`.

---

## Step 4: Fix `/send` endpoint (TC-45)

**File**: `orchestrator/api/routes/sessions.py`, function `send_message()` (line 604)

**Current code** (line 610-616):
```python
from orchestrator.terminal.manager import TMUX_SESSION
from orchestrator.terminal.session import send_to_session

success = send_to_session(s.name, body.message, TMUX_SESSION)
```

**New code**:
```python
if is_remote_host(s.host) and s.rws_pty_id:
    # RWS PTY: write message + Enter directly to PTY stdin
    success = _write_to_rws_pty(s, body.message + "\n")
else:
    from orchestrator.terminal.manager import TMUX_SESSION
    from orchestrator.terminal.session import send_to_session
    success = send_to_session(s.name, body.message, TMUX_SESSION)
```

**Note**: `send_to_session()` does text + Enter with retry logic to verify the Enter key was received. The RWS path appends `\n` directly (no retry needed since `pty_input` writes bytes atomically to the master fd).

---

## Step 5: Fix `/type` endpoint (TC-46)

**File**: `orchestrator/api/routes/sessions.py`, function `type_text()` (line 623)

**Current code** (line 635-637):
```python
from orchestrator.terminal.manager import TMUX_SESSION, send_keys_literal
success = send_keys_literal(TMUX_SESSION, s.name, body.text)
```

**New code**:
```python
if is_remote_host(s.host) and s.rws_pty_id:
    success = _write_to_rws_pty(s, body.text)
else:
    from orchestrator.terminal.manager import TMUX_SESSION, send_keys_literal
    success = send_keys_literal(TMUX_SESSION, s.name, body.text)
```

**No `\n`** — `/type` injects text without pressing Enter.

---

## Step 6: Fix `/paste-to-pane` endpoint (TC-47)

**File**: `orchestrator/api/routes/sessions.py`, function `paste_to_pane_endpoint()` (line 643)

**Current code** (line 656-658):
```python
from orchestrator.terminal.manager import TMUX_SESSION, paste_to_pane
success = paste_to_pane(TMUX_SESSION, s.name, body.text)
```

**New code**:
```python
if is_remote_host(s.host) and s.rws_pty_id:
    # Bracketed paste: wrap in ESC[200~ ... ESC[201~ so Claude Code treats it as pasted text
    bracketed = f"\x1b[200~{body.text}\x1b[201~"
    success = _write_to_rws_pty(s, bracketed)
else:
    from orchestrator.terminal.manager import TMUX_SESSION, paste_to_pane
    success = paste_to_pane(TMUX_SESSION, s.name, body.text)
```

**Bracketed paste mode**: tmux's `paste-buffer -p` wraps text in `ESC[200~`...`ESC[201~`. The RWS path must do the same so Claude Code displays "[N lines of text]" instead of echoing every line.

---

## Step 7: Fix `/pause` endpoint (TC-48)

**File**: `orchestrator/api/routes/sessions.py`, function `pause_session()` (line 748)

**Current code** (line 758-762):
```python
tmux_sess, tmux_win = tmux_target(s.name)
try:
    send_keys(tmux_sess, tmux_win, "Escape", enter=False)
except Exception:
    logger.warning(...)
```

**New code**:
```python
if is_remote_host(s.host) and s.rws_pty_id:
    _write_to_rws_pty(s, "\x1b")  # ESC byte
else:
    tmux_sess, tmux_win = tmux_target(s.name)
    try:
        send_keys(tmux_sess, tmux_win, "Escape", enter=False)
    except Exception:
        logger.warning(...)
```

---

## Step 8: Fix `/continue` endpoint (TC-49)

**File**: `orchestrator/api/routes/sessions.py`, function `continue_session()` (line 768)

**Current code** (line 776-785):
```python
tmux_sess, tmux_win = tmux_target(s.name)
try:
    send_keys_literal(tmux_sess, tmux_win, "continue")
    send_keys(tmux_sess, tmux_win, "", enter=True)
except Exception:
    logger.warning(...)
```

**New code**:
```python
if is_remote_host(s.host) and s.rws_pty_id:
    _write_to_rws_pty(s, "continue\n")
else:
    tmux_sess, tmux_win = tmux_target(s.name)
    try:
        from orchestrator.terminal.manager import send_keys_literal
        send_keys_literal(tmux_sess, tmux_win, "continue")
        send_keys(tmux_sess, tmux_win, "", enter=True)
    except Exception:
        logger.warning(...)
```

---

## Step 9: Fix `/stop` endpoint (TC-50)

**File**: `orchestrator/api/routes/sessions.py`, function `stop_session()` (line 791)

**Current code** (line 799-813):
```python
tmux_sess, tmux_win = tmux_target(s.name)
try:
    send_keys(tmux_sess, tmux_win, "Escape", enter=False)
    time.sleep(0.5)
    send_keys_literal(tmux_sess, tmux_win, "/clear")
    send_keys(tmux_sess, tmux_win, "", enter=True)
except Exception:
    logger.warning(...)
```

**New code**:
```python
if is_remote_host(s.host) and s.rws_pty_id:
    import time
    _write_to_rws_pty(s, "\x1b")  # Escape
    time.sleep(0.5)
    _write_to_rws_pty(s, "/clear\n")
else:
    tmux_sess, tmux_win = tmux_target(s.name)
    import time
    try:
        send_keys(tmux_sess, tmux_win, "Escape", enter=False)
        time.sleep(0.5)
        from orchestrator.terminal.manager import send_keys_literal
        send_keys_literal(tmux_sess, tmux_win, "/clear")
        send_keys(tmux_sess, tmux_win, "", enter=True)
    except Exception:
        logger.warning(...)
```

The rest of stop_session (unassign tasks, close interactive CLI, update status) is independent of tmux and needs no change.

---

## Step 10: Fix `/prepare-for-task` endpoint (TC-51)

**File**: `orchestrator/api/routes/sessions.py`, function `prepare_session_for_task()` (line 834)

**Current code** (line 852-879):
```python
tmux_sess, tmux_win = tmux_target(s.name)
try:
    send_keys(tmux_sess, tmux_win, "Escape", enter=False)
    time.sleep(0.3)
    subprocess.run(["tmux", "send-keys", "-t", f"{tmux_sess}:{tmux_win}", "C-c"], ...)
    time.sleep(0.5)
    send_keys_literal(tmux_sess, tmux_win, "/clear")
    send_keys(tmux_sess, tmux_win, "", enter=True)
except Exception:
    logger.warning(...)
```

**New code**:
```python
if is_remote_host(s.host) and s.rws_pty_id:
    import time
    _write_to_rws_pty(s, "\x1b")    # Escape
    time.sleep(0.3)
    _write_to_rws_pty(s, "\x03")    # Ctrl-C (ETX byte)
    time.sleep(0.5)
    _write_to_rws_pty(s, "/clear\n")
    logger.info("Prepared session %s for new task assignment", s.name)
else:
    tmux_sess, tmux_win = tmux_target(s.name)
    import time
    try:
        send_keys(tmux_sess, tmux_win, "Escape", enter=False)
        time.sleep(0.3)
        import subprocess
        subprocess.run(["tmux", "send-keys", "-t", f"{tmux_sess}:{tmux_win}", "C-c"],
                       capture_output=True, timeout=2)
        time.sleep(0.5)
        from orchestrator.terminal.manager import send_keys_literal
        send_keys_literal(tmux_sess, tmux_win, "/clear")
        send_keys(tmux_sess, tmux_win, "", enter=True)
        logger.info("Prepared session %s for new task assignment", s.name)
    except Exception:
        logger.warning(...)
```

---

## Step 11: Fix brain sync terminal capture (TC-43)

**File**: `orchestrator/api/routes/brain.py`, lines 230-240

**Current code**:
```python
for s in active_workers:
    parts.append(f"## Worker: {s.name} (status: {s.status}, id: {s.id})")
    ts, tw = tmux.tmux_target(s.name)
    try:
        preview = tmux.capture_output(ts, tw, lines=30)
    except Exception:
        preview = "(could not capture terminal)"
```

**New code**:
```python
from orchestrator.terminal.ssh import is_remote_host
from orchestrator.terminal.remote_worker_server import get_remote_worker_server

for s in active_workers:
    parts.append(f"## Worker: {s.name} (status: {s.status}, id: {s.id})")
    # RWS PTY sessions: capture via daemon
    if is_remote_host(s.host) and s.rws_pty_id:
        try:
            rws = get_remote_worker_server(s.host)
            preview = rws.capture_pty(s.rws_pty_id, lines=30)
        except Exception:
            preview = "(could not capture terminal)"
    else:
        ts, tw = tmux.tmux_target(s.name)
        try:
            preview = tmux.capture_output(ts, tw, lines=30)
        except Exception:
            preview = "(could not capture terminal)"
```

---

## Files Changed Summary

| File | Changes |
|------|---------|
| `orchestrator/terminal/remote_worker_server.py` | Add `write_to_pty()` and `capture_pty()` client methods |
| `orchestrator/api/routes/sessions.py` | Add `_write_to_rws_pty()`, `_capture_rws_pty()` helpers. Branch on `rws_pty_id` in: `_capture_preview()`, `send_message()`, `type_text()`, `paste_to_pane_endpoint()`, `pause_session()`, `continue_session()`, `stop_session()`, `prepare_session_for_task()` |
| `orchestrator/api/routes/brain.py` | Branch on `rws_pty_id` in brain_sync terminal capture |

**Total**: 3 files, ~80 lines of new code. No new dependencies.

---

## Testing

### Unit Tests (new)
1. **`_write_to_rws_pty`**: Mock `get_remote_worker_server()`, verify `write_to_pty()` called with correct data
2. **`_capture_rws_pty`**: Mock RWS, verify `capture_pty()` called, returns output
3. **`_capture_preview` branch**: RWS PTY session → `_capture_rws_pty()` called; local session → tmux path
4. **`send_message` branch**: RWS PTY session → writes `message + \n`; local → `send_to_session()`
5. **`pause_session` branch**: RWS PTY → writes `\x1b`; local → tmux Escape
6. **`stop_session` branch**: RWS PTY → writes `\x1b` + sleep + `/clear\n`; local → tmux path
7. **`prepare_session_for_task` branch**: RWS PTY → writes `\x1b` + `\x03` + `/clear\n`
8. **`paste_to_pane` branch**: RWS PTY → writes bracketed paste sequence
9. **Brain sync capture**: RWS PTY session → uses `rws.capture_pty()`

### Integration Tests (against live worker)
1. Call `GET /preview` for RWS PTY session → non-empty content
2. Call `GET /sessions?include_preview=true` → RWS PTY sessions have live preview
3. Call `POST /pause` → verify Escape reached PTY (capture shows paused state)
4. Call `POST /continue` → verify Claude resumes
5. Call `POST /stop` → verify /clear sent, status → idle
6. Call `POST /send` with test message → verify message appears in PTY capture

### Verification Commands
```bash
# Lint + format
uv run ruff check . --fix && uv run ruff format .

# Unit tests
uv run pytest tests/unit/ -v -o "addopts="

# Quick smoke test against live session
curl -s -X POST http://localhost:8093/api/sessions/{SESSION_ID}/pause | jq .
curl -s http://localhost:8093/api/sessions/{SESSION_ID}/preview | jq '.content | length'
```
