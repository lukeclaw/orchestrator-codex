---
title: "Interactive CLI — Picture-in-Picture Terminal for User Interaction"
author: Yudong Qiu
created: 2026-02-28
last_modified: 2026-02-28
status: Proposed
---

# Interactive CLI — Picture-in-Picture Terminal for User Interaction

## Problem

Claude Code workers frequently need user interaction for operations that require interactive terminal input — password prompts (sudo, SSH passphrases, npm login), MFA codes, Git credential entry, interactive installers, or any CLI that reads from stdin. Today, the worker cannot provide an interactive terminal to the user. The workarounds are painful:

1. **Quit Claude Code**, open a separate terminal, run the interactive command, then return to Claude. This breaks flow and loses context.
2. **Open another terminal tab** and SSH into the same machine (for remote workers). This is slow, especially for rdevs that require SSH hop sequences.
3. **Worker gets stuck** waiting for input it cannot provide, blocking progress until the user notices.

These interruptions are especially disruptive in multi-worker orchestration scenarios where the user is managing several workers in the dashboard and doesn't want to context-switch to a raw terminal.

## Goals

- Give each worker the ability to spawn **one** interactive CLI session that the user can see and type into directly from the dashboard
- The interactive CLI should appear as a **picture-in-picture overlay** on the session detail page — visible but not obstructing the main terminal
- **Both Claude and the user** can send commands to the interactive CLI
- Claude has **full visibility** of the interactive CLI output (can read what happened)
- Works for **local workers** (extra tmux window) and **remote workers** (extra tmux window with SSH)
- Either party (Claude or user) can **close** the interactive CLI when done
- **At most one** interactive CLI per worker at any time

## Non-Goals

- General-purpose terminal multiplexer UI (this is a single auxiliary terminal, not a full tmux/screen manager)
- Persistent sessions that survive worker restarts (the interactive CLI is ephemeral)
- Multiple concurrent interactive CLIs per worker
- Replacing the main terminal — the interactive CLI is supplementary

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│  SessionDetailPage                                                       │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  Main Terminal (Claude Code worker)                                │  │
│  │                                                                    │  │
│  │  Claude: I need you to enter your password.                        │  │
│  │  Let me open an interactive terminal for you...                    │  │
│  │                                                        ┌────────┐ │  │
│  │                                                        │ Interac│ │  │
│  │                                                        │ tive   │ │  │
│  │                                                        │ CLI    │ │  │
│  │                                                        │        │ │  │
│  │                                                        │ $ sudo │ │  │
│  │                                                        │ Passwd:│ │  │
│  │                                                        │ ████   │ │  │
│  │                                                        │        │ │  │
│  │                                                        └────────┘ │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  Footer:  [TASK-123 Fix auth]     [Interactive CLI ●]    [Paste]  │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

The interactive CLI is implemented as a **separate tmux window** (`{worker}-icli`) within the `orchestrator` tmux session. This window has its own independent size and PTY, completely isolated from the main worker window. For remote workers, a new SSH connection is established in the new window. The interactive CLI's PTY output is streamed through a dedicated WebSocket endpoint to a floating xterm.js overlay on the frontend.

**Key architectural decision: separate tmux window (not split-pane).** A split-pane approach was considered but rejected because:
- tmux split-panes share the window geometry — resizing the PiP overlay would affect the main worker pane
- The `send_keys_async()` and `resize_async()` functions use `{session}:{window}` targeting, not pane-level targeting
- `get_pane_id_async()` returns only the first pane — routing to a specific pane requires new infrastructure
- A separate window gives the interactive CLI fully independent size control via its own WebSocket resize messages

---

## Detailed Design

### 1. Backend: Interactive CLI Lifecycle

#### 1.1 Data Model

```python
# In orchestrator/state/models.py

@dataclass
class InteractiveCLI:
    session_id: str              # Parent worker session
    window_name: str             # tmux window name (e.g., "worker1-icli")
    status: str                  # "active" | "closed"
    created_at: str              # ISO timestamp
    initial_command: str | None  # Optional command that was run on open
```

The interactive CLI state is kept **in-memory** (dict on the orchestrator) rather than persisted to SQLite, since it's ephemeral and doesn't need to survive restarts.

```python
# In orchestrator/terminal/interactive.py
_active_clis: dict[str, InteractiveCLI] = {}  # session_id -> InteractiveCLI
```

**Startup cleanup**: On server startup, scan for orphaned `*-icli` tmux windows in the `orchestrator` session and kill them. This handles the case where the server crashed/restarted while interactive CLIs were active.

```python
def cleanup_orphaned_icli_windows(tmux_session: str = "orchestrator") -> int:
    """Kill any orphaned *-icli tmux windows from a previous server run."""
    windows = tmux.list_windows(tmux_session)
    killed = 0
    for w in windows:
        if w.name.endswith("-icli"):
            tmux.kill_window(tmux_session, w.name)
            killed += 1
    if killed:
        logger.info("Cleaned up %d orphaned interactive CLI windows", killed)
    return killed
```

#### 1.2 Local Workers

For local workers, the interactive CLI is a **separate tmux window**:

```python
def open_interactive_cli(
    tmux_session: str,
    window_name: str,
    session_id: str,
    command: str | None = None,
    cwd: str | None = None,
) -> InteractiveCLI:
    """Open an interactive CLI for a local worker.

    Creates a new tmux window named '{window_name}-icli'.
    """
    if session_id in _active_clis:
        raise ValueError(f"Interactive CLI already active for session {session_id}")

    icli_window = f"{window_name}-icli"

    # Create a new tmux window
    args = ["new-window", "-d", "-t", tmux_session, "-n", icli_window]
    if cwd:
        args += ["-c", cwd]
    _run_tmux(*args)

    if command:
        target = f"{tmux_session}:{icli_window}"
        _run_tmux("send-keys", "-t", target, command, "Enter")

    cli = InteractiveCLI(
        session_id=session_id,
        window_name=icli_window,
        status="active",
        created_at=datetime.utcnow().isoformat(),
        initial_command=command,
    )
    _active_clis[session_id] = cli
    return cli
```

#### 1.3 Remote Workers

For remote workers, the interactive CLI creates a new tmux window and establishes a fresh SSH connection:

```python
def open_interactive_cli_remote(
    tmux_session: str,
    window_name: str,
    session_id: str,
    host: str,
    command: str | None = None,
    cwd: str | None = None,
) -> InteractiveCLI:
    """Open an interactive CLI for a remote worker.

    Creates a new tmux window, SSHs into the remote host, and optionally
    runs a command. Note: SSH establishment is NOT instant — it goes through
    the full connection handshake. For rdevs this involves 'rdev ssh connect'.
    """
    if session_id in _active_clis:
        raise ValueError(f"Interactive CLI already active for session {session_id}")

    icli_window = f"{window_name}-icli"
    target = f"{tmux_session}:{icli_window}"

    # Create new tmux window
    _run_tmux("new-window", "-d", "-t", tmux_session, "-n", icli_window)

    # SSH into the remote host (same method as main worker setup)
    ssh.remote_connect(tmux_session, icli_window, host)

    # Wait for shell prompt
    if not ssh.wait_for_prompt(tmux_session, icli_window, timeout=30):
        # SSH failed — clean up the window
        _run_tmux("kill-window", "-t", target, check=False)
        raise RuntimeError(f"SSH to {host} timed out for interactive CLI")

    if cwd:
        tmux.send_keys(tmux_session, icli_window, f"cd {cwd}", enter=True)
        time.sleep(0.3)

    if command:
        tmux.send_keys(tmux_session, icli_window, command, enter=True)

    cli = InteractiveCLI(
        session_id=session_id,
        window_name=icli_window,
        status="active",
        created_at=datetime.utcnow().isoformat(),
        initial_command=command,
    )
    _active_clis[session_id] = cli
    return cli
```

**Note on SSH latency**: Unlike the original design which assumed "instant" SSH via ControlMaster, the current codebase does not use SSH ControlMaster. Remote interactive CLI creation involves a full SSH handshake, which may be slow for rdevs. The API response should indicate this to the frontend so it can show a "connecting..." state. This is acceptable since interactive CLI requests are infrequent.

#### 1.4 Close & Liveness

```python
def close_interactive_cli(session_id: str, tmux_session: str = "orchestrator") -> bool:
    """Close the interactive CLI window for a worker."""
    cli = _active_clis.pop(session_id, None)
    if not cli:
        return False

    tmux.kill_window(tmux_session, cli.window_name)
    cli.status = "closed"
    return True


def check_interactive_cli_alive(session_id: str, tmux_session: str = "orchestrator") -> bool:
    """Check if the interactive CLI window still exists in tmux."""
    cli = _active_clis.get(session_id)
    if not cli:
        return False
    if not tmux.window_exists(tmux_session, cli.window_name):
        _active_clis.pop(session_id, None)
        return False
    return True


def get_active_cli(session_id: str) -> InteractiveCLI | None:
    """Get the active interactive CLI for a session, or None."""
    return _active_clis.get(session_id)
```

### 2. Backend: API Endpoints

New routes in `orchestrator/api/routes/interactive_cli.py`:

```python
@router.post("/api/sessions/{session_id}/interactive-cli")
async def open_interactive_cli_endpoint(session_id: str, body: OpenCLIRequest):
    """Open an interactive CLI for a worker session.

    Request body:
        command: Optional[str]  — Command to run immediately
        cwd: Optional[str]      — Working directory (defaults to worker's work_dir)

    Response:
        { "ok": true, "window_name": "worker1-icli" }

    Errors:
        409 — Interactive CLI already active for this session
        404 — Session not found

    Side effect: Broadcasts "interactive_cli_opened" via global WebSocket.
    """

@router.delete("/api/sessions/{session_id}/interactive-cli")
async def close_interactive_cli_endpoint(session_id: str):
    """Close the interactive CLI for a worker session.

    Response: { "ok": true }
    Errors: 404 — No active interactive CLI for this session

    Side effect: Broadcasts "interactive_cli_closed" via global WebSocket.
    """

@router.get("/api/sessions/{session_id}/interactive-cli")
async def get_interactive_cli_status(session_id: str):
    """Get the status of the interactive CLI.

    Response:
        { "active": true, "window_name": "...", "created_at": "...", "initial_command": "..." }
        or
        { "active": false }
    """

@router.post("/api/sessions/{session_id}/interactive-cli/send")
async def send_to_interactive_cli(session_id: str, body: SendRequest):
    """Send input to the interactive CLI (used by Claude workers).

    Request body:
        message: str  — Text to send (with Enter)
        keys: str     — Raw keys to send (without Enter, for Ctrl sequences)
    """

@router.post("/api/sessions/{session_id}/interactive-cli/capture")
async def capture_interactive_cli(session_id: str, lines: int = 30):
    """Capture recent output from the interactive CLI (used by Claude workers).

    Response: { "output": "...", "lines": 30 }
    """
```

### 3. Backend: WebSocket Streaming

#### 3.1 Refactor: Extract reusable streaming logic

The current `terminal_websocket()` in `ws_terminal.py` is a 350-line monolithic function that combines:
1. Session lookup (DB query)
2. tmux target resolution
3. PTY stream subscription
4. Stream batching & flow control
5. WebSocket message handling
6. Drift correction

To support the interactive CLI WebSocket, extract the core streaming logic (items 3-6) into a reusable `stream_pane()` coroutine:

```python
async def stream_pane(
    websocket: WebSocket,
    tmux_sess: str,
    tmux_win: str,
    session_id: str | None = None,  # For user activity tracking (optional)
) -> None:
    """Core terminal streaming loop — reusable for any tmux pane.

    Handles: PTY subscription, stream batching, input relay, resize,
    sync/history, drift correction.
    """
    # ... (extracted from terminal_websocket)
```

Then both endpoints use it:

```python
# Main terminal WebSocket
async def terminal_websocket(websocket: WebSocket, session_id: str):
    await websocket.accept()
    # Session lookup from DB → tmux_sess, tmux_win
    tmux_sess, tmux_win = resolve_session_target(session_id)
    await stream_pane(websocket, tmux_sess, tmux_win, session_id=session_id)

# Interactive CLI WebSocket
@router.websocket("/ws/terminal/{session_id}/interactive")
async def ws_interactive_cli(websocket: WebSocket, session_id: str):
    await websocket.accept()
    cli = get_active_cli(session_id)
    if not cli:
        await websocket.send_json({"type": "error", "message": "No active interactive CLI"})
        await websocket.close(code=4004)
        return
    tmux_sess = "orchestrator"
    await stream_pane(websocket, tmux_sess, cli.window_name)
```

This refactor is the most significant backend change. The existing `terminal_websocket()` function must be split without breaking any behavior.

### 4. Backend: WebSocket Events for Real-Time Frontend Updates

The open/close API endpoints broadcast events through the existing global WebSocket hub so the frontend reacts immediately (no polling needed):

```python
# When interactive CLI is opened:
await broadcast_ws({
    "type": "interactive_cli_opened",
    "session_id": session_id,
    "session_name": session.name,
    "window_name": cli.window_name,
    "command": command,
})

# When interactive CLI is closed:
await broadcast_ws({
    "type": "interactive_cli_closed",
    "session_id": session_id,
})
```

The frontend `AppContext` WebSocket listener handles these events to update interactive CLI state in real-time.

### 5. Backend: Worker CLI Tool

Shell script deployed alongside existing `orch-*` tools:

```bash
#!/bin/bash
# bin/orch-interactive — Open interactive CLI for user interaction
# Usage: orch-interactive [command]
#   orch-interactive                    # Open empty shell
#   orch-interactive "sudo yum install" # Open and run command
#   orch-interactive --close            # Close the interactive CLI
#   orch-interactive --capture          # Capture current output
#   orch-interactive --send "y"         # Send input to interactive CLI
#   orch-interactive --status           # Check if interactive CLI is active

set -e

API_BASE="${ORCH_API_BASE:-http://127.0.0.1:8093}"
SESSION_ID="${ORCH_SESSION_ID}"

if [ -z "$SESSION_ID" ]; then
    echo "Error: ORCH_SESSION_ID not set" >&2
    exit 1
fi

case "${1:-}" in
    --close)
        curl -sf -X DELETE "$API_BASE/api/sessions/$SESSION_ID/interactive-cli" | jq .
        ;;
    --capture)
        LINES="${2:-30}"
        curl -sf -X POST \
            "$API_BASE/api/sessions/$SESSION_ID/interactive-cli/capture?lines=$LINES" \
            | jq -r .output
        ;;
    --send)
        shift
        # Use jq for proper JSON escaping to avoid injection
        PAYLOAD=$(jq -n --arg msg "$*" '{message: $msg}')
        curl -sf -X POST "$API_BASE/api/sessions/$SESSION_ID/interactive-cli/send" \
            -H "Content-Type: application/json" \
            -d "$PAYLOAD" | jq .
        ;;
    --status)
        curl -sf "$API_BASE/api/sessions/$SESSION_ID/interactive-cli" | jq .
        ;;
    *)
        # Open interactive CLI with optional command
        COMMAND="${1:-}"
        if [ -n "$COMMAND" ]; then
            PAYLOAD=$(jq -n --arg cmd "$COMMAND" '{command: $cmd}')
        else
            PAYLOAD="{}"
        fi
        curl -sf -X POST "$API_BASE/api/sessions/$SESSION_ID/interactive-cli" \
            -H "Content-Type: application/json" \
            -d "$PAYLOAD" | jq .
        ;;
esac
```

**Deployment**: The script must be added to `deploy_worker_scripts()` in `orchestrator/agents/__init__.py` so it gets copied alongside the other `orch-*` tools. For already-running remote workers, the tool won't be available until they are recreated.

**Worker prompt update**: Add `orch-interactive` to the worker prompt (`agents/worker/prompt.md`) so Claude knows it exists:

```markdown
### Interactive CLI (`orch-interactive`)

Open an interactive terminal for the user when you need them to enter passwords,
MFA codes, or interact with CLI prompts that require stdin. The terminal appears
as a floating overlay in the dashboard.

**Important**: Claude should avoid sending input while the user is actively typing
in the interactive CLI to prevent keystroke interleaving.

```bash
orch-interactive "sudo yum install screen"  # Open and run command
orch-interactive                             # Open empty shell
orch-interactive --capture                   # Read current output
orch-interactive --send "y"                  # Send non-sensitive input
orch-interactive --close                     # Close when done
orch-interactive --status                    # Check if active
```
```

### 6. Frontend: TerminalView Enhancement

The existing `TerminalView` component hardcodes the WebSocket URL:

```js
const ws = new WebSocket(`${proto}//${location.host}/ws/terminal/${sessionId}`)
```

Add an optional `wsPath` prop to allow custom WebSocket paths:

```tsx
interface Props {
  sessionId: string
  wsPath?: string              // Custom WebSocket path (default: /ws/terminal/{sessionId})
  sessionStatus?: string
  disableScrollback?: boolean
  // ... existing props
}

// In connectWebSocket:
const path = wsPath || `/ws/terminal/${sessionId}`
const ws = new WebSocket(`${proto}//${location.host}${path}`)
```

Additionally, the interactive CLI doesn't need:
- Image paste handling → pass `onImagePaste={undefined}`
- Long text paste handling → pass `onTextPaste={undefined}`
- Session status locking → don't pass `sessionStatus`
- Reconnection backoff is fine (handles WebSocket drops gracefully)

### 7. Frontend: Picture-in-Picture UI

#### 7.1 InteractiveCLI Overlay Component

```tsx
// frontend/src/components/terminal/InteractiveCLI.tsx

interface Props {
  sessionId: string
  onClose: () => void
}

export default function InteractiveCLI({ sessionId, onClose }: Props) {
  const [isExpanded, setIsExpanded] = useState(false)
  const [isFocused, setIsFocused] = useState(false)

  const handleClose = async () => {
    try {
      await api(`/api/sessions/${sessionId}/interactive-cli`, { method: 'DELETE' })
    } catch { /* ignore */ }
    onClose()
  }

  return (
    <div
      className={`icli-overlay ${isFocused ? 'icli-focused' : ''} ${isExpanded ? 'icli-expanded' : ''}`}
      onFocus={() => setIsFocused(true)}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget)) setIsFocused(false)
      }}
    >
      <div className="icli-titlebar">
        <span className="icli-title">Interactive CLI</span>
        <div className="icli-controls">
          <button onClick={() => setIsExpanded(!isExpanded)} title="Toggle size">
            {isExpanded ? 'Collapse' : 'Expand'}
          </button>
          <button onClick={handleClose} title="Close">Close</button>
        </div>
      </div>
      <div className="icli-terminal">
        <TerminalView
          sessionId={sessionId}
          wsPath={`/ws/terminal/${sessionId}/interactive`}
        />
      </div>
    </div>
  )
}
```

#### 7.2 Layout & Positioning

**Default size**: 560x360 (roughly 70 cols x 22 rows — usable for most CLI prompts).

```css
.icli-overlay {
  position: absolute;
  bottom: 48px;
  right: 12px;
  width: 560px;
  height: 360px;
  z-index: 100;
  border-radius: 8px;
  border: 1px solid var(--border-subtle);
  background: var(--bg-primary);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  animation: icli-slide-in 0.2s ease-out;
}

.icli-overlay.icli-expanded {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 48px;
  width: 50%;
  height: auto;
  border-radius: 0;
}

.icli-overlay.icli-focused {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px var(--accent), 0 8px 32px rgba(0, 0, 0, 0.4);
}

.icli-titlebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 8px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-subtle);
  user-select: none;
  flex-shrink: 0;
}

.icli-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.icli-terminal {
  flex: 1;
  min-height: 0;
}

@keyframes icli-slide-in {
  from { opacity: 0; transform: translateY(12px) scale(0.95); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}
```

#### 7.3 SessionDetailPage Integration

The `SessionDetailPage` listens for interactive CLI events and manages the overlay:

```tsx
// In SessionDetailPage.tsx

const [icliActive, setIcliActive] = useState(false)
const [showICLI, setShowICLI] = useState(false)

// Check interactive CLI status on mount and on WebSocket events
useEffect(() => {
  if (!id) return
  api<{ active: boolean }>(`/api/sessions/${id}/interactive-cli`)
    .then(r => { setIcliActive(r.active); if (r.active) setShowICLI(true) })
    .catch(() => {})
}, [id])

// Listen for WebSocket events (from AppContext)
// "interactive_cli_opened" → setIcliActive(true), setShowICLI(true)
// "interactive_cli_closed" → setIcliActive(false), setShowICLI(false)
```

#### 7.4 Footer Badge

```tsx
{icliActive && (
  <button
    className={`sd-icli-badge ${showICLI ? 'active' : ''}`}
    onClick={() => setShowICLI(!showICLI)}
    title="Toggle interactive CLI"
  >
    <span className="sd-icli-dot" />
    Interactive CLI
  </button>
)}
```

#### 7.5 Global Indicator (Sidebar)

To address the case where the user is on a different page, extend the Sidebar to show which workers have active interactive CLIs:

- The sidebar "Workers" count badge already shows total count (e.g., "Workers 8")
- Add a secondary indicator when any worker has an active interactive CLI: "Workers 8 (1 input needed)"
- The AppContext already tracks sessions; add a lightweight `interactive_cli_sessions: Set<string>` field updated by WebSocket events
- Clicking the indicator navigates to that worker's session detail page

#### 7.6 Notification on Open

When a worker opens an interactive CLI, the existing notification system sends a toast:

```
Worker "auth-fix" needs interactive input
"sudo yum install screen"
[Show]  [Dismiss]
```

Clicking "Show" navigates to the session detail page. The notification uses `orch-notify` infrastructure — the open API endpoint creates a notification automatically.

### 8. Worker Interaction Flow

#### 8.1 Claude Opens Interactive CLI

```
Claude (worker):
  "I need to install a package that requires sudo. Let me open an
   interactive terminal for you to enter your password."

  [Calls: orch-interactive "sudo yum install screen"]

  "I've opened an interactive CLI. You should see it as a floating
   terminal on the dashboard. Please enter your password when prompted."
```

#### 8.2 User Enters Password

1. User sees toast notification + sidebar badge changes
2. User navigates to worker's session page (or is already there)
3. PiP overlay appears showing `sudo yum install screen` with password prompt
4. User clicks on the PiP terminal and types password
5. Command completes

#### 8.3 Claude Monitors and Closes

```
Claude (worker):
  [Calls: orch-interactive --capture]
  # Output shows: "Complete! 3 packages installed."

  [Calls: orch-interactive --close]

  "Package installed successfully. I've closed the interactive terminal."
```

#### 8.4 User Can Also Close

- Click the "Close" button on the PiP overlay title bar
- Sends `DELETE /api/sessions/{id}/interactive-cli`
- tmux window is killed, overlay disappears, WebSocket event broadcast

### 9. Edge Cases & Error Handling

#### 9.1 Only One Interactive CLI Per Worker

The backend enforces max one per session. 409 Conflict on duplicate open attempts:

```
$ orch-interactive "npm login"
Error: Interactive CLI already active. Close it first with: orch-interactive --close
```

#### 9.2 Interactive CLI Process Exits

The shell in the tmux window remains open after a command exits. The window stays alive (showing shell prompt). Claude or user must explicitly close it.

Background liveness check (every 5s in the monitor loop) uses `tmux.window_exists()`:

```python
def check_interactive_cli_alive(session_id: str, tmux_session: str = "orchestrator") -> bool:
    cli = _active_clis.get(session_id)
    if not cli:
        return False
    if not tmux.window_exists(tmux_session, cli.window_name):
        _active_clis.pop(session_id, None)
        # Broadcast close event
        return False
    return True
```

#### 9.3 Worker Session Deleted/Stopped

Session deletion/stop handlers call `close_interactive_cli(session_id)`. No-op if none active.

#### 9.4 SSH Disconnection (Remote)

If SSH drops in the interactive CLI window:
- The tmux window shows the SSH disconnect message
- The window still exists → liveness check passes
- User sees the disconnect in the PiP overlay
- User/Claude can close the dead CLI and open a new one

#### 9.5 Concurrent Input from Claude and User

Both parties can send input. If Claude calls `orch-interactive --send "y"` while the user is typing, keystrokes may interleave. The worker prompt documents this: "Claude should avoid sending input while the user is actively typing." This is inherent to shared terminals and not a solvable problem without a turn-taking protocol, which would over-complicate the design.

#### 9.6 tmux Window Visibility

The `{worker}-icli` tmux windows are visible if the user runs `tmux attach-session -t orchestrator`. This is by design — the user can interact with the interactive CLI directly via tmux if they prefer, just like they can with worker windows today.

### 10. Security Considerations

- **Password visibility**: Password input is handled by the remote terminal's TTY. Programs that suppress echo (sudo, SSH, `read -s`) will not transmit password characters through the PTY stream. However, **not all programs suppress echo** — some CLI tools echo passwords in cleartext. The interactive CLI provides the same visibility as looking at the terminal directly; it does not add any additional exposure.
- **Claude visibility**: Claude can use `orch-interactive --capture` to read terminal output. This captures what is visible on screen — same as a human looking at the terminal. Password characters suppressed by the TTY will not appear.
- **No credential forwarding**: The interactive CLI does not automatically forward any credentials. It provides a raw terminal.

### 11. Implementation Plan

#### Phase 1: Backend + CLI Tool

1. `orchestrator/terminal/interactive.py` — Lifecycle manager (open/close/capture/send/status/cleanup)
2. `orchestrator/api/routes/interactive_cli.py` — REST API endpoints
3. Refactor `ws_terminal.py` — Extract `stream_pane()` from `terminal_websocket()`
4. Add `/ws/terminal/{session_id}/interactive` WebSocket endpoint
5. WebSocket broadcast events (interactive_cli_opened / interactive_cli_closed)
6. `orch-interactive` shell script + add to `deploy_worker_scripts()`
7. Update `agents/worker/prompt.md` with `orch-interactive` documentation
8. Startup cleanup of orphaned `-icli` windows in `lifecycle.py`
9. Session delete/stop handlers call `close_interactive_cli()`

#### Phase 2: Frontend

10. Add `wsPath` prop to `TerminalView` component
11. `InteractiveCLI.tsx` — PiP overlay component
12. `InteractiveCLI.css` — Overlay styles
13. `SessionDetailPage.tsx` — Footer badge, overlay toggle, WebSocket event handling
14. `AppContext.tsx` — Track `interactiveCliSessions` set from WebSocket events
15. `Sidebar.tsx` — Global indicator for workers needing input
16. Toast notification on interactive CLI open

#### Phase 3: Polish

17. Keyboard shortcut `Ctrl+Shift+I` to toggle overlay visibility
18. Background liveness check integrated into monitor loop
19. Tests: unit tests for `interactive.py`, API endpoint tests, WebSocket tests

### 12. Alternatives Considered

#### A. Dedicated terminal window in the OS

Open a native terminal window (Terminal.app, iTerm2) with the right SSH command.
Rejected: requires detecting user's terminal app, doesn't work in Tauri, no Claude visibility.

#### B. tmux attach from a separate process

User runs `tmux attach-session -t orchestrator:worker-icli` in their own terminal.
Rejected: requires the user to open another terminal (the problem we're solving).

#### C. Web-based terminal popup (new browser tab)

Open a new browser tab/popup with a full terminal view.
Rejected: pop-up blockers, disconnected context, focus coordination overhead.

#### D. Inline split (not floating)

Split the terminal area to show both terminals side by side.
Tradeoff: more space but permanently shrinks main terminal. Could be offered as "docked" mode later.

#### E. tmux split-pane (instead of separate window)

Create a second pane in the worker's tmux window.
Rejected: pane-level targeting not supported by async helpers, shared geometry causes resize conflicts, `get_pane_id_async()` only returns first pane.

---

## Summary

The Interactive CLI feature adds a lightweight, picture-in-picture auxiliary terminal to each worker session. The backend creates a dedicated tmux window (`{worker}-icli`), streams it via a new WebSocket endpoint (sharing the refactored streaming core with the main terminal), and exposes it as a CLI tool (`orch-interactive`). The frontend renders it as a floating overlay with full xterm.js terminal emulation. Both the user and Claude have full read/write access. WebSocket events provide real-time state propagation, and a global sidebar indicator ensures workers needing input are never missed — even when the user is on a different page.
