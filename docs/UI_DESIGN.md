# Claude Orchestrator — UI Design Document

## Vision

A modern, dark-themed dashboard for managing multiple Claude Code sessions working on software projects. The UI should feel like a professional DevOps control center — think GitHub Projects meets Vercel Dashboard meets a terminal multiplexer. It is the single pane of glass for an engineer orchestrating parallel AI workers.

---

## Information Architecture

### Sidebar Navigation (persistent, collapsible)

```
[Logo] Claude Orchestrator
─────────────────────────────
📊  Dashboard                    ← Overview / home
📁  Projects                     ← Project list + detail
🖥️  Sessions                     ← Session list + terminals
📋  Tasks                        ← Task board (kanban or table)
⚡  Decisions                    ← Decision queue
💬  Chat                         ← LLM brain conversation
📜  Activity                     ← Full event log
⚙️  Settings                     ← Config, templates, keys
─────────────────────────────
[Connection status dot] Connected
```

The sidebar is 56px collapsed (icons only) or 220px expanded. It remembers state in localStorage. Each item shows a count badge when relevant (e.g., Decisions shows pending count, Tasks shows in-progress count).

---

## Page Designs

### 1. Dashboard (Home)

The landing page. At-a-glance overview of everything happening.

```
┌──────────────────────────────────────────────────────────────────────┐
│  STATS ROW                                                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │ 3        │ │ 2        │ │ 5        │ │ 2        │ │ 1        │ │
│  │ Sessions │ │ Projects │ │ Tasks    │ │ Decisions│ │ Open PRs │ │
│  │ (2 busy) │ │ (active) │ │ (3 todo) │ │ (pending)│ │          │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ │
│                                                                      │
│  ┌─ Active Projects ─────────────────────────────────────────────┐  │
│  │  project-alpha                    3 tasks · 2 workers · 75%   │  │
│  │  ████████████████████████░░░░░░░░                             │  │
│  │  project-beta                     2 tasks · 1 worker  · 20%   │  │
│  │  █████░░░░░░░░░░░░░░░░░░░░░░░░░░                             │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌─ Sessions ──────────────┐  ┌─ Pending Decisions ──────────────┐  │
│  │  ● worker-alpha WORKING │  │  ⚠ HIGH: Refactor auth module?   │  │
│  │    /src/project-a       │  │    [Yes, refactor] [No, proceed] │  │
│  │    Task: Implement OAuth│  │                                  │  │
│  │                         │  │  🔴 CRITICAL: PR #42 conflicts   │  │
│  │  ● worker-beta  IDLE    │  │    [Approve] [Dismiss]           │  │
│  │    /src/project-b       │  │                                  │  │
│  │                         │  │                                  │  │
│  │  ○ worker-gamma DISCONN │  │                                  │  │
│  └─────────────────────────┘  └──────────────────────────────────┘  │
│                                                                      │
│  ┌─ Recent Activity ────────────────────────────────────────────┐   │
│  │  10:32  session.connected    worker-alpha → localhost         │   │
│  │  10:31  pr.created           #42 Add user auth               │   │
│  │  10:30  task.started         Implement OAuth flow             │   │
│  └──────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
```

**Key behaviors:**
- Stats cards are clickable — navigate to the relevant page
- Project bars show completion % (tasks done / total tasks)
- Sessions are clickable — navigate to session detail with terminal
- Decisions are actionable inline — respond without leaving the page
- Activity is a live-updating feed

---

### 2. Projects Page

Two-level: project list → project detail.

#### 2a. Project List

```
┌──────────────────────────────────────────────────────────────────────┐
│  Projects                                          [+ New Project]   │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │ project-alpha                          Status: Active            ││
│  │ OAuth integration for the main app     Target: Feb 15            ││
│  │ Workers: worker-alpha, worker-beta     Tasks: 2/5 done           ││
│  │ ████████████████░░░░░░░░░░░░░░░░░░░░  40%                       ││
│  └─────────────────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │ project-beta                           Status: Active            ││
│  │ Improve test coverage                  Target: Feb 20            ││
│  │ Workers: worker-gamma                  Tasks: 0/3 done           ││
│  │ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  0%                        ││
│  └─────────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────┘
```

#### 2b. Project Detail

Clicking a project opens its detail view. This is the main workspace for managing a project.

```
┌──────────────────────────────────────────────────────────────────────┐
│  ← Projects / project-alpha                      [Edit] [Archive]    │
│  OAuth integration for the main app                                  │
│                                                                      │
│  ┌─ Info ───────┐  ┌─ Workers ──────────────────────────────────┐   │
│  │ Status: Active│  │ worker-alpha  WORKING  /src/project-a     │   │
│  │ Target: Feb 15│  │ worker-beta   IDLE     /src/project-b     │   │
│  │ Created: 3d   │  │                          [+ Assign Worker] │   │
│  └──────────────┘  └───────────────────────────────────────────┘   │
│                                                                      │
│  ┌─ Tasks ──────────────────────────────────────── [+ New Task] ──┐ │
│  │                                                                 │ │
│  │  TODO            IN PROGRESS         DONE          BLOCKED      │ │
│  │  ┌───────────┐  ┌───────────────┐   ┌──────────┐  ┌─────────┐ │ │
│  │  │ Write     │  │ Implement     │   │ Set up   │  │ Deploy  │ │ │
│  │  │ tests     │  │ OAuth flow    │   │ CI/CD    │  │ to stg  │ │ │
│  │  │ P:2       │  │ → worker-alpha│   │          │  │ ⚠ d1    │ │ │
│  │  │           │  │ P:1           │   │          │  │         │ │ │
│  │  └───────────┘  └───────────────┘   └──────────┘  └─────────┘ │ │
│  │  ┌───────────┐                                                  │ │
│  │  │ Add error │                                                  │ │
│  │  │ handling  │                                                  │ │
│  │  │ P:3       │                                                  │ │
│  │  └───────────┘                                                  │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌─ Pull Requests ──────────────────────────────────────────────┐   │
│  │  #42  Add user auth       OPEN       worker-alpha            │   │
│  │  #38  Setup CI pipeline   MERGED     worker-beta             │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  ┌─ Decisions ──────────────────────────────────────────────────┐   │
│  │  ⚠ Refactor auth module before OAuth?                        │   │
│  │    [Yes, refactor first] [No, add OAuth directly] [Dismiss]  │   │
│  └──────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
```

**Key behaviors:**
- Task board is a kanban view with 4 columns: TODO, IN PROGRESS, DONE, BLOCKED
- Tasks show priority (P:N), assigned worker, and blocking decision
- Tasks can be dragged between columns (updates status via API)
- Clicking a task opens a detail panel (slide-over) with description, dependencies, requirements
- Workers section shows assigned sessions with live status
- "Assign Worker" opens a picker of available sessions
- Decisions scoped to this project are shown inline

---

### 3. Sessions Page

List of all sessions + click into terminal detail.

#### 3a. Session List

```
┌──────────────────────────────────────────────────────────────────────┐
│  Sessions                                        [+ Add Session]     │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  ● worker-alpha          WORKING        localhost               ││
│  │    /src/project-a        Task: Implement OAuth flow             ││
│  │    Project: project-alpha                   Last active: 2m ago ││
│  └─────────────────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  ● worker-beta           IDLE           localhost               ││
│  │    /src/project-b        No task assigned                       ││
│  │    Project: project-alpha                   Last active: 5m ago ││
│  └─────────────────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  ○ worker-gamma          DISCONNECTED   rdev1.example.com      ││
│  │    —                     No task assigned                       ││
│  │    Project: project-beta                    Last active: 1h ago ││
│  └─────────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────┘
```

#### 3b. Session Detail (Terminal View)

Full-screen terminal with session info sidebar.

```
┌──────────────────────────────────────────────────────────────────────┐
│  ← Sessions / worker-alpha                                           │
│                                                                      │
│  ┌─ Sidebar (300px) ──┐  ┌─ Terminal ────────────────────────────┐  │
│  │                     │  │ ● Connected                           │  │
│  │  worker-alpha       │  │                                      │  │
│  │  ● WORKING          │  │  $ claude                            │  │
│  │                     │  │  > Implementing OAuth flow...        │  │
│  │  Host: localhost    │  │  Reading src/auth.py                 │  │
│  │  Path: /src/proj-a  │  │  Writing src/oauth.py               │  │
│  │  Created: 3d ago    │  │  ...                                 │  │
│  │  Active: 2m ago     │  │                                      │  │
│  │                     │  │                                      │  │
│  │  ─── Task ───       │  │                                      │  │
│  │  Implement OAuth    │  │                                      │  │
│  │  Status: in_progress│  │                                      │  │
│  │  Priority: 1        │  │                                      │  │
│  │                     │  │                                      │  │
│  │  ─── Project ───    │  │                                      │  │
│  │  project-alpha      │  │                                      │  │
│  │                     │  │                                      │  │
│  │  ─── PRs ───        │  │                                      │  │
│  │  #42 Add user auth  │  │                                      │  │
│  │                     │  │                                      │  │
│  │  ─── Activity ───   │  │                                      │  │
│  │  10:32 connected    │  │                                      │  │
│  │  10:31 pr.created   │  │                                      │  │
│  │  10:30 task.started │  │                                      │  │
│  │                     │  │                                      │  │
│  │  [Send Message]     │  │                                      │  │
│  │  [Remove Session]   │  ├──────────────────────────────────────┤  │
│  │                     │  │ Send message to worker-alpha...  [⏎] │  │
│  └─────────────────────┘  └──────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

**Key behaviors:**
- Terminal (xterm.js) fills the main area, fully interactive (read/write)
- Sidebar shows session context: info, current task, project, PRs, recent activity
- "Send Message" types directly into the terminal via tmux send-keys
- Message input bar at bottom of terminal for quick messages
- Sidebar is collapsible for full-width terminal

---

### 4. Tasks Page

Global task view — filterable table or board view across all projects.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Tasks                    [Board View | Table View]   [+ New Task]   │
│  Filter: [All Projects ▼] [All Statuses ▼] [All Workers ▼]          │
│                                                                      │
│  TABLE VIEW:                                                         │
│  ┌──────┬────────────────────┬──────────┬─────────┬─────────┬─────┐ │
│  │ Pri  │ Title              │ Project  │ Status  │ Worker  │ PR  │ │
│  ├──────┼────────────────────┼──────────┼─────────┼─────────┼─────┤ │
│  │ 1    │ Implement OAuth    │ alpha    │ 🔵 WIP │ w-alpha │ #42 │ │
│  │ 2    │ Write tests        │ alpha    │ ○ TODO │ —       │ —   │ │
│  │ 3    │ Add error handling │ alpha    │ ○ TODO │ —       │ —   │ │
│  │ 1    │ Deploy to staging  │ alpha    │ 🟡 BLOCKED│ —    │ —   │ │
│  │      │                    │          │   ⚠ d1  │         │     │ │
│  │ 1    │ Increase coverage  │ beta     │ ○ TODO │ —       │ —   │ │
│  └──────┴────────────────────┴──────────┴─────────┴─────────┴─────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

**Key behaviors:**
- Toggle between Board (kanban) and Table views
- Table is sortable by any column
- Clicking a task row opens a slide-over detail panel with:
  - Full description
  - Dependencies (what it depends on / what depends on it)
  - Requirements (capabilities needed)
  - Assigned worker
  - Blocking decision (with inline respond)
  - Associated PRs
- Bulk actions: assign to worker, change status, change priority

---

### 5. Decisions Page

Full decision queue with history.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Decisions                                [Pending | History]        │
│                                                                      │
│  PENDING (2)                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │ 🔴 CRITICAL                                           just now  ││
│  │ PR #42 has merge conflicts. How should we resolve?              ││
│  │ Session: worker-beta · Project: project-alpha                   ││
│  │ Context: Conflicts in src/auth.py and src/config.py             ││
│  │                                                                 ││
│  │ [Approve]  [Dismiss]                        [Type response...] ││
│  └─────────────────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │ ⚠ HIGH                                               2m ago    ││
│  │ Should we refactor the auth module before adding OAuth?         ││
│  │ Session: worker-alpha · Project: project-alpha                  ││
│  │                                                                 ││
│  │ [Yes, refactor first]  [No, add OAuth directly]  [Dismiss]     ││
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                      │
│  HISTORY                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │ ✓ NORMAL                                    responded · 1d ago  ││
│  │ Use JWT or session tokens for auth?                             ││
│  │ Response: "JWT — stateless, easier to scale"                    ││
│  └─────────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────┘
```

**Key behaviors:**
- Pending decisions sorted by urgency (critical first)
- Each decision shows full context: session, project, task, context text
- Options rendered as buttons, plus a free-text input for custom responses
- History tab shows resolved/dismissed decisions with the response given
- Decision responses can optionally include "was_helpful" feedback

---

### 6. Chat Page

Full-screen conversation with the LLM orchestrator brain.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Chat                                              [Clear History]   │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                                                              │   │
│  │  ORCHESTRATOR                                                │   │
│  │  ┌─────────────────────────────────────────────────┐        │   │
│  │  │ Welcome! I'm managing 3 sessions across 2       │        │   │
│  │  │ projects. worker-alpha is implementing OAuth,   │        │   │
│  │  │ worker-beta is idle. 2 decisions need attention. │        │   │
│  │  └─────────────────────────────────────────────────┘        │   │
│  │                                                              │   │
│  │                                         YOU                  │   │
│  │        ┌─────────────────────────────────────────┐          │   │
│  │        │ Assign worker-beta to the "write tests" │          │   │
│  │        │ task on project-alpha                    │          │   │
│  │        └─────────────────────────────────────────┘          │   │
│  │                                                              │   │
│  │  ORCHESTRATOR                                                │   │
│  │  ┌─────────────────────────────────────────────────┐        │   │
│  │  │ I'll assign worker-beta to "Write tests" on     │        │   │
│  │  │ project-alpha.                                   │        │   │
│  │  │                                                  │        │   │
│  │  │ ┌─ Proposed Action ─────────────────────────┐   │        │   │
│  │  │ │ assign_task                                │   │        │   │
│  │  │ │ Task: Write tests → worker-beta            │   │        │   │
│  │  │ │ [Approve] [Reject]                         │   │        │   │
│  │  │ └───────────────────────────────────────────┘   │        │   │
│  │  └─────────────────────────────────────────────────┘        │   │
│  │                                                              │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  ┌──────────────────────────────────────────────────── [Send] ──┐   │
│  │ Ask the orchestrator...                                      │   │
│  └──────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
```

**Key behaviors:**
- Chat shows LLM responses with proposed actions rendered as interactive cards
- Action cards have [Approve] / [Reject] buttons (respect approval_policy config)
- Approved actions execute immediately, result shown inline
- Chat context includes system state (the brain uses context_selector to assemble relevant info)
- Supports markdown rendering in responses
- "Clear History" resets conversation

---

### 7. Activity Page

Full event log with filters.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Activity                                                            │
│  Filter: [All Types ▼] [All Sessions ▼] [All Projects ▼] [Search]   │
│                                                                      │
│  TODAY                                                               │
│  10:32 AM  session.connected    worker-alpha   localhost             │
│  10:31 AM  pr.created           worker-beta    #42 Add user auth    │
│  10:30 AM  task.started         worker-alpha   Implement OAuth flow │
│  10:28 AM  decision.created     worker-alpha   Refactor auth?       │
│  10:25 AM  session.state_changed worker-alpha  idle → working       │
│                                                                      │
│  YESTERDAY                                                           │
│  ...                                                                 │
└──────────────────────────────────────────────────────────────────────┘
```

**Key behaviors:**
- Filterable by event type, session, project
- Live updates via WebSocket
- Grouped by day
- Clicking an activity row expands to show full event_data JSON
- Search across event data

---

### 8. Settings Page

Configuration management.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Settings                                                            │
│                                                                      │
│  [General] [Approval Policies] [Context Weights] [Templates]        │
│                                                                      │
│  GENERAL                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  Autonomy Mode        [Advisory ▼]                              ││
│  │  Monitor Interval     [5] seconds                               ││
│  │  Heartbeat Timeout    [120] seconds                             ││
│  │  Context Token Budget [8000] tokens                             ││
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                      │
│  APPROVAL POLICIES                                                   │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  Send Message         [✓] Require approval                      ││
│  │  Assign Task          [✓] Require approval                      ││
│  │  Create Task          [ ] Require approval                      ││
│  │  Rebrief Session      [✓] Require approval                      ││
│  │  Alert User           [ ] Require approval                      ││
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                      │
│  API KEY                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  Source: Claude Code Keychain    Status: ✓ Valid                 ││
│  │  [Test Connection]                                              ││
│  └─────────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────┘
```

---

## Component Architecture

```
src/
├── main.tsx
├── App.tsx                      ← Layout shell with sidebar + content area
├── api/
│   ├── client.ts                ← Typed fetch wrapper
│   └── types.ts                 ← All TypeScript interfaces
├── hooks/
│   ├── useWebSocket.ts          ← Auto-reconnecting WS, triggers refetch
│   ├── useSessions.ts           ← Sessions CRUD + polling
│   ├── useProjects.ts           ← Projects CRUD + polling
│   ├── useTasks.ts              ← Tasks CRUD + polling
│   ├── useDecisions.ts          ← Decisions CRUD + polling
│   ├── useActivities.ts         ← Activity log + polling
│   └── useChat.ts               ← Chat message state + send
├── context/
│   └── AppContext.tsx            ← Global state: connection, quick stats
├── layouts/
│   └── AppLayout.tsx            ← Sidebar + header + content slot
├── pages/
│   ├── DashboardPage.tsx        ← Overview with stats, projects, sessions
│   ├── ProjectsPage.tsx         ← Project list
│   ├── ProjectDetailPage.tsx    ← Single project: tasks board, workers, PRs
│   ├── SessionsPage.tsx         ← Session list
│   ├── SessionDetailPage.tsx    ← Terminal + sidebar info
│   ├── TasksPage.tsx            ← Global task table/board
│   ├── DecisionsPage.tsx        ← Decision queue + history
│   ├── ChatPage.tsx             ← Full LLM chat
│   ├── ActivityPage.tsx         ← Event log
│   └── SettingsPage.tsx         ← Config management
├── components/
│   ├── sidebar/
│   │   ├── Sidebar.tsx          ← Navigation sidebar
│   │   └── SidebarItem.tsx      ← Nav item with icon + badge
│   ├── projects/
│   │   ├── ProjectCard.tsx      ← Project summary card
│   │   ├── ProjectForm.tsx      ← Create/edit project modal
│   │   └── WorkerPicker.tsx     ← Assign worker to project
│   ├── tasks/
│   │   ├── TaskBoard.tsx        ← Kanban board (4 columns)
│   │   ├── TaskColumn.tsx       ← Single kanban column
│   │   ├── TaskCard.tsx         ← Task card in kanban/table
│   │   ├── TaskDetail.tsx       ← Slide-over task detail panel
│   │   ├── TaskForm.tsx         ← Create/edit task modal
│   │   └── TaskTable.tsx        ← Table view of tasks
│   ├── sessions/
│   │   ├── SessionCard.tsx      ← Session summary card
│   │   ├── SessionForm.tsx      ← Add session modal
│   │   └── SessionInfo.tsx      ← Session detail sidebar panel
│   ├── decisions/
│   │   ├── DecisionCard.tsx     ← Pending decision with action buttons
│   │   └── DecisionHistory.tsx  ← Resolved decision display
│   ├── activity/
│   │   └── ActivityFeed.tsx     ← Activity timeline
│   ├── chat/
│   │   ├── ChatPanel.tsx        ← Message list + input
│   │   ├── ChatMessage.tsx      ← Single message bubble
│   │   └── ActionCard.tsx       ← Proposed action with approve/reject
│   ├── terminal/
│   │   └── TerminalView.tsx     ← xterm.js terminal component
│   └── common/
│       ├── Modal.tsx
│       ├── SlideOver.tsx        ← Right-panel slide-over for details
│       ├── StatusBadge.tsx
│       ├── UrgencyTag.tsx
│       ├── ProgressBar.tsx
│       ├── EmptyState.tsx
│       ├── TimeAgo.tsx
│       └── FilterBar.tsx        ← Reusable filter dropdowns
└── styles/
    ├── variables.css            ← CSS custom properties (dark theme)
    └── global.css               ← Reset, typography, common classes
```

---

## Routing

```
/                           → DashboardPage
/projects                   → ProjectsPage
/projects/:id               → ProjectDetailPage
/sessions                   → SessionsPage
/sessions/:id               → SessionDetailPage (terminal)
/tasks                      → TasksPage
/decisions                  → DecisionsPage
/chat                       → ChatPage
/activity                   → ActivityPage
/settings                   → SettingsPage
```

---

## Design Principles

1. **Data-driven, not mocked.** Every piece of UI maps to a real API endpoint and database entity. No placeholder data.

2. **Context-rich.** Each entity shows its relationships — a session card shows its project, task, and recent activity. A task card shows its worker, blocking decision, and PR.

3. **Actionable inline.** Decisions can be responded to from anywhere they appear (dashboard, project detail, decisions page). Tasks can be reassigned without navigating away.

4. **Real-time.** WebSocket pushes trigger re-renders. Session status, decision counts, and activity feeds update live.

5. **Progressive disclosure.** List pages show summaries; clicking reveals full detail in slide-overs or dedicated pages. Terminal view is the deepest level of drill-down.

6. **Keyboard-friendly.** Global shortcuts: `G D` → Dashboard, `G P` → Projects, `G S` → Sessions, `G T` → Tasks, `G C` → Chat.

---

## API Endpoints Used Per Page

| Page | Endpoints |
|------|-----------|
| Dashboard | `GET /sessions`, `GET /projects`, `GET /tasks`, `GET /decisions/pending`, `GET /activities?limit=10`, `GET /prs` |
| Projects | `GET /projects`, `POST /projects`, `PATCH /projects/:id`, `DELETE /projects/:id` |
| Project Detail | `GET /projects/:id`, `GET /tasks?project_id=`, `GET /prs?session_id=`, `GET /decisions?project_id=`, `GET /activities?project_id=` |
| Sessions | `GET /sessions`, `POST /sessions`, `DELETE /sessions/:id` |
| Session Detail | `GET /sessions/:id`, `GET /tasks?assigned_session_id=`, `GET /prs?session_id=`, `GET /activities?session_id=`, `WS /ws/terminal/:id` |
| Tasks | `GET /tasks`, `POST /tasks`, `PATCH /tasks/:id`, `DELETE /tasks/:id` |
| Decisions | `GET /decisions`, `GET /decisions/pending`, `POST /decisions/:id/respond`, `POST /decisions/:id/dismiss` |
| Chat | `POST /chat` |
| Activity | `GET /activities` |
| Settings | `GET /config` (needs new endpoint), `PATCH /config` (needs new endpoint) |

---

## New API Endpoints Needed

1. **`GET /api/config`** — List all config entries (for settings page)
2. **`PATCH /api/config/:key`** — Update a config value
3. **`GET /api/projects/:id/stats`** — Project task completion stats (or compute client-side)

---

## Dark Theme Palette (existing, refined)

```css
--bg:             #0d1117    /* Page background */
--surface:        #161b22    /* Cards, panels */
--surface-hover:  #1c2129    /* Hover state */
--surface-raised: #21262d    /* Elevated surfaces */
--border:         #30363d    /* Borders */
--text-primary:   #e6edf3    /* Primary text */
--text-secondary: #8b949e    /* Secondary text */
--text-muted:     #484f58    /* Muted text */
--accent:         #58a6ff    /* Links, active items */
--green:          #3fb950    /* Success, idle, done */
--yellow:         #d29922    /* Warning, waiting */
--red:            #f85149    /* Error, critical */
--orange:         #db6d28    /* High urgency */
--purple:         #a371f7    /* Merged, special */
```
