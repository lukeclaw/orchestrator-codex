---
title: "Claude Orchestrator — UI Design Document"
author: Yudong Qiu
created: 2026-02-07
last_modified: 2026-02-21
status: Implemented
---

# Claude Orchestrator — UI Design Document

## Vision

A modern, dark-themed dashboard for managing multiple Claude Code sessions working on software projects. The UI should feel like a professional DevOps control center — think GitHub Projects meets Vercel Dashboard meets a terminal multiplexer. It is the single pane of glass for an engineer orchestrating parallel AI workers.

---

## Information Architecture

### Three-Column Layout

The app uses a persistent three-column layout:

```
┌─────────────┬──────────────────────────────────┬──────────────────────┐
│  Sidebar    │  Main Content                    │  Brain Panel         │
│  (220px /   │  (flex: 1)                       │  (resizable)         │
│   56px)     │                                  │                      │
│             │  ┌─ Header ───────────────────┐  │  ┌─ Header ───────┐ │
│  Dashboard  │  │ tmux attach...   ● Live    │  │  │ Brain [Working]│ │
│  Projects   │  ├────────────────────────────┤  │  ├────────────────┤ │
│  Tasks      │  │                            │  │  │                │ │
│  Workers 8  │  │  Page Content              │  │  │  Claude Code   │ │
│  Context    │  │  (scrollable)              │  │  │  Terminal      │ │
│  Notifs     │  │                            │  │  │  (xterm.js)    │ │
│             │  │                            │  │  │                │ │
│  ─────────  │  │                            │  │  │                │ │
│  Settings   │  │                            │  │  │         Paste  │ │
└─────────────┴──────────────────────────────────┴──────────────────────┘
```

- **Sidebar**: 220px expanded, 56px collapsed (icons only). State persisted in localStorage. Each item has a keyboard shortcut (D, P, T, W, K, N). Shows count badges when relevant (Workers shows waiting count, Notifications shows unread count).
- **Main content**: Header (40px) with tmux command (click-to-copy) and WebSocket connection status. Below is the scrollable page content.
- **Brain panel**: Resizable right panel with a live Claude Code terminal (the orchestrator brain). Has start/stop controls, paste button for sending commands. Collapsible.

### Sidebar Navigation

```
[Logo] Orchestrator
─────────────────────────────
  Dashboard           (D)     ← Overview / home
  Projects            (P)     ← Project list + detail
  Tasks               (T)     ← Task table with filters
  Workers        [8]  (W)     ← Worker list + terminals (badge = waiting count)
  Context             (K)     ← Context items management
  Notifications  [3]  (N)     ← Notification feed (badge = unread count)
─────────────────────────────
  Settings                    ← Config management
```

---

## Page Designs

### 1. Dashboard (Home)

The landing page. At-a-glance overview of everything happening. Layout order: Stats → Recent Activity → Trends → Active Projects → Workers.

```
┌──────────────────────────────────────────────────────────────────────┐
│  STATS ROW (compact, horizontal)                                     │
│  ┌───────────────────┐ ┌──────────────┐ ┌────────────────────────┐  │
│  │ 11  WORKERS       │ │ 11  PROJECTS │ │ 75  IN-PROGRESS TASKS  │  │
│  │     8 waiting ·   │ │              │ │     253 done · 96 todo │  │
│  │     2 offline     │ │              │ │                        │  │
│  └───────────────────┘ └──────────────┘ └────────────────────────┘  │
│                                                                      │
│  ┌─ Recent Activity ──────────────────────────────────────────────┐  │
│  │  ✓ PENP-7 completed — Improve dashboard performance    2h ago  │  │
│  │  ⏸ 8 workers waiting for input                        41m ago  │  │
│  │  ✓ PENP-7-2 completed — Increase Trino query timeout  14h ago  │  │
│  │  ✓ OC-1-22 completed — Remove dead setOmsPlan()       15h ago  │  │
│  │  ✕ quirky-eagle disconnected                           16h ago  │  │
│  │  → MQ-4 picked up by quirky-eagle                      1d ago  │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌─ Active Projects ──────────────────────────── [+ New Project] ─┐  │
│  │  NAME          TASKS  SUBTASKS  PROGRESS  WORKERS     UPDATED  │  │
│  │  ● sdui prem     1      0       ██░ 0/1  sdui_fli..   2d ago  │  │
│  │  ● Prem Hub      1      4       ███ 3/5  —             2d ago  │  │
│  │  ● Lix Clean     2     91       █░░ 1/93 subs-mt..    3d ago  │  │
│  │  ░░░░░░░░░░░░░░░ (fade gradient when more rows below) ░░░░░░  │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌─ Workers ──────────────────────────────────── [+ Add Worker] ──┐  │
│  │  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐   │  │
│  │  │ subs-backend_  │  │ prem-eng-port_ │  │ prem-eng-port_ │   │  │
│  │  │ happy-einstein │  │ robust-valley  │  │ fuzzy-kumquat  │   │  │
│  │  │ [Waiting]      │  │ [Waiting]      │  │ [Waiting]      │   │  │
│  │  │ > preview...   │  │ > preview...   │  │ > preview...   │   │  │
│  │  │ PENP-7  2h ago │  │ OC-2   37m ago │  │ PENP-5  2h ago │   │  │
│  │  └────────────────┘  └────────────────┘  └────────────────┘   │  │
│  │  ... (3-column auto-fill grid, 300px min)                      │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

**Stats bar:**
- 3 cards in a compact horizontal strip (number + label side by side, not stacked)
- Workers card turns yellow/warning when workers need attention (waiting or offline)
- Each card is a link to its respective page
- Subtitles show actionable breakdowns (e.g., "8 waiting · 2 offline", "253 done · 96 todo")

**Recent Activity:**
- Derived from existing task and worker data — no separate API call
- Task events (completed, picked up) from the last 7 days
- Worker status events (waiting, disconnected, working) from the last 24 hours
- Similar worker events are grouped (e.g., "8 workers waiting for input" instead of 8 separate lines)
- Capped at 8 items, sorted by most recent. Hidden when empty
- Each item is clickable (links to task or worker detail)

**Trends:**
- Three visualizations showing historical data from the `status_events` table
- Time range toggle (7d / 30d / 90d) using `.toggle-group.toggle-sm`
- Independent data fetch via `useTrends` hook (not connected to WebSocket cycle)
- **Throughput chart:** Stacked bar chart (Recharts) showing completed tasks (blue) and subtasks (purple) per day. Rolling 7-day average displayed in header.
- **Worker Activity heatmap:** Custom SVG grid (7 days x 24 hours) showing when workers start working. Opacity-scaled blue cells. Timestamps in UTC. Hover tooltip shows day/hour/count.
- **Worker-Hours chart:** Area chart (Recharts) showing total worker-hours per day. Green stroke with gradient fill. Displays today's hours and weekly total.
- Layout: 2-column CSS grid. Throughput spans full width (row 1). Heatmap and Worker-Hours side by side (row 2). Single column below 900px.
- Empty state: "No activity data yet." when no historical events exist.
- Hidden entirely when loading (shows "Loading trends..." text).

**Active Projects table:**
- Sortable columns: Name, Tasks, Subtasks, Progress, Workers, Updated
- Status and Created columns hidden on dashboard (redundant since all are "Active")
- Progress bar has color-coded segments: green (done), blue (active), red (blocked)
- Workers column shows colored tags matching worker status
- Max height 300px with scroll + fade gradient overlay when overflowing
- Section header "Active Projects" links to /projects

**Workers grid:**
- 3-column auto-fill grid (min 300px per card)
- Sorted by last_viewed_at (most recently viewed first)
- Each card shows: status dot, split name (dimmed prefix + bold suffix), status badge, terminal preview (last 8 lines), assigned task with key + footer timestamp
- Card border color matches worker status
- RDEV badge hidden when ALL workers are on rdevs (adds no info)
- Clicking a card navigates to /workers/:id

---

### 2. Projects Page

Two views: card view (default) and table view, with status filter.

#### Card View

```
┌──────────────────────────────────────────────────────────────────────┐
│  Projects          [Table | Cards]              [+ New Project]       │
│  Status: [All ▼]                                                     │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │ project-name                          [active]        [Edit ✎] ││
│  │ Project description text...                                     ││
│  │ Tasks 2/5  ██████████░░░░░░░░░░  Subtasks 15/21                ││
│  │ worker-alpha worker-beta                           3d ago       ││
│  └─────────────────────────────────────────────────────────────────┘│
│  ...                                                                 │
└──────────────────────────────────────────────────────────────────────┘
```

#### Table View

Same sortable table as the dashboard but with all columns (including Status and Created).

#### Project Detail

Clicking a project opens its detail view with task management.

```
┌──────────────────────────────────────────────────────────────────────┐
│  ← Projects / project-name                       [Edit] [Archive]    │
│  Description text...                                                 │
│                                                                      │
│  ┌─ Tasks ──────────────────────────────────────── [+ New Task] ──┐ │
│  │  KEY    TITLE              STATUS    WORKER        UPDATED      │ │
│  │  OC-1   Clean up APIs      in_prog   zen-dinosaur   2d ago     │ │
│  │  OC-2   Clean up backend   in_prog   happy-einst    1d ago     │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌─ Workers ──────────────────────────────────────────────────────┐  │
│  │  worker-cards for assigned workers                             │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

---

### 3. Tasks Page

Global task view — filterable and sortable table across all projects.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Tasks                                              [+ New Task]     │
│  Filter: [All Projects ▼] [All Statuses ▼]                          │
│                                                                      │
│  ┌──────┬─────────────────────┬──────────┬──────────┬────────┬────┐ │
│  │ KEY  │ Title               │ Project  │ Status   │ Worker │ PR │ │
│  ├──────┼─────────────────────┼──────────┼──────────┼────────┼────┤ │
│  │ OC-1 │ Clean up APIs       │ OMS      │ in_prog  │ zen-d  │ —  │ │
│  │ OC-2 │ Clean up backend    │ OMS      │ in_prog  │ happy  │ —  │ │
│  │ MQ-4 │ Filter training data│ Magic Q  │ in_prog  │ quirky │ —  │ │
│  └──────┴─────────────────────┴──────────┴──────────┴────────┴────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

Clicking a task row navigates to the Task Detail page showing full description, subtasks, assigned worker, and project context.

---

### 4. Workers Page

List of all workers with status overview. Includes an "Rdevs" tab for managing remote dev boxes.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Workers                   [Workers | Rdevs]     [+ Add Worker]      │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  ● subs-backend_happy-einstein   WAITING      rdev/host        ││
│  │    > terminal preview...                                        ││
│  │    Task: OC-2 Clean up backend              Last active: 37m   ││
│  └─────────────────────────────────────────────────────────────────┘│
│  ...                                                                 │
└──────────────────────────────────────────────────────────────────────┘
```

#### Worker Detail (Session Detail)

Full terminal view with session info sidebar.

```
┌──────────────────────────────────────────────────────────────────────┐
│  ← Workers / happy-einstein                                          │
│                                                                      │
│  ┌─ Sidebar (300px) ──┐  ┌─ Terminal ────────────────────────────┐  │
│  │                     │  │                                      │  │
│  │  happy-einstein     │  │  $ claude                            │  │
│  │  ● WAITING          │  │  > Working on task...                │  │
│  │                     │  │  Reading src/auth.py                 │  │
│  │  Host: rdev/host    │  │  ...                                 │  │
│  │  Created: 3d ago    │  │                                      │  │
│  │  Active: 2m ago     │  │                                      │  │
│  │                     │  │                                      │  │
│  │  ─── Task ───       │  │                                      │  │
│  │  OC-2 Clean up APIs │  │                                      │  │
│  │  Status: in_progress│  │                                      │  │
│  │                     │  │                                      │  │
│  │  ─── Project ───    │  │                                      │  │
│  │  OMS cleanup        │  │                                      │  │
│  │                     │  │                                      │  │
│  │  [Send Message]     │  ├──────────────────────────────────────┤  │
│  │  [Remove Worker]    │  │ Send message to worker...        [⏎] │  │
│  └─────────────────────┘  └──────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

---

### 5. Context Page

Manage context items (documents, guidelines, specs) that get injected into worker prompts.

---

### 6. Notifications Page

Notification feed showing system events, worker alerts, and brain messages.

---

### 7. Settings Page

Configuration management for brain settings, worker defaults, and system preferences.

---

## Component Architecture

```
src/
├── main.tsx
├── App.tsx                      ← Router with AppLayout shell
├── api/
│   ├── client.ts                ← Typed fetch wrapper
│   └── types.ts                 ← All TypeScript interfaces
├── hooks/
│   ├── useBackup.ts             ← Backup functionality
│   ├── useBrainPanelState.ts    ← Brain panel resize/collapse state
│   ├── useContextItems.ts       ← Context items CRUD
│   ├── useProjects.ts           ← Projects CRUD
│   ├── useSettings.ts           ← Settings management
│   ├── useSidebarState.ts       ← Sidebar collapse state (localStorage)
│   ├── useSmartPaste.ts         ← Image + long text paste support
│   └── useTrends.ts            ← Trends data fetching (independent of WS)
├── context/
│   └── AppContext.tsx            ← Global state: workers, projects, tasks,
│                                   connection status, WebSocket auto-reconnect
├── layouts/
│   └── AppLayout.tsx            ← Three-column: Sidebar | Content | Brain
├── pages/
│   ├── DashboardPage.tsx        ← Stats + activity + projects + workers
│   ├── ProjectsPage.tsx         ← Project list (card/table views + filter)
│   ├── ProjectDetailPage.tsx    ← Single project: tasks, workers, description
│   ├── TasksPage.tsx            ← Global task table with filters
│   ├── TaskDetailPage.tsx       ← Task detail: description, subtasks, worker
│   ├── WorkersPage.tsx          ← Worker list + Rdevs tab
│   ├── SessionDetailPage.tsx    ← Terminal + sidebar info
│   ├── ContextPage.tsx          ← Context items management
│   ├── NotificationsPage.tsx    ← Notification feed
│   └── SettingsPage.tsx         ← Config management
├── components/
│   ├── layout/
│   │   ├── Header.tsx           ← Tmux command (click-to-copy) + connection dot
│   │   └── StatsBar.tsx         ← Compact stats strip with breakdowns
│   ├── sidebar/
│   │   ├── Sidebar.tsx          ← Navigation with keyboard shortcuts
│   │   └── SidebarItem.tsx      ← Nav item with icon + badge
│   ├── brain/
│   │   ├── BrainPanel.tsx       ← Resizable right panel
│   │   └── BrainTerminal.tsx    ← xterm.js terminal for brain
│   ├── dashboard/
│   │   ├── RecentActivity.tsx   ← Activity feed derived from tasks/workers
│   │   ├── RecentActivity.css
│   │   ├── TrendsPanel.tsx      ← Container with range toggle + chart grid
│   │   ├── TrendsPanel.css
│   │   ├── ThroughputChart.tsx  ← Stacked bar chart (Recharts)
│   │   ├── WorkerHeatmap.tsx    ← Custom SVG heatmap (7x24 grid)
│   │   └── WorkerHoursChart.tsx ← Area chart (Recharts)
│   ├── projects/
│   │   ├── ProjectCard.tsx      ← Project summary card (card view)
│   │   ├── ProjectsTable.tsx    ← Sortable table with hiddenColumns support
│   │   ├── ProjectForm.tsx      ← Create project modal
│   │   └── ProjectEditModal.tsx ← Edit project modal
│   ├── tasks/
│   │   ├── TaskBoard.tsx        ← Kanban board view
│   │   ├── TaskCard.tsx         ← Task card in board/table
│   │   ├── TaskForm.tsx         ← Create/edit task modal
│   │   └── TaskTable.tsx        ← Table view of tasks
│   ├── workers/
│   │   ├── WorkerCard.tsx       ← Full worker card (workers page)
│   │   └── WorkerCardCompact.tsx← Compact card (dashboard) with terminal preview
│   ├── sessions/
│   │   └── AddSessionModal.tsx  ← Add worker modal
│   ├── rdevs/
│   │   ├── RdevTable.tsx        ← Rdev management table
│   │   └── CreateRdevModal.tsx  ← Create rdev modal
│   ├── terminal/
│   │   └── TerminalView.tsx     ← xterm.js terminal component
│   ├── context/
│   │   └── ContextModal.tsx     ← Context item editor
│   └── common/
│       ├── Modal.tsx
│       ├── ConfirmPopover.tsx
│       ├── ErrorBoundary.tsx
│       ├── FilterBar.tsx        ← Reusable filter dropdowns
│       ├── Icons.tsx            ← SVG icon components
│       ├── Markdown.tsx         ← Markdown renderer
│       ├── NotificationToast.tsx← Toast notifications
│       ├── SmartPastePopup.tsx  ← Paste detection UI
│       ├── TagDropdown.tsx      ← Tag picker dropdown
│       └── TimeAgo.tsx          ← Relative time formatting
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
/tasks                      → TasksPage
/tasks/:id                  → TaskDetailPage
/workers                    → WorkersPage
/workers/rdevs              → WorkersPage (rdevs tab)
/workers/:id                → SessionDetailPage (terminal)
/context                    → ContextPage
/notifications              → NotificationsPage
/settings                   → SettingsPage
```

---

## Design Principles

1. **Data-driven, not mocked.** Every piece of UI maps to a real API endpoint and database entity. No placeholder data.

2. **Context-rich.** Each entity shows its relationships — a worker card shows its project, task, and terminal preview. A task shows its worker, project, and subtasks.

3. **Actionable inline.** Tasks can be reassigned, workers can be messaged, projects can be created — all without deep navigation.

4. **Real-time.** WebSocket pushes trigger re-renders. Worker status, task progress, and activity feeds update live.

5. **Progressive disclosure.** List pages show summaries; clicking reveals full detail in dedicated pages. Terminal view is the deepest level of drill-down.

6. **Keyboard-friendly.** Global shortcuts: `D` → Dashboard, `P` → Projects, `T` → Tasks, `W` → Workers, `K` → Context, `N` → Notifications.

7. **Information density.** Designed for power users managing 10+ workers daily. Compact layouts, grouped events, and smart defaults over spacious layouts.

---

## Dark Theme Palette

```css
--bg:             #0d1117    /* Page background */
--surface:        #161b22    /* Cards, panels */
--surface-hover:  #1c2129    /* Hover state */
--surface-raised: #21262d    /* Elevated surfaces */
--border:         #30363d    /* Borders */
--border-subtle:  #21262d    /* Subtle borders */
--text-primary:   #e6edf3    /* Primary text */
--text-secondary: #8b949e    /* Secondary text */
--text-muted:     #484f58    /* Muted text */
--accent:         #58a6ff    /* Links, active items */
--accent-muted:   #388bfd26  /* Accent backgrounds */
--accent-hover:   #79c0ff    /* Accent hover */
--green:          #3fb950    /* Success, idle, done */
--green-muted:    #238636    /* Green backgrounds */
--yellow:         #d29922    /* Warning, waiting */
--yellow-muted:   #9e6a03    /* Yellow backgrounds */
--red:            #f85149    /* Error, critical, disconnected */
--red-muted:      #da3633    /* Red backgrounds */
--orange:         #db6d28    /* Paused, screen_detached */
--purple:         #a371f7    /* Special */
```

---

## Worker Naming Convention

Workers are named `{project-slug}_{adjective}-{noun}` (e.g., `premium-eng-portal_robust-valley`). In the UI, the project prefix is dimmed and the unique suffix is bolded for quick scanning. The full name is available on hover via title attribute.

---

## Worker Statuses

| Status | Color | Meaning |
|--------|-------|---------|
| idle | green | Connected, no task assigned |
| working | blue (accent) | Actively executing |
| waiting | yellow | Needs human input |
| paused | orange | Paused by user |
| error | red | Error state |
| disconnected | red | Lost connection |
| connecting | blue | Establishing connection |
| screen_detached | orange | tmux screen detached |
