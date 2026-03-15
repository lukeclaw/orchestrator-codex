# Features

A comprehensive guide to what the Claude Orchestrator does, how users interact with it, and the full feature set from major to minor.

---

## 1. Design Philosophy

The Claude Orchestrator exists to solve a specific problem: managing multiple Claude Code sessions in parallel is painful. Without it, an engineer must manually switch between terminal tabs, copy-paste context, remember which worker is doing what, and babysit each session for permission prompts. The orchestrator turns this into a single-dashboard experience where the user operates as a **strategic decision-maker**, not a terminal babysitter.

**Core design values:**

- **Zero hard-coded context.** The orchestrator never assumes what you're building. All project context, instructions, and conventions are user-defined and stored in the database. The system is a general-purpose AI worker manager, not tied to any specific domain.
- **Local-first.** Everything runs on your machine. SQLite database, tmux sessions, local file system. No cloud services, no accounts, no telemetry. The only network traffic is SSH to remote dev machines when you choose to use remote workers.
- **Information density for power users.** The UI is designed for engineers who manage 5-15 parallel workers. Every pixel communicates state. Muted dark theme keeps visual noise low while status colors (green/yellow/red/orange/purple/blue) carry consistent meaning across every page.
- **Real-time, data-driven UI.** The dashboard reflects reality within seconds. WebSocket connections push state changes immediately. No polling, no stale views, no "click refresh to see updates."
- **Progressive disclosure.** The dashboard is useful at a glance (worker grid, status badges) but reveals depth on demand (terminal streaming, file explorer, task artifacts, trend charts).

---

## 2. How the User Interacts

### The Dashboard as a Single Pane of Glass

The primary interaction model is a web dashboard (served at `localhost:5173` in development, or inside a native macOS Tauri window). The user opens the dashboard and sees everything: active workers, task progress, project status, and notifications — all on one screen or one click away.

### Three-Column Layout

The UI follows a three-column structure:

1. **Sidebar (left)** — Navigation between pages: Dashboard, Projects, Tasks, PRs, Workers, Context, Skills, Notifications, Settings. Shows the Brain panel toggle, unread notification count, and PR attention badge.
2. **Main content (center)** — The active page: worker grid, task list, project details, terminal view, etc.
3. **Brain panel (right, resizable)** — A dedicated Claude Code session for orchestration-level decisions. The user can chat with the Brain to plan work, triage tasks, or coordinate workers. Collapsible and resizable.

### Keyboard-Driven

Power users interact heavily through keyboard shortcuts. Key bindings include navigation between pages, toggling the brain panel, opening file explorer (`Ctrl+Shift+E`), and terminal interaction. The design avoids modal dialogs that block keyboard flow — confirmations use inline popovers instead of `window.confirm()`.

### Real-Time Updates

A persistent WebSocket connection (`/ws/state`) pushes all state changes to the frontend: worker status transitions, task updates, new notifications. The terminal streaming uses a separate binary WebSocket for raw PTY output. Connection drops trigger automatic reconnect with exponential backoff.

### Getting Started

A Getting Started modal guides new users through initial setup — creating their first project, understanding the dashboard layout, and configuring workers.

---

## 3. Major Features

### 3.1 Dashboard

The landing page provides an at-a-glance overview of the entire system.

- **Stats bar** — Counts of active workers, in-progress tasks, and total projects.
- **Recent activity** — A chronological feed of worker status changes, task completions, and notifications. Displayed in a collapsible panel.
- **Worker grid** — Visual cards for each worker showing name, status badge, assigned task, and host (local/rdev). Cards use colored left-accent bars to indicate status.
- **Active projects** — Quick links to projects with progress indicators. Displayed in a collapsible panel.
- **Trends section** — Historical charts showing task throughput, worker activity heatmap, worker utilization hours (with human-hours overlay), and PR merge throughput. Configurable time ranges: 7d, 30d, 90d. Powered by a `status_events` table and `human_activity_events` table. Each chart supports click-to-drill-down via a detail modal.

### 3.2 Project & Task Management

Projects and tasks are the organizational backbone.

**Projects:**
- Named containers for related work (e.g., "API Gateway Rewrite").
- Each project has a description, status (active/completed/archived), target date, and a task prefix (e.g., "AGR") for human-readable task keys.
- Project-scoped context items automatically inject into workers assigned to tasks in that project.
- Project detail page shows aggregate stats: task counts (by status), subtask counts, assigned workers, and context items.

**Tasks:**
- Belong to a project. Have a title, description, status (todo/in_progress/done/blocked/cancelled), priority (H/M/L), and optional parent task for subtask hierarchies.
- Human-readable keys like `AGR-7` (project prefix + sequential index within project). Subtasks get compound keys like `AGR-7-1`.
- **Subtasks** — Tasks can have child tasks (subtasks) via `parent_task_id`. Subtask stats are tracked per task (total, completed, in-progress counts). Subtask status changes cascade to parent task awareness.
- **Assignment** — Tasks can be assigned to a worker session. The worker's prompt includes the task description and any project context.
- **Notes** — Free-form markdown field for worker observations, decision logs, and inline Mermaid diagrams.
- **Links** — Typed URL attachments with free-form tags (PR, doc, reference, etc.) displayed as clickable chips.
- **Artifacts** — Named, typed rich content attached to tasks. Two tiers:
  - *Tier 1:* Inline Mermaid diagrams in notes (rendered by the Markdown component).
  - *Tier 2:* HTML artifacts rendered in sandboxed iframes — dashboards, interactive visualizations, anything a worker produces as a self-contained HTML document. Secure by design (no access to parent window or Tauri APIs).

**Task views:**
- **Table view** — Traditional list with sorting and filtering by status, priority, project.
- **Board view** — Kanban-style columns (todo, in_progress, blocked, done) with drag-and-drop-style task cards.

**Task detail page** shows full task info, assigned worker terminal (live), notes editor, subtask list, artifacts viewer, PR preview cards, notifications, and links.

### 3.3 Worker Management

Workers are Claude Code sessions — each one a Claude instance running in a terminal.

**Worker types:**
- **Local workers** — Run in tmux windows on the local machine.
- **Remote workers (rdev)** — Run on remote development VMs, accessed via SSH. The orchestrator manages SSH tunnels, remote daemon deployment, and PTY sessions transparently.

**Worker lifecycle:**
- **idle** — Claude is at the prompt, waiting for input.
- **working** — Claude is actively processing (TUI detected via screen content analysis).
- **waiting** — Claude is asking a permission question (auto-approval may handle this).
- **paused** — User has manually paused the worker.
- **error** — Something went wrong (process crash, SSH failure).
- **disconnected** — Lost connection to the worker (SSH drop, tmux window gone).
- **connecting** — Reconnection in progress. The UI shows a step-by-step progress overlay (tunnel, daemon, PTY check, deploy, PTY create, verify).

**Auto-naming:** Workers get memorable names like `ember-cli-checkout_bizarre-orange` — a compound of recognizable words, not UUIDs.

**Auto-reconnect:** When a worker disconnects, the system automatically attempts reconnection with per-session attempt counting and exponential backoff. After max attempts, auto-reconnect pauses and notifies the user. The reconnect pipeline is sequential and non-intrusive: it verifies SSH, tunnel, daemon, and PTY health one layer at a time, never sending keystrokes to an active TUI.

**Reconnect progress:** The backend tracks the current reconnect step (`reconnect_step` field on the session). The frontend renders a centered overlay showing completed steps (checkmarks), the current step (spinner), and pending steps (circles), along with elapsed time. On failure, the overlay shows where the reconnect failed with a Retry button. This replaces the old error-text-spam pattern where repeated "PTY not attached" messages accumulated in the terminal.

**Health monitoring:** A background loop periodically checks each worker's health — process alive, SSH reachable, PTY responsive, /tmp files intact. Unhealthy workers are flagged and auto-reconnected if enabled. A per-host circuit breaker prevents health check storms against unreachable hosts.

### 3.4 PRs Page

A top-level PR triage dashboard organized by user attention, not GitHub state.

**Attention model:** Every open PR has an attention level computed from its review and CI state:

| Level | Name | Accent Color | Condition |
|-------|------|-------------|-----------|
| 1 | Needs action | Red | CI failing/error OR changes requested |
| 2 | Ready to ship | Green | Approved AND CI passing AND not draft |
| 3 | In review | Blue | Pending reviewers or CI running |
| 4 | Draft | Gray | isDraft = true |

**Page layout:**
- **Active tab** (default) — All open PRs authored by `@me`, sorted by attention level (red first). Filter pills: Needs action, Ready to ship, In review, Draft — each with a count and colored dot.
- **Recent tab** — Merged/closed PRs within a configurable window (7/14/30 days). Filter pills: All, Merged, Closed.
- **Table columns:** PR (accent bar + repo + title + diff size), Status (review + CI chips), Task/Worker (linked task key + worker name), Updated (relative time).
- **Expanded row detail** — Click a row to reveal review comment threads, changed files list, and action buttons (mark ready, auto-merge toggle, open in GitHub).

**Sidebar badge:** Count of PRs at attention level 1 (needs action) shown as a warning badge on the PRs nav item.

**Data source:** Single GitHub GraphQL query fetches PR list with `reviewDecision`, `reviewRequests`, `statusCheckRollup`, `additions/deletions`, and `autoMergeRequest`. Attention level computed server-side. Task/worker cross-references resolved by scanning task links for matching PR URLs.

**Caching:** Backend in-memory cache (10min TTL), frontend ref cache (10min TTL), bypass on manual refresh.

### 3.5 Terminal Streaming

Each worker's terminal is viewable live in the dashboard as a fully interactive xterm.js instance.

**How it works:**
- Output flows through tmux's `pipe-pane -O` command, which taps the raw PTY byte stream into a FIFO (named pipe). A `PtyStreamReader` reads the FIFO and fans out to all connected WebSocket clients via binary frames. This preserves the full TUI rendering — no escape sequence corruption, no line fragmentation.
- Input travels the reverse path: xterm.js `onData` → WebSocket text frame → FastAPI handler → `tmux send-keys` (hex-encoded to avoid shell interpretation).

**Drift correction:** Even with pipe-pane streaming, the terminal display can drift from reality (missed bytes, reconnects). A periodic sync mechanism captures the full pane content via `tmux capture-pane` and sends a complete refresh to the client. Hash comparison avoids redundant syncs.

**Typing latency optimization:** The streaming pipeline is optimized for sub-50ms keystroke echo. Key optimizations include stream flusher batching tuned for interactive latency, separation of input and output WebSocket handling, and async I/O throughout.

**Multi-client fan-out:** Multiple browser tabs can view the same terminal simultaneously. The `PtyStreamPool` manages one reader per session with fan-out to N subscribers.

### 3.6 Context System

Context items are structured pieces of information injected into agent prompts.

- **Scopes:** Global (applies to all agents), brain-only, or project-scoped (applies to workers on tasks in that project).
- **Categories:** instruction, requirement, convention, reference, note — each with a clear semantic purpose.
- **Re-brief on recovery:** When a worker reconnects or resumes, relevant context items are re-injected so Claude doesn't lose track of standing instructions.
- **Management UI:** A dedicated Context page with create/edit/delete, category filtering, and scope selection.

### 3.7 Brain Panel

A dedicated Claude Code session for high-level orchestration.

- **Purpose:** The Brain is where the user plans work, triages incoming requests, coordinates multi-worker strategies, and makes architectural decisions. It's the "manager" Claude that understands the big picture.
- **UI:** A resizable right-side panel that overlays the main content. Toggle via sidebar button or keyboard shortcut. The brain terminal is a full xterm.js instance with the same streaming infrastructure as worker terminals.
- **Separate agent config:** The brain has its own prompt (`agents/brain/prompt.md`), hooks, and skills — distinct from worker agents. It receives different context (global + brain-scoped items) and has access to orchestrator CLI commands for task/worker management.

### 3.8 Auto-Approval

Configurable rules that automatically approve trivial Claude permission prompts.

- **Problem solved:** Claude Code frequently asks "Do you want to continue?" or "Allow read access to X?" during normal operation. Manually approving these for 10+ workers is tedious and blocks progress.
- **How it works:** The output parser watches terminal content for known permission patterns. When a match is found, the system sends the appropriate keystroke (`y`, Enter, etc.) automatically.
- **Configurable:** Users can enable/disable auto-approval globally or per-worker, and configure which prompt patterns are auto-approved via the Settings page.
- **Safety:** Destructive operations (file deletion, system commands) are never auto-approved. The system errs on the side of caution — unknown prompts are left for the user.

### 3.9 Notification System

Non-blocking alerts from workers and the system.

- **Sources:** Workers can send notifications via the `orch-notify` CLI command (e.g., "PR ready for review", "Need manual intervention"). The system also generates notifications for errors, disconnections, and task completions.
- **Types:** info, warning, pr_comment — each with distinct visual styling. PR comment notifications carry structured metadata (repo, PR number, reviewer, comment body).
- **UI:** Dedicated Notifications page with dismiss/filter. Unread count badge in the sidebar. Notifications include optional links (e.g., to a PR URL) and metadata. Task-related notifications are also shown on the task detail page.
- **macOS native notifications:** When running as a Tauri app, critical notifications also trigger native macOS alerts.

---

## 4. Secondary Features

### 4.1 File Explorer

A VS Code-style file browser embedded in the session detail page.

- **Three-pane layout:** File tree (left), file content viewer (top-right), terminal (bottom-right). The terminal animates from full-size into the bottom-right pane when the explorer opens.
- **Lazy loading:** Directories expand on click, fetching contents on demand — no recursive full-tree scan.
- **File viewer:** Syntax-highlighted code display for source files. Rendered markdown preview for `.md` files.
- **Activity awareness:** Files recently modified by the worker are highlighted with VS Code-style git status colors (green for new, yellow for modified).
- **Remote-transparent:** Works identically for local and remote (rdev) workers. Remote file operations go through the RWS daemon over the SSH tunnel.
- **Non-intrusive:** Collapsed by default. A floating action button in the terminal area opens it. Keyboard shortcut: `Ctrl+Shift+E`.

### 4.2 Remote Browser View

View and interact with a Chromium browser running on a remote dev machine, directly from the dashboard.

- **Problem solved:** Workers on rdev frequently use Playwright for browser automation. When the browser hits a login page (OAuth, SSO, MFA), the worker gets stuck — it can't interact with the page, and the operator can't see the remote browser. Port-forwarding doesn't work because auth flows depend on real domains, cookies, and CORS policies.
- **How it works:** Chrome DevTools Protocol (CDP) screencast streams the remote browser's rendered frames as JPEG images to the dashboard. Mouse clicks and keyboard events are relayed back via CDP `Input.dispatch*` commands. The browser stays on rdev with its original URL intact — only pixels and input events travel over the wire.
- **UI:** Picture-in-picture overlay on the session detail page. Resizable and draggable.
- **`orch-browser` CLI:** Workers use this command to launch a browser with `--remote-debugging-port=9222`, auto-detect Playwright installation, and trigger the browser view overlay. The same browser instance is shared with the Playwright MCP server, so the operator sees exactly what Playwright is doing in real time.

### 4.3 Interactive CLI

A picture-in-picture terminal overlay for interactive user input.

- **Problem solved:** Workers sometimes need user interaction — password prompts (sudo, SSH), MFA codes, git credentials, interactive installers. Without this, the user must quit Claude, open a separate terminal, run the command, and return.
- **How it works:** Each worker can spawn one ephemeral interactive CLI session. It appears as a PiP overlay on the session detail page. Both the user and Claude can type into it. Claude has full visibility of the output.
- **Local workers:** Uses an additional tmux window.
- **Remote workers:** Uses an RWS daemon PTY session, which survives SSH reconnects.

### 4.4 Rdev Management

Manage remote development VM instances directly from the dashboard.

- **UI:** Accessible via the Workers page with a dedicated "Rdevs" tab. Shows a table of rdev instances with name, state, cluster, creation date, last access, and linked worker info.
- **Actions:** Create new rdev instances, restart, stop, and delete them. Create modal supports cluster selection.
- **Background refresh:** Rdev list refreshes in the background every 30 minutes with a 1-hour cache TTL.
- **Worker linking:** Rdev instances show which worker (if any) is currently running on them and the worker's status.

### 4.5 Skills Management

View and manage the slash-command skills available to brain and worker agents.

- **Built-in skills:** Stored as markdown files in `agents/brain/skills/` and `agents/worker/skills/`. Read-only in the UI — these are source-controlled.
- **Custom skills:** Stored in the SQLite database. Full CRUD via the Skills page. Users can create new slash commands with a name, description, target (brain/worker), and markdown content.
- **Auto-deploy:** Skills are deployed to `.claude/commands/` in the agent's working directory at session start. Custom skill names and descriptions are also injected into the agent prompt so the agent knows when to use them.
- **Enable/disable:** Custom skills can be toggled on/off without deletion.
- **Skill overrides:** Configuration for overriding built-in skill behavior.

### 4.6 Trends & Analytics

Historical visualizations on the dashboard, displayed in a collapsible Trends panel.

- **Task throughput** — Bar chart showing tasks completed per day/week. Click a bar to see which tasks were completed, including subtask→parent hierarchy.
- **Worker activity heatmap** — Grid showing which workers were active at which hours (day-of-week × hour), colored by intensity. Click a cell to see which workers were active during that time slot.
- **Worker utilization hours** — Area chart showing total active hours per worker over time. Overlaid with a second human-hours series on an independent right Y-axis. Header shows average hours/day for both. Click a day to see per-worker timeline bars with per-interval task context (which task each worker was on during each working interval).
- **Human-hours tracking** — Tracks the operator's active time on the app via interaction heartbeats (clicks, keyboard, scroll, terminal input). A 5-minute idle timeout closes intervals. Displayed as a blue/purple overlay on the worker-hours chart. Detail modal shows "You" timeline bars for the selected day.
- **PR merge throughput** — Derived from cached PR search results, showing merge velocity over time.
- **Time range selector:** 7-day, 30-day, 90-day views.
- **Data source:** A `status_events` table records every session/task status transition with timestamps, session names, and task context. A `human_activity_events` table records operator activity intervals.

### 4.7 Backup & Restore

SQLite database snapshot management.

- **Backup:** Creates a timestamped copy of the database file. Accessible via API and the Settings page.
- **Restore:** Replaces the active database with a backup. The orchestrator swaps its live connection without restart — background tasks (monitor loop, tunnel health) are stopped, the connection is replaced, and tasks restart with the new database.
- **Automatic backups:** Configurable auto-backup interval.

### 4.8 Smart Paste

Intelligent clipboard handling in the terminal.

- **Image detection:** When pasting image data into a terminal (which can't display images), the system intercepts and offers alternative handling.
- **Long text detection:** Pasting large text blocks into a terminal can be destructive (interpreted as commands). The system detects long pastes and shows a confirmation dialog with preview.

### 4.9 PR Preview

Workers can generate and share pull request previews.

- **Route:** `POST /api/sessions/{id}/pr-preview` — generates a PR diff preview from the worker's current changes.
- **UI integration:** Viewable from the task detail page as a PR preview card showing reviews, CI status, file changes, and comment threads.

### 4.10 Settings

Global configuration management via a dedicated Settings page.

- **Categories:** General (worker naming, auto-approval), Notifications, Backup, and Advanced settings.
- **Stored in DB:** Settings use the `config` table with key-value pairs, categories, and descriptions.
- **Pill-style tabs:** Settings are organized into tabbed sections following the app's tab bar pattern.

---

## 5. Infrastructure Features

### 5.1 SSH Tunneling

The backbone of remote worker communication.

- **Reverse tunnels:** When a remote worker starts, the orchestrator creates an SSH reverse tunnel (`-R`) so the remote machine can reach the local orchestrator API. This is how worker hooks and CLI commands (`orch-notify`, `orch-status`, `orch-browser`) communicate back to the orchestrator.
- **Forward tunnels:** SSH local port forwarding (`-L`) provides access to the RWS daemon (port 9741) and CDP debug port (9222) on the remote host.
- **On-demand port forwarding:** Workers can request ad-hoc port forwards via `orch-tunnel <port>` for scenarios like viewing a remote dev server locally.
- **Tunnel health monitoring:** A background loop periodically checks tunnel health and restarts dead tunnels. The `ReverseTunnelManager` tracks PIDs and provides recovery on orchestrator restart. A consecutive failure counter (max 5) prevents infinite restart loops.

### 5.2 Remote Worker Server (RWS)

A daemonized TCP server running on each remote host.

- **Purpose:** Handles file operations and PTY terminal sessions for remote workers. Survives SSH disconnects — the orchestrator reconnects through a new tunnel.
- **Protocol:** TCP with JSON-lines. Command connections handle file ops, PTY management, and health pings. Dedicated PTY stream connections carry full-duplex raw terminal I/O.
- **PTY management:** Creates PTY sessions via `pty.openpty()` + `os.fork()`. Each PTY has a 64KB ringbuffer for history replay on reattach. On reconnect, the ringbuffer is replayed followed by a `Ctrl+L` screen redraw. PTY IDs are tracked in the session model (`rws_pty_id`).
- **Lifecycle:** Deployed via SSH (base64 bootstrap). Forks to background, detaches from SSH. Binds `127.0.0.1:9741`. Auto-shutdown after 60 minutes of inactivity.
- **Connection resilience:** Four-level auto-recovery: command socket reconnect → tunnel-level reconnect → socket reconnect in pool → daemon kill+restart.

### 5.3 Session Recovery

Robust reconnection for both local and remote workers.

- **Reconnect pipeline:** A sequential, non-intrusive process that fixes one layer at a time: SSH connectivity → tunnel health → RWS daemon → PTY session → Claude process. Each step uses probes (not keystrokes) to detect state without disrupting an active TUI. The current step is written to the `reconnect_step` field in the DB for frontend progress display.
- **TUI guard:** The system detects whether Claude's TUI is active by checking for the `alternate_on` terminal mode. It never sends keystrokes to a pane with an active TUI, preventing the catastrophic bug of typing shell commands into Claude's interface.
- **Per-session locking:** Concurrent reconnect attempts for the same session are serialized via `asyncio.Lock` to prevent race conditions.
- **Attempt counting and backoff:** Each session tracks consecutive reconnect failures. After exceeding the max attempt limit, auto-reconnect is paused and the user is notified. The counter resets on manual reconnect or after sustained uptime.
- **Manifest-based /tmp recovery:** Agent configs, hooks, skills, and scripts are deployed to `/tmp/orchestrator/`. A `.manifest.json` lists all deployed files. The health check verifies the manifest and regenerates missing files, recovering from OS-level /tmp cleanup or reboots.
- **Single Source of Truth (SOT):** Two canonical functions (`deploy_worker_tmp_contents()`, `deploy_brain_tmp_contents()`) define the complete contents of each agent's tmp directory. All code paths (initial launch, reconnect, health recovery) delegate to these functions.

### 5.4 Releasing

The orchestrator ships as a native macOS app via Tauri.

- **Packaging:** The `.app` bundle includes Python, all pip dependencies, tmux, and the built frontend. Fully self-contained — no system Python or Homebrew required.
- **Version management:** Single source of truth in `pyproject.toml`. A bump script (`scripts/bump-version.sh`) syncs to `tauri.conf.json`, `Cargo.toml`, and `package.json`.
- **CI/CD:** GitHub Actions builds and publishes releases on tag push. Manual build also supported via `scripts/build_app.sh`.
- **Auto-update:** Tauri's built-in updater checks for new versions. Update metadata served via `latest.json` on GitHub Releases.

---

## 6. Edge Cases & Reliability

The orchestrator operates in a complex environment (SSH connections, tmux sessions, remote daemons, concurrent Claude processes). Key edge cases that are explicitly handled:

- **Reconnect oscillation:** Workers that repeatedly cycle between working and disconnected are caught by attempt counting and exponential backoff. After max attempts, auto-reconnect pauses and notifies the user. A per-host circuit breaker prevents health check storms against unreachable hosts.
- **FIFO backpressure:** If the WebSocket consumer falls behind the PTY stream, the FIFO writer can block. The PtyStreamReader uses non-blocking reads with configurable buffer sizes to prevent deadlocks.
- **Tunnel `ClearAllForwardings` bug:** SSH's `ClearAllForwardings` option was found to kill existing port forwards when establishing new tunnels. The tunnel manager carefully manages SSH multiplexing to avoid this.
- **Zombie remote port bindings:** When a reverse tunnel SSH session dies ungracefully, the remote sshd process can hold port 8093 indefinitely. The tunnel startup detects "remote port forwarding failed" errors and can kill zombie sshd processes on the remote host.
- **Stuck rdev SSH:** SSH connections to remote hosts can hang indefinitely. All SSH operations use `ConnectTimeout`, `ServerAliveInterval`, and `BatchMode=yes` to detect and recover from stale connections.
- **Multiple WebSocket clients:** Several browser tabs can view the same terminal. The PtyStreamPool manages fan-out, and each client gets an initial sync (capture-pane snapshot) on connect.
- **Database lock contention:** SQLite WAL mode enables concurrent reads. Write operations use a `ConnectionFactory` with retry logic to handle brief lock conflicts gracefully.
- **/tmp directory wipes:** macOS can clean `/tmp` at any time. The manifest-based health check detects missing files and regenerates them without user intervention.
- **Claude `/clear` command:** When a user runs `/clear` in Claude Code, it resets the conversation ID. The orchestrator detects this and updates the stored `claude_session_id` so future `--resume` commands target the correct conversation.
- **Crash recovery for human-hours:** If the app crashes or force-quits, orphaned activity intervals (`end_time IS NULL`) are conservatively closed on next startup (capped at idle timeout duration) to avoid inflated hour counts.
- **Forward tunnel orphans:** When RWS pool entries are replaced during reconnect, orphaned SSH forward tunnel processes can accumulate. Cleanup sweeps kill stale SSH processes for the affected host.
