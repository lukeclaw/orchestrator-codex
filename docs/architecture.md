# Architecture

A technical reference for the Claude Orchestrator's architectural design, applied principles, critical design choices, and a map of where to find logic in the codebase.

---

## 1. Design Principles

- **Zero hard-coded context.** The orchestrator is domain-agnostic. All project knowledge, instructions, and conventions are user-defined and stored in the database. No assumptions about what you're building.
- **Simplicity first.** Prefer fewer moving parts. SQLite over Postgres. tmux over a custom process manager. Single Python process over microservices. The system should be understandable by one person.
- **Local-only.** No cloud dependencies. Everything runs on the user's machine. Remote workers communicate via SSH — a protocol the user already trusts and understands.
- **Fail gracefully.** SSH drops, tmux dies, /tmp gets wiped, rdev reboots. Every failure mode has a recovery path. The system should self-heal where possible and alert the user where not.
- **tmux as the ground truth.** Claude Code processes live in tmux. If the orchestrator crashes and restarts, tmux sessions survive. The user can always `tmux attach` as a last resort. The orchestrator reads tmux state — it doesn't own it.
- **Explicit actions.** The orchestrator never takes destructive actions silently. Auto-approval has a safety allowlist. Reconnect never sends keystrokes to an active TUI. Database restores require explicit confirmation.

---

## 2. Tech Stack

| Layer | Technology | Role |
|-------|-----------|------|
| **Backend** | Python 3.13, FastAPI, uvicorn | REST API, WebSocket handlers, orchestration logic |
| **Database** | SQLite (WAL mode) | Single-file persistence, crash-safe, concurrent reads |
| **Process management** | tmux | Session multiplexer for local Claude Code processes |
| **Remote access** | SSH (tunnels, port forwarding) | Connectivity to rdev VMs |
| **Remote daemon** | RWS (Python, TCP) | PTY sessions and file ops on remote hosts |
| **Frontend** | React 18, TypeScript, Vite | Single-page dashboard application |
| **Terminal rendering** | xterm.js | In-browser terminal emulation with full TUI support |
| **Charts** | Recharts | Trend visualizations (throughput, heatmap, utilization) |
| **Desktop packaging** | Tauri (Rust) | Native macOS app shell with auto-update |
| **Styling** | Plain CSS (custom design system) | Dark theme, CSS custom properties, no framework |

---

## 3. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Browser / Tauri WebView                                                │
│                                                                         │
│  React SPA (localhost:5173)                                             │
│  ├─ REST API calls ──────────────────────┐                              │
│  ├─ WebSocket /ws/state (JSON) ──────────┤                              │
│  ├─ WebSocket /ws/terminal/{id} (binary) ┤                              │
│  └─ WebSocket /ws/browser-view/{id} ─────┤                              │
└───────────────────────────────────────────┼─────────────────────────────┘
                                            │
                                            ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  FastAPI Server (localhost:8093)                                         │
│                                                                         │
│  ├─ REST Routes     (sessions, tasks, projects, prs, rdevs, context...) │
│  ├─ WebSocket       (terminal streaming, state broadcast, browser view) │
│  ├─ Orchestrator    (monitor loop, tunnel health loop, event bus)       │
│  ├─ Human Tracker   (activity heartbeat → interval tracking)            │
│  ├─ State Layer     (SQLite DB, repositories, migrations)               │
│  └─ Session Layer   (health checks, reconnect pipeline, tunnel mgmt)    │
│                                                                         │
│  ┌──────────────┐    ┌──────────────────┐    ┌───────────────────────┐  │
│  │ tmux session  │    │ SSH tunnels      │    │ PtyStreamPool         │  │
│  │ (local workers│    │ (reverse + fwd)  │    │ (FIFO readers,        │  │
│  │  in windows)  │    │                  │    │  fan-out to WS)       │  │
│  └──────┬───────┘    └────────┬─────────┘    └───────────────────────┘  │
└─────────┼─────────────────────┼─────────────────────────────────────────┘
          │                     │
          │ (local)             │ SSH -R (reverse) / -L (forward)
          ▼                     ▼
┌──────────────┐    ┌──────────────────────────────────────────────────────┐
│ Local Claude  │    │  Remote Host (rdev)                                  │
│ Code process  │    │                                                      │
│ (in tmux pane)│    │  ┌─────────────────────────────────────────────┐    │
└──────────────┘    │  │ RWS Daemon (port 9741)                       │    │
                     │  │  ├─ PTY sessions (Claude Code processes)     │    │
                     │  │  ├─ File operations (list, read, stat)       │    │
                     │  │  └─ 64KB ringbuffer per PTY                  │    │
                     │  └─────────────────────────────────────────────┘    │
                     │                                                      │
                     │  Hooks/CLI → SSH reverse tunnel → Orchestrator API   │
                     └──────────────────────────────────────────────────────┘
```

---

## 4. Backend Architecture

### 4.1 `orchestrator/api/` — HTTP & WebSocket Layer

The FastAPI application, route handlers, and real-time communication.

- **`app.py`** — Application factory. Defines the `lifespan` context manager that handles startup (DB migrations, tmux reconciliation, tunnel recovery, human tracker start, orchestrator engine start) and shutdown (human tracker stop, orchestrator stop). Mounts static files, CORS middleware, and all route routers.
- **`deps.py`** — FastAPI dependency injection: database connection and config access.
- **`websocket.py`** — State broadcast WebSocket (`/ws/state`). Pushes JSON messages for session status changes, task updates, and notifications to all connected clients. Also handles `user_activity` heartbeat messages for human-hours tracking and `focus_update` messages for page focus tracking.
- **`ws_terminal.py`** — Terminal streaming WebSocket (`/ws/terminal/{session_id}`). Handles both local (pipe-pane via PtyStreamPool) and remote (RWS PTY stream) sessions. Manages initial sync (capture-pane snapshot), binary frame output, and text frame input (send-keys). Also handles interactive CLI WebSocket connections. Terminal input triggers human activity heartbeats.
- **`ws_browser_view.py`** — Browser view WebSocket (`/ws/browser-view/{session_id}`). Relays CDP screencast frames (JPEG) and input events between the frontend canvas and the CDP proxy.

**Route modules** (`api/routes/`):

| Route file | Endpoints | Purpose |
|------------|-----------|---------|
| `sessions.py` | CRUD, start/stop/pause, health check, reconnect | Worker session management |
| `tasks.py` | CRUD, assign, status transitions, subtasks | Task lifecycle |
| `projects.py` | CRUD, list with stats | Project management |
| `brain.py` | Start/stop, status, paste, deploy, redeploy, heartbeat scheduling | Brain agent management |
| `context.py` | CRUD, scope filtering | Context item management |
| `skills.py` | CRUD, enable/disable, list built-in | Skill management |
| `notifications.py` | List, dismiss, create | Notification management |
| `prs.py` | Search (active/recent), attention levels | PR triage dashboard data |
| `pr_preview.py` | Detail fetch, reviews, files, checks | Single PR detail for expanded view |
| `rdevs.py` | List, create, delete, restart, stop | Remote dev instance management |
| `files.py` | List dir, read file, stat, search | File explorer (local + remote via RWS) |
| `browser_view.py` | Start/stop/status, CDP tunnel setup | Remote browser view lifecycle |
| `interactive_cli.py` | Open/close, send input, capture | Interactive CLI sessions |
| `trends.py` | Throughput, activity, worker-hours, human-hours, detail | Dashboard trend data |
| `backup.py` | Create/restore/list/delete/download | Database backup management |
| `paste.py` | Smart paste handling | Clipboard intelligence |
| `settings.py` | Get/set config values | Application settings |
| `dashboard.py` | SPA fallback | Serves index.html for React Router |
| `updates.py` | Version check | Auto-update support |

### 4.2 `orchestrator/core/` — Engine & Coordination

The central orchestration logic that ties the system together.

- **`orchestrator.py`** — The `Orchestrator` class. Starts two background async tasks: the monitor loop (polls tmux state, updates DB) and the tunnel health loop (checks SSH tunnel liveness). Subscribes to the event bus. Supports hot-swapping the DB connection (for backup restore).
- **`events.py`** — Lightweight pub/sub event bus. `publish(event_type, data)` and `subscribe(pattern, callback)`. Supports wildcard subscriptions (`"*"`). Used internally for loose coupling between components.
- **`lifecycle.py`** — Startup and shutdown procedures. `startup_check()` reconciles the DB with tmux reality (marks sessions as disconnected if their tmux window is gone). `recover_tunnels()` re-adopts or restarts SSH tunnels after an orchestrator restart. `shutdown()` for clean exit.
- **`state_manager.py`** — Centralized state change handler. Processes session status transitions and triggers side effects (notifications, event broadcasting, auto-reconnect).
- **`human_tracker.py`** — Background async task that tracks operator activity. Receives heartbeats (in-memory timestamp updates) from WebSocket and terminal input handlers. Polls every 30s to manage activity intervals in the DB: starts a new interval when the user becomes active, closes it after 5 minutes of inactivity. Handles graceful shutdown (closing open intervals) and crash recovery (closing stale intervals on startup).

### 4.3 `orchestrator/terminal/` — tmux & Terminal Management

Everything related to terminal sessions, tmux interaction, and output processing.

- **`manager.py`** — Low-level tmux commands: `create_session`, `create_window`, `send_keys`, `capture_pane`, `list_windows`, `resize_pane`, `kill_window`. All subprocess-based (`tmux` CLI).
- **`session.py`** — High-level session setup. `create_local_session()` creates a tmux window, deploys agent files to `/tmp/orchestrator/`, and launches Claude Code. `create_remote_session()` does the equivalent via SSH + RWS.
- **`pty_stream.py`** — The PTY streaming engine. `PtyStreamReader` reads raw bytes from a tmux `pipe-pane` FIFO. `PtyStreamPool` manages one reader per session and fans out to multiple WebSocket subscribers. Handles FIFO lifecycle, backpressure, and cleanup.
- **`control.py`** — tmux control-mode connection (`tmux -C`). Used for sending keys, resizing panes, and other control operations. Not used for output streaming (that's pipe-pane).
- **`output_parser.py`** — Regex-based analysis of terminal content. Detects Claude Code states: idle prompt, working (TUI active), permission prompt (waiting), error messages. Used by health checks and auto-approval.
- **`monitor.py`** — The background monitor loop. Periodically polls tmux pane content, runs the output parser, and updates session status in the DB.
- **`markers.py`** — Terminal marker system for detecting specific output patterns and coordinating between input and output.
- **`interactive.py`** — Interactive CLI session management. Opens/closes auxiliary terminal sessions (tmux window for local, RWS PTY for remote). Manages the lifecycle and I/O routing.
- **`remote_worker_server.py`** — The RWS daemon script (deployed to remote hosts) and the client class (`RemoteWorkerServer`). The daemon handles PTY creation/management, file operations, and health pings over TCP/JSON-lines. The client manages connection pooling, auto-reconnect, forward tunnel management, and socket lifecycle.
- **`ssh.py`** — SSH utility functions: host type detection (`is_remote_host`), SSH command construction.
- **`file_sync.py`** — File synchronization utilities for deploying agent configs to remote hosts.
- **`claude_update.py`** — Claude Code version management and update detection.

### 4.4 `orchestrator/session/` — Health, Reconnect & Tunnels

The resilience layer that keeps workers alive across failures.

- **`health.py`** — Comprehensive health check system. Checks process liveness, SSH reachability, PTY responsiveness, and /tmp file integrity. `check_worker_health()` for individual workers, `check_all_workers_health()` for the periodic sweep. Includes the manifest-based /tmp recovery (`ensure_tmp_dir_health`, `ensure_brain_tmp_health`). Per-host circuit breaker (`_HostCircuitBreaker`) prevents health check storms against unreachable hosts.
- **`reconnect.py`** — The reconnect pipeline. `reconnect_local_worker()` and `reconnect_remote_worker()` implement sequential recovery: verify SSH → check tunnel → ensure RWS daemon → verify PTY → verify Claude process. Uses non-intrusive probes (`alternate_on` detection) and per-session `asyncio.Lock` to prevent concurrent reconnects. Tracks `reconnect_step` in the DB for frontend progress display. Per-session attempt counting with backoff prevents infinite retry loops.
- **`tunnel.py`** — `ReverseTunnelManager` — manages SSH reverse tunnels (`-R`) for rdev API access and forward tunnels (`-L`) for RWS/CDP. Tracks tunnel PIDs, provides startup/recovery/teardown, and handles the `ClearAllForwardings` SSH quirk.
- **`tunnel_monitor.py`** — Background loop that periodically verifies tunnel health and restarts dead tunnels. Uses a consecutive failure counter (max 5) to give up on persistently failing tunnels.
- **`state_machine.py`** — Session state machine defining valid status transitions (e.g., idle→working, working→disconnected, disconnected→connecting).

### 4.5 `orchestrator/browser/` — CDP Proxy

Remote browser viewing via Chrome DevTools Protocol.

- **`cdp_proxy.py`** — The main CDP proxy. Connects to a remote Chromium's CDP WebSocket (tunneled via SSH -L to localhost:9222). Starts `Page.startScreencast` for JPEG frame streaming. Relays `Input.dispatchMouseEvent` and `Input.dispatchKeyEvent` for user interaction. Manages frame acknowledgment and quality settings.
- **`cdp_worker_proxy.py`** — Worker-side CDP proxy for scenarios where the worker's Playwright MCP shares the browser instance.

### 4.6 `orchestrator/state/` — Data Layer

SQLite database, schema migrations, and data access.

- **`db.py`** — Database connection management. `get_connection()` returns a WAL-mode SQLite connection with busy timeout. `ConnectionFactory` creates fresh connections for write operations (avoids lock contention with the main read connection). `with_retry` decorator for transient lock errors.
- **`models.py`** — Plain dataclasses mapping to DB tables: `Project`, `Session`, `Task`, `Config`, `ContextItem`, `Notification`, `Skill`, `InteractiveCLI`.
- **`migrations/`** — Numbered SQL migration files (`001_initial.sql` through `036_add_task_to_status_events.sql`). Applied in order by `migrations/runner.py` on startup. Idempotent (uses `CREATE TABLE IF NOT EXISTS`, etc.). Key migrations include: `027_add_status_events` (trends data), `031_add_rws_pty_id` (remote PTY tracking), `035_add_human_activity_events` (operator hours), `036_add_task_to_status_events` (per-interval task context).
- **`repositories/`** — Data access layer. One module per entity:

| Repository | Purpose |
|------------|---------|
| `sessions.py` | Session CRUD, status updates, reconnect fields |
| `tasks.py` | Task CRUD, subtask queries, assignment |
| `projects.py` | Project CRUD, stats aggregation |
| `config.py` | Key-value configuration |
| `context.py` | Context item CRUD with scope/category filtering |
| `notifications.py` | Notification CRUD, PR comment metadata |
| `skills.py` | Skill CRUD (custom + built-in discovery) |
| `status_events.py` | Status transition log, trend aggregations (throughput, heatmap, worker-hours with detail and task context) |
| `human_activity.py` | Activity interval CRUD, human-hours aggregation with cross-midnight splitting, stale interval recovery, cleanup |

### 4.7 `orchestrator/agents/` — Agent Deployment

Configuration and file deployment for Claude Code agents.

- **`deploy.py`** — Single Source of Truth (SOT) functions for agent tmp directories. `deploy_worker_tmp_contents()` and `deploy_brain_tmp_contents()` create the complete set of files an agent needs: bin scripts (CLI commands like `orch-notify`, `orch-memory`), hooks (pre/post-tool, pre-compact, session-start), settings, built-in skills, custom skills from DB, and `prompt.md` (with `{{BRAIN_MEMORY}}` wisdom injection for the brain). Each writes a `.manifest.json` for health verification.

### 4.8 `orchestrator/` — Top-Level Modules

- **`main.py`** — Application entry point. Loads config, initializes logging, starts uvicorn.
- **`launcher.py`** — Process launcher for starting the server in various modes (standalone, Tauri sidecar).
- **`paths.py`** — Centralized path definitions for DB, config, tmp dirs, agent dirs.
- **`backup.py`** — Database backup/restore logic. Creates timestamped copies, handles connection swapping for live restore.
- **`utils.py`** — Small shared utilities.

---

## 5. Frontend Architecture

### 5.1 Entry Point & Routing

- **`main.tsx`** — React root. Wraps the app in `AppProvider` (global state context) and `BrowserRouter`.
- **`App.tsx`** — Route definitions using React Router. Maps URL paths to page components.

**Routes:**

| Path | Page Component | Purpose |
|------|---------------|---------|
| `/` | `DashboardPage` | Overview: stats, activity, worker grid, trends |
| `/projects` | `ProjectsPage` | Project list with search/filter |
| `/projects/:id` | `ProjectDetailPage` | Project detail with tasks |
| `/tasks` | `TasksPage` | All tasks with table + kanban board views |
| `/tasks/:id` | `TaskDetailPage` | Task detail with terminal, notes, subtasks, artifacts |
| `/prs` | `PRsPage` | PR triage dashboard with attention levels |
| `/workers` | `WorkersPage` | Worker grid with status, actions |
| `/workers/rdevs` | `WorkersPage` | Rdev management tab |
| `/workers/:id` | `SessionDetailPage` | Live terminal, file explorer, browser view |
| `/context` | `ContextPage` | Context item management |
| `/skills` | `SkillsPage` | Skill management |
| `/notifications` | `NotificationsPage` | Notification list |
| `/settings` | `SettingsPage` | Application settings |

### 5.2 State Management

- **`AppContext`** (`context/AppContext.tsx`) — Global state provider. Holds: sessions (workers), projects, tasks, notifications, connection status. Establishes the WebSocket connection to `/ws/state` and processes incoming messages to update state. Provides auto-reconnect with exponential backoff. Sends throttled `user_activity` heartbeats on user interaction (click, keyboard, scroll) for human-hours tracking. Triggers periodic health checks.
- **Page-local hooks** — Each page uses custom hooks for data fetching and local state: `useTrends`, `useSkills`, `useContextItems`, `useSettings`, etc. These hooks encapsulate API calls and polling logic.

### 5.3 Component Organization

```
frontend/src/
├── api/              # API client functions (fetch wrappers), types.ts
├── components/
│   ├── brain/        # BrainPanel (resizable side panel), BrainTerminal
│   ├── browser/      # BrowserView (CDP screencast canvas)
│   ├── common/       # Shared: Modal, ConfirmPopover, CustomSelect, SlidingTabs,
│   │                 #   ErrorBoundary, Markdown, TimeAgo, Icons, SmartPastePopup,
│   │                 #   NotificationToast, TagDropdown, GettingStartedModal, linkify
│   ├── context/      # ContextModal (create/edit)
│   ├── dashboard/    # TrendsPanel, TrendDetailModal, ThroughputChart,
│   │                 #   WorkerHoursChart (dual Y-axis with human hours),
│   │                 #   WorkerHeatmap, RecentActivity, CollapsiblePanel
│   ├── file-explorer/ # File tree, file viewer, three-pane layout
│   ├── layout/       # Header, StatsBar
│   ├── projects/     # ProjectsTable, ProjectCard, ProjectForm, ProjectEditModal
│   ├── rdevs/        # RdevTable, CreateRdevModal
│   ├── sessions/     # AddSessionModal
│   ├── sidebar/      # Sidebar (nav items, badges), SidebarItem
│   ├── skills/       # SkillCard, SkillModal
│   ├── tasks/        # TaskBoard (kanban), TaskTable, TaskCard, TaskForm,
│   │                 #   TaskWorkerPreview, AssignTaskModal, WorkerAssignModal,
│   │                 #   PrPreviewCard, TaskNotificationsCard, TaskLinksCard,
│   │                 #   TaskSubtasksCard, prUtils
│   ├── terminal/     # TerminalView (with reconnect overlay), InteractiveCLI
│   └── workers/      # WorkerCard, WorkerCardCompact
├── context/          # React context providers (AppContext)
├── hooks/            # Custom React hooks
├── layouts/          # AppLayout (sidebar + main + brain panel)
├── pages/            # Page-level components (one per route)
└── styles/           # global.css (design system tokens, base styles)
```

### 5.4 Real-Time Communication

| Channel | Protocol | Data Format | Purpose |
|---------|----------|-------------|---------|
| `/ws/state` | WebSocket | JSON | State broadcast: session updates, task changes, notifications, user activity heartbeats |
| `/ws/terminal/{id}` | WebSocket | Binary (output), Text (input) | Live terminal streaming |
| `/ws/browser-view/{id}` | WebSocket | Binary (JPEG frames), Text (input events) | Remote browser screencast |
| `/ws/interactive-cli/{id}` | WebSocket | Binary + Text | Interactive CLI terminal |

The frontend's `AppContext` establishes the state WebSocket on mount and processes messages to update React state. Terminal WebSockets are established per-session when the user navigates to a session detail page.

---

## 6. Critical Design Choices

### tmux as Session Manager

Claude Code processes run inside tmux windows, not as direct child processes of the orchestrator. This means:
- **Crash resilience:** If the orchestrator crashes and restarts, all Claude sessions are still alive in tmux. Startup reconciliation re-discovers them.
- **Escape hatch:** The user can always `tmux attach -t orchestrator` and interact with workers directly if the web UI is unavailable.
- **pipe-pane is read-only:** The `pipe-pane -O` tap copies PTY bytes to a FIFO without modifying the stream. The terminal works identically whether or not the orchestrator is reading.

### pipe-pane over control-mode for Output Streaming

Early versions used tmux control-mode (`%output` events) for terminal streaming. This caused TUI frame tearing because control-mode fragments output into lines and octal-encodes escape sequences. `pipe-pane -O` taps the raw PTY byte stream — exactly what the terminal sees — eliminating all rendering corruption. Control-mode is still used for commands (send-keys, resize) but not output.

### SQLite with WAL Mode

- **Single file:** No database server to install, configure, or maintain. The DB is just a file on disk.
- **WAL mode:** Allows concurrent readers while a writer holds the lock. The monitor loop and API handlers can read simultaneously.
- **ConnectionFactory:** Write operations create fresh connections via a factory to avoid blocking the main read connection. A `with_retry` decorator handles transient `SQLITE_BUSY` errors.
- **Migrations:** Numbered SQL files (001-036) applied sequentially on startup. Idempotent DDL ensures safe re-runs.

### Reverse Tunnels for rdev Communication

Workers on remote hosts need to call the orchestrator API (for hooks, CLI commands, notifications). Rather than exposing the orchestrator to the network, we use SSH reverse tunnels:
- The orchestrator creates `ssh -R 8093:localhost:8093 rdev-host`, making the API available at `localhost:8093` on the remote machine.
- Worker scripts call `curl http://localhost:8093/api/...` — they don't need to know the orchestrator's real address.
- Forward tunnels (`-L`) provide the reverse direction: local access to RWS (9741) and CDP (9222) on the remote host.

### Non-Intrusive Reconnect Pipeline

The reconnect system is designed around one critical invariant: **never send keystrokes to a pane with an active TUI.** If Claude Code's TUI is running and you send shell commands, those characters appear as user input in Claude's interface — a catastrophic failure mode.

The solution:
1. Detect TUI state by checking `alternate_on` (terminal alternate screen mode) via capture-pane metadata.
2. If TUI is active, the Claude process is alive — skip to verification, don't try to relaunch.
3. If TUI is not active, the pane is at a shell prompt — safe to send commands.
4. Each reconnect step is sequential and atomic: fix SSH, then tunnel, then daemon, then PTY, then Claude.
5. Each step writes `reconnect_step` to the DB so the frontend can show progress.
6. Per-session attempt counting prevents infinite reconnect loops.

### Agent Deployment SOT

All agent configuration files (/tmp/orchestrator/{session_id}/) are generated by two canonical functions. This eliminates the bug class where different code paths (initial setup, reconnect, health recovery) produce slightly different file sets. The manifest file enables fast health verification without re-deploying.

### Brain Autonomy

The brain supports optional autonomous monitoring and long-term memory:

- **Heartbeat loop**: When `brain.heartbeat` setting is enabled, `start_brain()` sends `/loop {interval} /heartbeat` after Claude launches. Claude Code's built-in `/loop` handles scheduling. The heartbeat skill (`agents/brain/skills/heartbeat.md`) is non-blocking — takes safe actions immediately, notifies user for awareness, investigates stuck workers via inline `/unblock` procedure.
- **Brain memory (`orch-memory`)**: A CLI wrapper over the context API with pre-set `scope=brain` and `category=memory|wisdom`. Learning logs (`category=memory`) are raw notes. Wisdom (`category=wisdom`) is a curated doc injected into the brain's system prompt via `{{BRAIN_MEMORY}}` at deploy time (see `_get_brain_memory_from_db()` in `deploy.py`).
- **Hooks**: `PreCompact` prompts the brain to save learnings before context wipe. `SessionStart` re-deploys files (refreshing wisdom injection) and re-arms the heartbeat loop after `/clear` or `/compact`.
- **Context page "Brain Memory" tab**: Read-only view of wisdom + learning logs. Filters `context_items` by `scope=brain, category IN (memory, wisdom)`. The regular Context tab excludes these items via `excludeScopeCategories` in the `useContextItems` hook.

### Human Activity Tracking

The operator's active time is tracked via a lightweight heartbeat → interval model:
- Frontend sends throttled `user_activity` WebSocket messages (max once per 30s) on click/keyboard/scroll.
- Terminal input also triggers heartbeats.
- A background task polls every 30s: if the last heartbeat is within the 5-minute idle timeout, an activity interval is open in the DB; otherwise it's closed.
- Crash recovery: stale open intervals are conservatively closed on startup.
- This produces accurate active-time data without per-event storage cost.

### Tauri for Desktop Packaging

The app ships as a native macOS `.app` bundle via Tauri:
- **Self-contained:** Python (via PyInstaller sidecar), all dependencies, tmux binary, and the built frontend are bundled inside the app.
- **Window management:** The app hides (not quits) on window close, quits on Cmd+Q. This keeps the orchestrator server running while the window is hidden.
- **Auto-update:** Tauri's built-in updater checks GitHub Releases for new versions.

---

## 7. Data Flow Diagrams

### Terminal Streaming Pipeline

```
Claude Code (in tmux pane)
    │
    │ PTY output (raw bytes)
    ▼
tmux pipe-pane -O → FIFO (/tmp/orchestrator-pty-{session}.fifo)
    │
    │ PtyStreamReader (async file read)
    ▼
PtyStreamPool (fan-out)
    │
    ├─→ WebSocket client 1 (binary frame)
    ├─→ WebSocket client 2 (binary frame)
    └─→ WebSocket client N (binary frame)
         │
         ▼
    xterm.js (browser) → rendered terminal
```

Drift correction (periodic):
```
tmux capture-pane -p -e → full pane content → hash compare
    │ (only if hash differs from last sync)
    ▼
WebSocket sync frame → xterm.js reset + write
```

### Reconnect Pipeline (Remote Worker)

```
Health check detects: PTY dead or SSH unreachable
    │
    ▼
1. Check SSH connectivity (tcp ping + ssh banner)
    │ ✗ → wait, retry with backoff
    ▼ ✓                                         reconnect_step=
2. Check/restart reverse tunnel (-R 8093)        "tunnel"
    │ ✗ → restart tunnel, verify
    ▼ ✓
3. Check/restart forward tunnel (-L 9741)        "daemon"
    │ ✗ → restart tunnel, verify
    ▼ ✓
4. Check RWS daemon health (TCP ping to 9741)    "daemon"
    │ ✗ → deploy + start daemon
    ▼ ✓
5. Check PTY session alive (pty_id still valid)  "pty_check"
    │ ✗ → deploy configs ("deploy"), create new PTY ("pty_create")
    ▼ ✓
6. Verify Claude process (alternate_on)          "verify"
    │ ✗ → launch Claude with --resume
    ▼ ✓
7. Update DB status → "working" or "idle"        step cleared
```

On failure: status → "disconnected", reconnect_step preserved (shows where it failed).
On max attempts exceeded: auto-reconnect paused, user notified.

### Worker Lifecycle State Machine

```
        create
          │
          ▼
       ┌──────┐   start    ┌─────────┐   prompt detected  ┌─────────┐
       │ idle │ ──────────→ │ working │ ──────────────────→ │ waiting │
       └──┬───┘             └────┬────┘                     └────┬────┘
          │                      │                                │
          │    ◄─────────────────┤  (auto-approve or user input)  │
          │    task complete      │                                │
          │                      │ ◄──────────────────────────────┘
          │                      │
          │    user action   ┌───▼────┐
          │ ◄──────────────  │ paused │
          │                  └────────┘
          │
          │  error/crash     ┌───────┐
          ├────────────────→ │ error │
          │                  └───────┘
          │
          │  connection lost   ┌──────────────┐   auto-reconnect  ┌────────────┐
          └──────────────────→ │ disconnected │ ────────────────→ │ connecting │
                               └──────────────┘                   └─────┬──────┘
                                      ▲                                  │
                                      │  failed (or max attempts)        │ success
                                      └──────────────────────────────────┘
                                                                         │
                                                                         ▼
                                                                  idle or working
```

### Human Activity Tracking

```
User interaction (click/key/scroll)
    │
    │ throttled (max 1/30s)
    ▼
WebSocket { type: "user_activity" }
    │                                     Terminal input (ws_terminal.py)
    │                                         │
    ▼                                         ▼
HumanActivityTracker.record_heartbeat()  ← also called from terminal input
    │
    │ updates in-memory _last_heartbeat timestamp
    │
    ▼ (every 30s poll)
Is now - _last_heartbeat < 5 min?
    │
    ├─ YES, no open interval → INSERT human_activity_events (start_time=now)
    ├─ NO, open interval exists → UPDATE end_time = last_heartbeat
    └─ Otherwise → no-op
```

---

## 8. Code Map

Quick reference: "I want to understand/modify X" → where to look.

| What | File(s) | Notes |
|------|---------|-------|
| **API server setup** | `api/app.py` | Lifespan, middleware, route mounting |
| **REST endpoints** | `api/routes/*.py` | One file per resource (20 route modules) |
| **Terminal streaming** | `terminal/pty_stream.py`, `api/ws_terminal.py` | PtyStreamReader/Pool + WebSocket handler |
| **Terminal input** | `api/ws_terminal.py`, `terminal/control.py` | WebSocket text frames → tmux send-keys |
| **Output parsing** | `terminal/output_parser.py` | Regex detection of Claude states |
| **Session creation** | `terminal/session.py` | Local + remote worker setup |
| **Worker health checks** | `session/health.py` | Process, SSH, PTY, manifest checks, circuit breaker |
| **Reconnect logic** | `session/reconnect.py` | Sequential pipeline, TUI guard, step tracking, attempt counting |
| **SSH tunnel management** | `session/tunnel.py` | ReverseTunnelManager |
| **Tunnel health loop** | `session/tunnel_monitor.py` | Periodic tunnel verification with failure counting |
| **Remote daemon (RWS)** | `terminal/remote_worker_server.py` | Daemon script + client + pool + forward tunnels |
| **Agent file deployment** | `agents/deploy.py` | SOT functions, manifests |
| **Browser view (CDP)** | `browser/cdp_proxy.py`, `api/ws_browser_view.py` | Screencast + input relay |
| **Interactive CLI** | `terminal/interactive.py`, `api/routes/interactive_cli.py` | PiP terminal sessions |
| **File explorer backend** | `api/routes/files.py` | Local + RWS file ops |
| **PR triage data** | `api/routes/prs.py` | GitHub GraphQL, attention levels, caching |
| **PR detail** | `api/routes/pr_preview.py` | Single PR reviews, files, checks |
| **Rdev management** | `api/routes/rdevs.py` | Rdev lifecycle, background refresh |
| **Human activity tracking** | `core/human_tracker.py`, `state/repositories/human_activity.py` | Heartbeat → interval → hours aggregation |
| **Trends/analytics** | `api/routes/trends.py`, `state/repositories/status_events.py` | Throughput, heatmap, worker-hours, human-hours, detail views |
| **Database schema** | `state/migrations/versions/*.sql` | 36 numbered migrations |
| **Data models** | `state/models.py` | Dataclasses: Project, Session, Task, Config, ContextItem, Notification, Skill, InteractiveCLI |
| **Data access** | `state/repositories/*.py` | CRUD per entity (9 repository modules) |
| **DB connection** | `state/db.py` | WAL mode, ConnectionFactory, retry |
| **Monitor loop** | `terminal/monitor.py`, `core/orchestrator.py` | Periodic tmux state polling |
| **Event bus** | `core/events.py` | Pub/sub for internal events |
| **Startup/shutdown** | `core/lifecycle.py` | Reconciliation, tunnel recovery |
| **Config loading** | `main.py`, `config.yaml` | YAML config |
| **Frontend entry** | `frontend/src/main.tsx`, `App.tsx` | React root, router (13 routes) |
| **Global state** | `frontend/src/context/AppContext.tsx` | WebSocket, sessions, tasks, activity heartbeats |
| **Frontend types** | `frontend/src/api/types.ts` | All API response interfaces |
| **Design tokens** | `frontend/src/styles/global.css` | Colors, spacing, components |
| **Terminal component** | `frontend/src/components/terminal/` | xterm.js wrapper, reconnect overlay |
| **Trends components** | `frontend/src/components/dashboard/` | TrendsPanel, charts, detail modal |
| **PRs page** | `frontend/src/pages/PRsPage.tsx` | Attention-based PR triage |
| **Task views** | `frontend/src/components/tasks/` | Table, board, cards, subtasks, links, PR preview |
| **Page components** | `frontend/src/pages/*.tsx` | One per route (12 pages) |
| **Sidebar** | `frontend/src/components/sidebar/` | Navigation, badges (unread, PR attention) |
| **Tauri config** | `src-tauri/tauri.conf.json`, `src-tauri/src/main.rs` | Window behavior, sidecar |
| **Build scripts** | `scripts/build_app.sh`, `scripts/build_sidecar.py` | App packaging |
| **Version management** | `pyproject.toml`, `scripts/bump-version.sh` | Single source of truth |
| **Design documents** | `docs/design_logs/` | Detailed design logs per feature |
