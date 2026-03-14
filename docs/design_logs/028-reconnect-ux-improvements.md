# 028 — Remote Worker Reconnect UX Improvements

## Problem Statement

When a remote worker disconnects (server restart, SSH tunnel death, etc.), the user sees a confusing terminal view:

1. **Repeated error spam in terminal**: The message "Remote session is reconnecting — PTY not attached yet" appears multiple times as each WebSocket retry gets the same 4004 close code and writes a new error line.
2. **No reconnection progress**: The user has no idea what the backend is doing — is it re-establishing SSH? Restarting the daemon? Creating a new PTY? They just see a dark terminal with red error text.
3. **Disconnected badge with no context**: The header shows a red "Disconnected" badge and a refresh icon, but doesn't indicate that auto-reconnect is actively working.
4. **Overlay fights with terminal content**: The small "Reconnecting in 1s" toast overlaps the bottom-right corner while the terminal body shows error messages — two competing UI elements for the same state.
5. **No distinction between "reconnecting" and "permanently disconnected"**: The UI looks essentially the same whether the system is actively trying to reconnect or has given up.

### What the user sees today

```
┌─────────────────────────────────────────────────────────┐
│ worker_name  [rdev]  [Disconnected]  🔄                 │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ Remote session is reconnecting — PTY not attached yet   │ ← error msg #1
│ Remote session is reconnecting — PTY not attached yet   │ ← error msg #2
│ Remote session is reconnecting — PTY not attached yet   │ ← error msg #3
│ Remote session is reconnecting — PTY not attached yet   │ ← error msg #4
│ Remote session is reconnecting — PTY not attached yet   │ ← error msg #5
│ Remote session is reconnecting — PTY not attached yet   │ ← error msg #6
│                                                         │
│                                                         │
│                           ┌─────────────────────┐       │
│                           │ Reconnecting in 1s  │       │ ← overlay toast
│                           └─────────────────────┘       │
└─────────────────────────────────────────────────────────┘
```

### What the user should see

```
┌─────────────────────────────────────────────────────────┐
│ worker_name  [rdev]  [Reconnecting...]  🔄              │
├─────────────────────────────────────────────────────────┤
│                                                         │
│                                                         │
│              ┌──────────────────────────┐               │
│              │   ◠  Reconnecting...     │               │
│              │                          │               │
│              │   ✓ SSH tunnel           │               │
│              │   ◠ RWS daemon           │               │
│              │   ○ PTY session          │               │
│              │                          │               │
│              │   Attempt 2 · 15s        │               │
│              └──────────────────────────┘               │
│                                                         │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## Root Cause Analysis

### Why error messages repeat in the terminal

The WebSocket reconnection loop (4004 path) creates a new WS connection every 5 seconds. Each time, the backend sends the error JSON message before closing with 4004:

```
ws_terminal.py:561-564
    await websocket.send_json(
        {"type": "error", "message": "Remote session is reconnecting — PTY not attached yet"}
    )
    await websocket.close(code=4004)
```

The frontend writes this to xterm:

```
TerminalView.tsx:314
    terminal.write(`\r\n\x1b[31m${msg.message}\x1b[0m\r\n`)
```

Each retry appends another red line. After 12 retries (60 seconds), there are 12 identical lines.

### Why there's no progress indication

The backend reconnection happens in a background thread (`trigger_reconnect` → `_bg_reconnect`). It goes through multiple steps:

1. Acquire per-session lock
2. Ensure reverse tunnel alive
3. Ensure RWS daemon connected
4. Reconnect RWS forward tunnel
5. Check PTY status
6. Deploy configs
7. Create new PTY
8. Verify PTY alive

None of these steps emit progress events. The only status change visible to the frontend is `status: "connecting"` (set before the thread starts) → `status: "working"` (set on success) or `status: "disconnected"/"error"` (set on failure).

### Why the header badge doesn't reflect reconnect state

The session status in the DB is set to `"connecting"` when reconnect starts, but:
- The frontend status badge just renders `{session.status}` — there's no special treatment for `"connecting"` vs `"disconnected"` in terms of messaging.
- The `"connecting"` status does trigger `isLocked = true` in TerminalView, which shows "Setting up connection..." overlay — but this is the *initial* setup message, not a reconnect-specific one.

---

## Design

### 1. Reconnect Progress Events via Server-Sent Updates

**Approach**: Add a `reconnect_step` field to the session model that the backend updates as it progresses through reconnect stages. The frontend polls session status (already happens via AppContext) and reads this field.

**Backend changes** — `reconnect.py`:

Add a helper that updates both status and the current reconnect step:

```python
# New field on sessions table: reconnect_step TEXT (nullable)
# Values: "tunnel", "daemon", "pty_check", "deploy", "pty_create", "verify", null

def _update_reconnect_progress(conn, session_id, step: str, repo):
    """Update the reconnect step for frontend progress display."""
    repo.update_session(conn, session_id, reconnect_step=step)
```

Instrument `_reconnect_rws_pty_worker` and `reconnect_remote_worker`:

```python
def _reconnect_rws_pty_worker(conn, session, repo, tunnel_manager):
    # 1. Ensure reverse tunnel alive
    _update_reconnect_progress(conn, session.id, "tunnel", repo)
    if tunnel_manager and not tunnel_manager.is_alive(session.id):
        _ensure_tunnel(session, tunnel_manager, repo, conn)

    # 2. Ensure RWS daemon connected
    _update_reconnect_progress(conn, session.id, "daemon", repo)
    rws = _ensure_rws_ready(session.host, timeout=30)

    # 3. Check PTY status
    _update_reconnect_progress(conn, session.id, "pty_check", repo)
    ...

    # 5. Deploy configs
    _update_reconnect_progress(conn, session.id, "deploy", repo)
    ...

    # 6. Create new PTY
    _update_reconnect_progress(conn, session.id, "pty_create", repo)
    ...

    # 7. Verify
    _update_reconnect_progress(conn, session.id, "verify", repo)
    ...

    # Clear on success
    repo.update_session(conn, session.id, reconnect_step=None, status="working")
```

Clear `reconnect_step` on both success and failure:
```python
# In trigger_reconnect's _bg_reconnect:
except Exception:
    repo.update_session(bg_conn, _session.id, status="disconnected", reconnect_step=None)
```

**Why a DB field instead of WebSocket/SSE**: The reconnection happens in a background thread with its own DB connection. The simplest way to communicate progress to the frontend is through the existing session polling mechanism (AppContext already polls `/api/sessions` every few seconds). No new transport needed.

**Schema migration**:
```sql
ALTER TABLE sessions ADD COLUMN reconnect_step TEXT;
```

### 2. Frontend Reconnect Overlay

**Replace the current dual-display** (error text in terminal + toast overlay) with a single, centered reconnect overlay that shows progress steps.

**New component**: `ReconnectOverlay` (inline in TerminalView or a small sub-component).

**Step display mapping**:
| `reconnect_step` | Label | Icon state |
|---|---|---|
| `"tunnel"` | SSH tunnel | spinning |
| `"daemon"` | RWS daemon | pending |
| `"pty_check"` | Checking PTY | pending |
| `"deploy"` | Deploying configs | pending |
| `"pty_create"` | Creating PTY | pending |
| `"verify"` | Verifying | pending |

Steps before the current one show a checkmark. The current step shows a spinner. Steps after show a circle (pending).

**When to show**: The reconnect overlay appears when:
- `sessionStatus === "connecting"` AND the session has a `reconnect_step` value, OR
- The terminal WebSocket is in the `reconnecting` state (4004 loop)

**When NOT to show**: Initial connection (first time setup) should keep the existing skeleton + "Setting up connection..." overlay.

**Elapsed time**: Show a running timer since the reconnect started (track locally from when `sessionStatus` transitions to `"connecting"`).

**Attempt counter**: Show which reconnect attempt this is (from `reconnectAttemptRef` for WS retries, or from a new `reconnect_attempt` DB field if we want backend-level tracking).

**TerminalView.tsx changes**:

```tsx
// Determine overlay type
const isReconnecting = sessionStatus === 'connecting' && session?.reconnect_step
const showReconnectOverlay = isReconnecting || (ready && connectionState === 'reconnecting')

// In the overlay section:
{showReconnectOverlay && (
  <div className="terminal-overlay">
    <ReconnectProgress
      step={session?.reconnect_step}
      elapsedSeconds={reconnectElapsed}
    />
  </div>
)}
```

### 3. Suppress Error Text Spam in Terminal

**Stop writing 4004 error messages to the terminal**. The error text "Remote session is reconnecting — PTY not attached yet" is redundant when the overlay shows reconnect progress.

**ws_terminal.py change** — Remove or suppress the error JSON message for the 4004 path:

```python
# Before (sends visible error text to terminal):
if is_remote_host(host):
    await websocket.send_json(
        {"type": "error", "message": "Remote session is reconnecting — PTY not attached yet"}
    )
    await websocket.close(code=4004)

# After (close with 4004, no error text — frontend handles display):
if is_remote_host(host):
    await websocket.close(code=4004)
```

**TerminalView.tsx change** — Clear terminal on reconnect instead of accumulating error lines:

```tsx
// In the 4004 handler:
if (event.code === 4004) {
    setConnectionState('reconnecting')
    // Don't write error text — overlay handles display
    // Clear any previous error spam on first 4004
    if (reconnectAttemptRef.current === 0) {
        terminal.reset()
    }
    ...
}
```

### 4. Header Status Badge for Reconnecting State

The session status badge should distinguish between `"connecting"` (reconnect in progress) and `"disconnected"` (given up / needs manual intervention).

**SessionDetailPage.tsx** — The badge already renders `session.status`, so `"connecting"` shows as-is. But the CSS class and display text need adjustment:

```tsx
// Map status to display label
const statusLabel = session.status === 'connecting' ? 'Reconnecting...' : session.status
```

**CSS** — Add a `connecting` badge style (pulsing amber, similar to `reconnecting` terminal border):

```css
.status-badge.connecting {
  background: rgba(210, 153, 34, 0.15);
  color: #d29922;
  animation: badge-pulse 1.5s ease-in-out infinite;
}
```

### 5. Reconnect Failure State

When reconnect fails (status goes to `"disconnected"` or `"error"`), the overlay should transition to a clear failure message with the Retry button:

```
┌──────────────────────────┐
│   ✕  Reconnect failed    │
│                          │
│   ✓ SSH tunnel           │
│   ✓ RWS daemon           │
│   ✕ PTY session (failed) │
│                          │
│   [Retry]  [Dismiss]     │
└──────────────────────────┘
```

The last `reconnect_step` value tells us where it failed. We can show completed steps with checkmarks and the failed step with an X.

To support this, the backend should preserve the `reconnect_step` value on failure (don't clear it), and add a convention: `reconnect_step` is non-null → reconnect was attempted. Combined with `status`:
- `status="connecting"` + `reconnect_step="daemon"` → reconnect in progress at daemon step
- `status="disconnected"` + `reconnect_step="daemon"` → reconnect failed at daemon step
- `status="working"` + `reconnect_step=null` → successfully connected

### 6. Session API Response Changes

The session API response needs to include `reconnect_step` so the frontend can read it:

```python
# In the sessions list/detail API response:
{
    "id": "...",
    "status": "connecting",
    "reconnect_step": "daemon",  # new field
    ...
}
```

This just requires adding the column to the DB and including it in the session model serialization.

---

## Data Flow Summary

```
User clicks Reconnect (or auto-reconnect triggers)
    │
    ▼
trigger_reconnect()
    ├── Sets status="connecting" in DB
    ├── Spawns background thread
    │
    │   Background thread:
    │   ├── reconnect_step="tunnel"  → DB
    │   ├── reconnect_step="daemon"  → DB
    │   ├── reconnect_step="pty_check" → DB
    │   ├── reconnect_step="deploy"  → DB
    │   ├── reconnect_step="pty_create" → DB
    │   ├── reconnect_step="verify"  → DB
    │   └── status="working", reconnect_step=null → DB (success)
    │       OR status="disconnected" → DB (failure, step preserved)
    │
    ▼
Frontend (AppContext polls /api/sessions every 2-3s)
    ├── Reads session.status + session.reconnect_step
    ├── TerminalView renders ReconnectOverlay with step progress
    └── Header badge shows "Reconnecting..." with amber pulse
```

---

## Implementation Plan

### Phase 1: Backend progress tracking (Small)

1. Add `reconnect_step TEXT` column to sessions table
2. Add `_update_reconnect_progress()` helper in `reconnect.py`
3. Instrument `_reconnect_rws_pty_worker` and `reconnect_remote_worker` with step updates
4. Clear step on success, preserve on failure
5. Include `reconnect_step` in session API response

### Phase 2: Frontend overlay (Medium)

1. Add `reconnect_step` to the `Session` TypeScript type
2. Create `ReconnectProgress` sub-component (step list with icons)
3. Replace current terminal error text + toast with centered overlay
4. Suppress error message writes for 4004 WebSocket closes
5. Clear terminal on first 4004 instead of accumulating error lines
6. Add elapsed time counter

### Phase 3: Header and badge polish (Small)

1. Map `"connecting"` status to "Reconnecting..." display label
2. Add CSS for `connecting` badge (amber pulse)
3. Show failure context when status is `"disconnected"` + `reconnect_step` is set

---

## Edge Cases

**Rapid manual reconnect clicks**: `trigger_reconnect` already checks `lock.locked()` and returns early if a reconnect is in progress. The frontend sees `{"ok": false, "error": "Reconnect already in progress"}` and can show a toast.

**Server restart during reconnect**: The background thread dies. On next poll, the frontend sees `status="connecting"` but no further progress. The health check (every 5 min) will eventually detect this and either complete the reconnect or set status to `"disconnected"`. To handle the gap: if `reconnect_step` hasn't changed for >60s, the frontend can show "Reconnect may be stalled" with a manual Retry button.

**Multiple sessions reconnecting simultaneously**: Each session has its own `reconnect_step` field and per-session lock. No interference.

**Frontend polls miss a step**: Steps may be fast (e.g., tunnel check takes <1s). The frontend may see `"tunnel"` → `"pty_create"` skipping `"daemon"`. This is fine — the overlay should show all steps up to and including the current one as completed, not require seeing each step individually.

**Old sessions without reconnect_step**: `reconnect_step` is nullable. If null and status is `"connecting"`, fall back to the current generic "Setting up connection..." overlay.

---

## Alternatives Considered

### WebSocket push for progress events
Instead of polling, push progress updates through a dedicated WebSocket or the existing session events WS. Rejected because:
- The terminal WS is disconnected during reconnect (that's the whole problem)
- Adding a separate WS channel adds complexity for marginal latency gain
- Polling every 2-3s is good enough — reconnect steps take 5-30s each

### Server-Sent Events (SSE)
A reconnect-specific SSE endpoint. Rejected for the same reason — the existing polling is sufficient and simpler.

### Toast notifications for each step
Show step progress as sequential toast notifications. Rejected because toasts are transient and easy to miss. A persistent overlay in the terminal area is more discoverable and doesn't clutter the notification system.
