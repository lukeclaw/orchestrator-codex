---
title: "Claude Orchestrator — Trends Section Design Spec"
author: Yudong Qiu
created: 2026-02-21
status: Draft
---

# Trends Section — Design Spec

## Overview

Add a **Trends** section to the Dashboard between "Recent Activity" and "Active Projects". This section surfaces historical patterns in task throughput, worker utilization, and project velocity through compact, information-dense visualizations that match the existing dark-themed DevOps aesthetic.

The goal is to answer at a glance: *How productive has the system been? Are things speeding up or slowing down? When are workers most active?*

---

## Data Foundation

### Problem: Missing Historical Data

The current schema only stores the **latest state** of each entity. Key gaps:

| Data point | Currently available | Gap |
|---|---|---|
| When a task was completed | `tasks.updated_at` when `status='done'` | Approximate — `updated_at` may be set by non-status edits |
| When a worker started/stopped working | `sessions.last_status_changed_at` | Only records the **most recent** transition, not history |
| How long a worker spent on a task | Not tracked | No start/end timestamps per assignment |
| Daily worker-hours | Not tracked | No status transition log |

### New Table: `status_events`

A lightweight append-only event log that records every status transition for tasks and workers.

```sql
CREATE TABLE status_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,        -- 'task' or 'session'
    entity_id TEXT NOT NULL,
    old_status TEXT,                   -- NULL for creation events
    new_status TEXT NOT NULL,
    project_id TEXT,                   -- denormalized for fast project-level queries
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    metadata TEXT                      -- JSON, optional (e.g., assigned_session_id for task pickup)
);

CREATE INDEX idx_status_events_type_ts ON status_events(entity_type, timestamp);
CREATE INDEX idx_status_events_entity ON status_events(entity_id, timestamp);
CREATE INDEX idx_status_events_project ON status_events(project_id, timestamp);
```

**Write path:** Insert a row whenever:
- A task status changes (todo→in_progress, in_progress→done, etc.)
- A worker status changes (idle→working, working→waiting, etc.)

This is a single `INSERT` added to the existing `update()` methods in `TaskRepository` and `SessionRepository`. No reads are in the hot path.

**Retention:** Keep 90 days of events. A daily cleanup query deletes rows older than 90 days. At ~100 events/day this table stays under 10k rows.

### Backfill from Existing Data

On first migration, seed `status_events` from current task data:
- For each task with `status='done'`: insert an event `(entity_type='task', new_status='done', timestamp=updated_at)`
- For each task with `status='in_progress'`: insert an event `(entity_type='task', new_status='in_progress', timestamp=updated_at)`

This gives immediate chart data from day one.

---

## Backend API

### `GET /api/trends/throughput`

Returns daily task and subtask completion counts.

**Query params:**
- `days` (int, default 30, max 90) — lookback window
- `project_id` (optional) — filter to one project

**Response:**
```json
{
  "period": "day",
  "data": [
    { "date": "2026-02-20", "tasks_completed": 5, "subtasks_completed": 12 },
    { "date": "2026-02-19", "tasks_completed": 3, "subtasks_completed": 8 },
    ...
  ]
}
```

**SQL:**
```sql
SELECT date(timestamp) as date,
       SUM(CASE WHEN e.entity_id NOT IN (SELECT id FROM tasks WHERE parent_task_id IS NOT NULL) THEN 1 ELSE 0 END) as tasks_completed,
       SUM(CASE WHEN e.entity_id IN (SELECT id FROM tasks WHERE parent_task_id IS NOT NULL) THEN 1 ELSE 0 END) as subtasks_completed
FROM status_events e
WHERE e.entity_type = 'task'
  AND e.new_status IN ('done', 'completed')
  AND e.timestamp >= date('now', '-' || ? || ' days')
GROUP BY date(timestamp)
ORDER BY date DESC
```

### `GET /api/trends/worker-activity`

Returns hourly worker activity for a heatmap grid.

**Query params:**
- `days` (int, default 28) — lookback window (rounded to full weeks for heatmap)

**Response:**
```json
{
  "data": [
    { "date": "2026-02-20", "hour": 14, "active_workers": 4 },
    { "date": "2026-02-20", "hour": 15, "active_workers": 6 },
    ...
  ]
}
```

**SQL:** Count distinct `entity_id` entries per (date, hour) where `entity_type='session'` and `new_status='working'`.

### `GET /api/trends/velocity`

Returns rolling 7-day average of tasks completed per day, plus cumulative totals.

**Query params:**
- `days` (int, default 30)
- `project_id` (optional)

**Response:**
```json
{
  "data": [
    { "date": "2026-02-20", "completed": 5, "cumulative": 253, "rolling_avg": 4.1 },
    ...
  ]
}
```

### `GET /api/trends/worker-hours`

Returns estimated daily worker-hours (time spent in `working` status).

**Query params:**
- `days` (int, default 30)

**Response:**
```json
{
  "data": [
    { "date": "2026-02-20", "total_hours": 18.5, "worker_count": 6 },
    ...
  ]
}
```

**Calculation:** For each worker, sum the duration between consecutive `working` → (any other status) transitions per day.

---

## Frontend Design

### Library Choice: Recharts

**Recharts** is the best fit for this project:
- React-native (composable JSX components, not imperative)
- Lightweight (~45kb gzip), tree-shakeable
- Built-in responsive containers
- Clean dark theme support via CSS variables
- No heavy dependencies (uses D3 internals, not the full D3 bundle)
- Good TypeScript support

Alternative considered: **Chart.js** — heavier, imperative API, less React-idiomatic. **D3** — too low-level for standard charts.

### Layout on Dashboard

The Trends section sits between Recent Activity and Active Projects. It contains a row of 2-3 compact chart panels at equal width.

```
┌──────────────────────────────────────────────────────────────────────┐
│  STATS BAR                                                           │
├──────────────────────────────────────────────────────────────────────┤
│  Recent Activity                                                     │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─ Trends ──────────────────────────────────────── [7d|30d|90d] ─┐ │
│  │                                                                  │ │
│  │  ┌─ Throughput ──────────┐  ┌─ Worker Activity ──────────────┐ │ │
│  │  │  ▁▃▅▇█▅▃▁▃▅▇█▅▃▁▃▅ │  │  ░░▒▒▓▓██▓▓▒▒░░  (heatmap)   │ │ │
│  │  │  5 tasks/day avg      │  │  Mon █████████░░░              │ │ │
│  │  │                       │  │  Tue ███████████░              │ │ │
│  │  │  [tasks] [subtasks]   │  │  Wed ████████░░░░              │ │ │
│  │  └───────────────────────┘  │  Thu ██████████░░              │ │ │
│  │                              │  Fri █████████░░░              │ │ │
│  │  ┌─ Worker Hours ────────┐  │  Sat ░░░░░░░░░░░░              │ │ │
│  │  │  ▁▃▅▇█▅▃▁▃▅▇█▅▃▁▃▅ │  │  Sun ░░░░░░░░░░░░              │ │ │
│  │  │  18.5h total today    │  └─────────────────────────────────┘ │ │
│  │  └───────────────────────┘                                       │ │
│  └──────────────────────────────────────────────────────────────────┘ │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│  Active Projects                                                     │
├──────────────────────────────────────────────────────────────────────┤
│  Workers                                                             │
└──────────────────────────────────────────────────────────────────────┘
```

### Responsive Behavior

| Breakpoint | Layout |
|---|---|
| > 1200px | 2-column grid: left column stacks Throughput + Worker Hours, right column has Heatmap |
| 900–1200px | 2-column grid, same layout but narrower |
| < 900px | Single column, all charts stacked vertically |

### Chart 1: Throughput (Bar Chart)

**Purpose:** How many tasks and subtasks are completed each day?

**Visual:**
- Stacked bar chart — tasks (accent blue `#58a6ff`) on bottom, subtasks (purple `#a371f7`) on top
- X-axis: dates (abbreviated: "Feb 20", "Feb 19", ...)
- Y-axis: count (auto-scaled, integers only)
- Hover tooltip: "Feb 20: 5 tasks, 12 subtasks"
- Summary stat above chart: "**4.1** tasks/day avg" (rolling 7-day)

**Dimensions:** Min-height 160px, max-height 200px. Responsive width.

```
   12 ┤
      │        ██
    8 ┤  ▓▓    ██
      │  ▓▓ ██ ██    ██
    4 ┤  ██ ██ ██ ██ ██ ▓▓
      │  ██ ██ ██ ██ ██ ██ ██
    0 ┼──────────────────────────
      Feb14  16  18  20  22

      ██ tasks  ▓▓ subtasks
```

**Interaction:**
- Hover shows tooltip with exact values
- Clicking a bar could navigate to a filtered tasks view (stretch goal, not MVP)

### Chart 2: Worker Activity Heatmap

**Purpose:** When are workers most active? Identify patterns (e.g., most work happens 9am–5pm weekdays).

**Visual:**
- Grid: rows = days of week (Mon–Sun), columns = hours (0–23) or recent dates
- Cell color intensity = number of active workers at that (day, hour)
- Color scale: transparent → green (`#238636` → `#3fb950`) matching the existing "success" palette
- Labels: day abbreviations on Y-axis, hour labels on X-axis (every 3h: 0, 3, 6, 9, 12, 15, 18, 21)

**Option A — Day-of-week × Hour-of-day (aggregated):**
Best for seeing weekly work patterns. Aggregates across all weeks in the window.

```
        0   3   6   9   12  15  18  21
   Mon  ░   ░   ░   ▒   ▓   █   ▓   ░
   Tue  ░   ░   ░   ▓   █   █   ▒   ░
   Wed  ░   ░   ░   ▒   ▓   ▓   ▒   ░
   Thu  ░   ░   ░   ▓   █   █   ▓   ░
   Fri  ░   ░   ░   ▒   ▓   ▓   ▒   ░
   Sat  ░   ░   ░   ░   ░   ░   ░   ░
   Sun  ░   ░   ░   ░   ░   ▒   ░   ░
```

**Option B — Calendar heatmap (GitHub-style):**
A horizontal grid where each column = one day, color = total completions that day. Simpler to implement, immediately recognizable.

```
   Mon  ░ ▒ ░ ▓ █ ▒ ░ ▓ █ ▒ ░ ▒ ░ ▓
   Wed  ░ ░ ▒ ▒ ▓ ░ ▒ ▓ ▓ ░ ▒ ▓ ░ ▒
   Fri  ░ ▒ ▓ ░ ▒ ▓ ░ ▒ ▒ ░ ▓ ▒ ░ ░
          ←  4 weeks ago            today →
```

**Recommendation:** Start with **Option A** (day×hour) — it provides more actionable insight about *when* workers are active and helps identify utilization gaps.

**Dimensions:** 240px height (fixed), full available width.

**Interaction:**
- Hover tooltip: "Tuesday 2pm: avg 4.2 active workers"
- Color legend: subtle gradient bar below the chart

### Chart 3: Worker Hours (Area Chart)

**Purpose:** How many total worker-hours were logged each day?

**Visual:**
- Filled area chart with gradient fill (accent blue, 20% opacity fill)
- Line color: `#58a6ff` (accent)
- Fill: linear gradient from `#58a6ff33` (top) to `transparent` (bottom)
- X-axis: dates
- Y-axis: hours (decimal, e.g., 18.5h)
- Summary stat: "**18.5h** today · **142h** this week"

**Dimensions:** Same as Throughput — min-height 160px, max-height 200px.

```
   24h ┤
      │           ╭─╮
   18h ┤     ╭───╯   ╰──╮
      │  ╭─╯              ╰─╮
   12h ┤ ╯                    ╰╮
      │╱░░░░░░░░░░░░░░░░░░░░░░╲
    6h ┤░░░░░░░░░░░░░░░░░░░░░░░░
      │░░░░░░░░░░░░░░░░░░░░░░░░
    0h ┼────────────────────────
      Feb14  16  18  20  22
```

### Time Range Selector

A pill toggle in the section header: **7d** | **30d** | **90d**

- Default: **30d**
- All three charts update simultaneously when the range changes
- State is local (not persisted to localStorage — trends are ephemeral)

**Styling:** Same as existing filter buttons — small, borderless pills with `accent-muted` background when active.

```css
.trends-range-btn {
  padding: 2px 10px;
  font-size: 12px;
  background: transparent;
  color: var(--text-secondary);
  border: 1px solid var(--border);
  border-radius: 4px;
  cursor: pointer;
}
.trends-range-btn.active {
  background: var(--accent-muted);
  color: var(--accent);
  border-color: var(--accent);
}
```

---

## Component Architecture

### New Files

```
frontend/src/
├── components/
│   └── dashboard/
│       ├── TrendsSection.tsx          ← Container: fetches data, manages range state
│       ├── TrendsSection.css
│       ├── ThroughputChart.tsx        ← Stacked bar chart (Recharts)
│       ├── WorkerActivityHeatmap.tsx  ← Custom SVG heatmap (no Recharts needed)
│       └── WorkerHoursChart.tsx       ← Area chart (Recharts)
├── hooks/
│   └── useTrends.ts                   ← Data fetching hook for /api/trends/*

orchestrator/
├── api/
│   └── routes/
│       └── trends.py                  ← New route module
├── state/
│   ├── repositories/
│   │   └── status_events.py           ← Event insert + aggregation queries
│   └── migrations/
│       └── versions/
│           └── 027_add_status_events.sql
```

### TrendsSection.tsx

```tsx
// Pseudocode structure
export default function TrendsSection() {
  const [range, setRange] = useState<7 | 30 | 90>(30)
  const { throughput, activity, hours, loading } = useTrends(range)

  return (
    <section className="trends panel">
      <div className="panel-header">
        <h2>Trends</h2>
        <div className="trends-range">
          {[7, 30, 90].map(d => (
            <button
              key={d}
              className={`trends-range-btn ${range === d ? 'active' : ''}`}
              onClick={() => setRange(d)}
            >{d}d</button>
          ))}
        </div>
      </div>
      <div className="trends-grid">
        <div className="trends-col-left">
          <ThroughputChart data={throughput} />
          <WorkerHoursChart data={hours} />
        </div>
        <div className="trends-col-right">
          <WorkerActivityHeatmap data={activity} />
        </div>
      </div>
    </section>
  )
}
```

### useTrends Hook

```tsx
function useTrends(days: number) {
  // Fetches all three endpoints in parallel
  // Returns { throughput, activity, hours, loading, error }
  // Re-fetches when `days` changes
  // Does NOT use AppContext — trends data is independent
}
```

### WorkerActivityHeatmap (Custom SVG)

The heatmap is a custom SVG component rather than a Recharts chart, since Recharts doesn't have a native heatmap. This keeps it lightweight and gives full control over the grid layout.

```tsx
// Renders a 7×24 grid (days × hours)
// Each cell is a <rect> with fill color interpolated from the data
// Color scale: transparent → --green-muted → --green
// Tooltip on hover via a positioned <div>
```

**Color scale function:**
```ts
function heatColor(value: number, max: number): string {
  if (value === 0) return 'transparent'
  const intensity = Math.min(value / max, 1)
  // 4 stops: 0=transparent, 0.25=#23863644, 0.5=#238636, 1.0=#3fb950
  if (intensity < 0.25) return `rgba(35, 134, 54, ${intensity * 2.6})`
  if (intensity < 0.5) return '#238636'
  return '#3fb950'
}
```

---

## CSS Styling

All chart styling uses CSS variables for consistency. No inline colors in chart components.

```css
/* TrendsSection.css */

.trends {
  margin-bottom: 16px;
}

.trends-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  padding: 14px 18px;
}

.trends-col-left {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.trends-col-right {
  display: flex;
  flex-direction: column;
}

/* Chart card */
.trend-card {
  background: var(--bg);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius);
  padding: 12px 14px;
}

.trend-card-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 8px;
}

.trend-card-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.trend-card-stat {
  font-size: 13px;
  color: var(--text-primary);
}

.trend-card-stat strong {
  color: var(--accent);
  font-variant-numeric: tabular-nums;
}

/* Recharts overrides for dark theme */
.trends .recharts-cartesian-axis-tick-value {
  fill: var(--text-muted);
  font-size: 11px;
}

.trends .recharts-cartesian-grid line {
  stroke: var(--border-subtle);
}

.trends .recharts-tooltip-wrapper .recharts-default-tooltip {
  background: var(--surface-raised) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--radius);
  font-size: 12px;
}

/* Range toggle */
.trends-range {
  display: flex;
  gap: 4px;
}

.trends-range-btn {
  padding: 2px 10px;
  font-size: 12px;
  font-weight: 500;
  background: transparent;
  color: var(--text-secondary);
  border: 1px solid var(--border);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;
}

.trends-range-btn:hover {
  color: var(--text-primary);
  border-color: var(--text-muted);
}

.trends-range-btn.active {
  background: var(--accent-muted);
  color: var(--accent);
  border-color: var(--accent);
}

/* Heatmap cells */
.heatmap-cell {
  rx: 2;
  transition: opacity 0.1s;
}

.heatmap-cell:hover {
  stroke: var(--text-secondary);
  stroke-width: 1;
}

/* Responsive */
@media (max-width: 900px) {
  .trends-grid {
    grid-template-columns: 1fr;
  }
}
```

---

## Recharts Theme Config

Shared constants for all Recharts charts to ensure visual consistency:

```ts
// chartTheme.ts
export const CHART_COLORS = {
  tasks: '#58a6ff',       // accent blue
  subtasks: '#a371f7',    // purple
  workerHours: '#58a6ff', // accent blue
  grid: '#21262d',        // border-subtle
  axis: '#484f58',        // text-muted
  tooltip: {
    bg: '#21262d',
    border: '#30363d',
    text: '#e6edf3',
  },
}

export const CHART_MARGIN = { top: 4, right: 4, bottom: 0, left: -10 }
```

---

## Empty & Loading States

**Loading:** Show a subtle pulse animation placeholder matching the chart dimensions. Use CSS-only animation (no skeleton library).

```css
.trend-card-loading {
  background: linear-gradient(90deg, var(--bg) 25%, var(--surface) 50%, var(--bg) 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
  border-radius: var(--radius);
}
@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
```

**Empty state (no data):** Show a muted message inside the chart area: "No data yet — trends appear as tasks are completed."

**Insufficient data:** If fewer than 3 data points, still render the chart (don't hide it) — even a single bar is useful context.

---

## Performance Considerations

1. **Lazy loading:** The `useTrends` hook fetches only when the dashboard mounts, not in `AppContext`. Trends data is not part of the WebSocket refresh cycle — it's fetched once and cached with a 60-second TTL.

2. **Backend query efficiency:** All aggregation queries use the `status_events` table with indexed `(entity_type, timestamp)`. No joins required for the basic charts. The table stays small (<10k rows with 90-day retention).

3. **Render performance:** Recharts uses SVG, which handles up to ~200 data points without issue. 90 days = 90 bars, well within limits. The heatmap is 7×24 = 168 cells, trivial.

4. **Bundle size:** Recharts adds ~45kb gzipped. This is acceptable for a desktop dashboard app. Tree-shaking ensures only used chart types are bundled (BarChart, AreaChart, Tooltip, ResponsiveContainer).

---

## Data Integrity & Edge Cases

| Scenario | Handling |
|---|---|
| Task edited (not status-changed) triggers `updated_at` | Only insert `status_events` when `status` column actually changes, not on every update |
| Worker restarts quickly (working→disconnected→working in 1 min) | All transitions are logged; short gaps show accurately in worker-hours |
| No workers active on a day | That day has 0h in worker-hours chart; bar is absent (not zero-height) |
| System downtime (no events logged) | Gap in data shows as gap in charts; no interpolation |
| Subtask vs parent task | Throughput chart distinguishes them via `parent_task_id IS NOT NULL` check |
| Time zones | All timestamps are UTC in the database; frontend converts to local time for display labels |

---

## Implementation Phases

### Phase 1: Data Layer (Backend)

1. Create migration `027_add_status_events.sql` with table + indexes
2. Add `StatusEventRepository` with `insert()` and aggregation query methods
3. Modify `TaskRepository.update()` and `SessionRepository.update()` to emit events on status changes
4. Backfill existing task data into `status_events`
5. Add `/api/trends/throughput` endpoint
6. Add `/api/trends/worker-activity` endpoint
7. Add `/api/trends/worker-hours` endpoint

### Phase 2: Charts (Frontend)

1. Install Recharts: `npm install recharts`
2. Create `useTrends` hook with parallel data fetching
3. Build `ThroughputChart` (stacked bar)
4. Build `WorkerHoursChart` (area)
5. Build `WorkerActivityHeatmap` (custom SVG)
6. Build `TrendsSection` container with range toggle
7. Integrate into `DashboardPage` between Recent Activity and Active Projects

### Phase 3: Polish

1. Add loading shimmer states
2. Add empty states
3. Add hover tooltips
4. Responsive testing at all breakpoints
5. Update `docs/003-ui-design.md` with the Trends section

---

## Visual Reference — Full Dashboard with Trends

```
┌──────────────────────────────────────────────────────────────────────┐
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────────┐ │
│  │ 11  WORKERS   │  │ 4  PROJECTS  │  │ 8  IN-PROGRESS TASKS      │ │
│  │    3 working  │  │              │  │    253 done · 96 todo      │ │
│  └──────────────┘  └──────────────┘  └────────────────────────────┘ │
│                                                                      │
│  ┌─ Recent Activity ────────────────────────────────────────────────┐│
│  │  ✓ PENP-7 completed — Improve dashboard performance    2h ago   ││
│  │  ⏸ 8 workers waiting for input                        41m ago   ││
│  │  ...                                                             ││
│  └──────────────────────────────────────────────────────────────────┘│
│                                                                      │
│  ┌─ Trends ──────────────────────────────────────── [7d|30d|90d] ──┐│
│  │  ┌─ THROUGHPUT ─── 4.1/day ─┐  ┌─ WORKER ACTIVITY ────────────┐││
│  │  │                           │  │      0  3  6  9  12 15 18 21 │││
│  │  │   12 ┤        ▓▓          │  │ Mon  ░  ░  ░  ▒  ▓  █  ▓  ░ │││
│  │  │    8 ┤  ██    ██          │  │ Tue  ░  ░  ░  ▓  █  █  ▒  ░ │││
│  │  │    4 ┤  ██ ██ ██ ██ ██   │  │ Wed  ░  ░  ░  ▒  ▓  ▓  ▒  ░ │││
│  │  │    0 ┼────────────────    │  │ Thu  ░  ░  ░  ▓  █  █  ▓  ░ │││
│  │  │      ██ tasks ▓▓ subtasks │  │ Fri  ░  ░  ░  ▒  ▓  ▓  ▒  ░ │││
│  │  └───────────────────────────┘  │ Sat  ░  ░  ░  ░  ░  ░  ░  ░ │││
│  │  ┌─ WORKER HOURS ── 18.5h ──┐  │ Sun  ░  ░  ░  ░  ░  ▒  ░  ░ │││
│  │  │        ╭─╮                │  │                               │││
│  │  │   ╭───╯   ╰──╮           │  │  ░ 0  ▒ 1-2  ▓ 3-5  █ 6+   │││
│  │  │  ╯░░░░░░░░░░░░╰╮         │  └───────────────────────────────┘││
│  │  │ ░░░░░░░░░░░░░░░░╲        │                                   ││
│  │  └───────────────────────────┘                                   ││
│  └──────────────────────────────────────────────────────────────────┘│
│                                                                      │
│  ┌─ Active Projects ──────────────────────────── [+ New Project] ───┐│
│  │  NAME          TASKS  SUBTASKS  PROGRESS  WORKERS     UPDATED    ││
│  │  ...                                                              ││
│  └──────────────────────────────────────────────────────────────────┘│
│                                                                      │
│  ┌─ Workers ──────────────────────────────────── [+ Add Worker] ────┐│
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐                 ││
│  │  │ worker-1   │  │ worker-2   │  │ worker-3   │                 ││
│  │  └────────────┘  └────────────┘  └────────────┘                 ││
│  └──────────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────┘
```

---

## Open Questions

1. **Heatmap variant:** Option A (day×hour aggregated) vs Option B (GitHub-style calendar). Recommendation is A, but B is simpler and may be more intuitive.

2. **Project-level filtering:** Should the Trends section have an optional project filter dropdown, or always show system-wide data? The API supports project filtering, but the UI could start without it.

3. **Worker-hours accuracy:** Since we're deriving hours from status transitions, gaps in logging (e.g., orchestrator was off) will undercount. Should we show a "data quality" indicator, or accept the approximation?

4. **Velocity chart:** A fourth chart showing rolling 7-day velocity (line chart) could replace or complement the throughput bar chart. Worth adding in a later iteration?
