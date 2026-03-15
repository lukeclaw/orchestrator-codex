# Human-Hours Tracking Feature

Track the human user's active time on the Orchestrator app and display it alongside worker-hours on the dashboard Trends panel.

## Context & Existing Infrastructure

**Worker-hours tracking** already works end-to-end:
- `status_events` table records session state transitions (`working` -> `waiting`, etc.)
- `status_events.query_worker_hours()` computes daily hours from working->non-working intervals, with cross-midnight splitting and open-interval clamping
- `WorkerHoursChart.tsx` renders an area chart; clicking a day opens `TrendDetailModal` with per-worker timeline bars
- `TrendsPanel.tsx` composes all three trend charts (throughput, heatmap, worker-hours)

**User activity signals already exist**:
- `ws_terminal.py` tracks `_session_last_input` per session (keystroke timestamps)
- `AppContext.tsx` sends `focus_update` via WebSocket on route changes
- `websocket.py` stores `_current_focus_url` from frontend

**Key constraint**: The app runs as a Tauri desktop app. There is exactly one human user. No multi-user model.

**Latest migration**: `031_add_rws_pty_id.sql`. Next migration is `032`.

---

## Design

### 1. What counts as "active human time"

Active time is any period where the user is interacting with the Orchestrator app. An activity heartbeat is emitted on:

- **Direct user interaction in the frontend**: clicks, keyboard input, scrolls
- **Terminal input**: typing into a worker terminal (already tracked in `ws_terminal.py`)
- **Window focus**: the Orchestrator Tauri window is focused

**What does NOT count as activity**:
- Mouse movement alone (too noisy — desk vibrations, idle cursor drift inflate hours)
- The Orchestrator window being visible but unfocused (user is in another app)
- Backend running headless with no frontend connected

**Idle timeout**: If **5 minutes** pass with no heartbeat, the user is considered inactive. The current active interval ends at the timestamp of the last heartbeat (not the moment the timeout fires).

### 2. Data flow

```
Frontend (heartbeat)
    -> WebSocket message { type: "user_activity" }
    -> websocket.py calls tracker.record_heartbeat()
    -> tracker updates in-memory _last_heartbeat timestamp

Terminal input (ws_terminal.py)
    -> record_user_input() also calls tracker.record_heartbeat()

Background loop (every 30s):
    -> Checks: was user active in last 5min?
    -> If active and no open interval: start new interval in DB
    -> If idle and open interval exists: close it (end_time = last_heartbeat)
```

### 3. Database: `human_activity_events` table

```sql
CREATE TABLE IF NOT EXISTS human_activity_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time TEXT NOT NULL,       -- ISO UTC timestamp
    end_time TEXT                   -- ISO UTC timestamp, NULL = still active
);
CREATE INDEX idx_human_activity_start ON human_activity_events (start_time);
CREATE INDEX idx_human_activity_end ON human_activity_events (end_time);
```

**Why a separate table instead of reusing `status_events`?**
- `status_events` is keyed by `entity_type`/`entity_id` (sessions, tasks) — human activity has no entity
- Different query patterns: human hours need interval-based queries, not state-transition queries
- Cleaner separation; no risk of conflicting with existing worker-hours aggregation

Each row is one continuous activity interval. When the user goes idle, `end_time` is set. When they return, a new row is created. Storage-efficient: ~one row per active session (not per heartbeat).

**No `source` column** — the plan originally included a `source TEXT DEFAULT 'app'` column, but since we never query by source and don't distinguish terminal vs. dashboard activity in v1, it's omitted (YAGNI).

### 4. Backend changes

#### 4a. Migration: `032_add_human_activity_events.sql`

Creates the `human_activity_events` table with both indexes.

#### 4b. Repository: `orchestrator/state/repositories/human_activity.py`

- `start_interval(conn) -> int` — Insert row with `start_time=utc_now_iso()`, `end_time=NULL`. Returns row id.
- `close_interval(conn, interval_id, end_time: str)` — Set `end_time` on the given row.
- `get_open_interval(conn) -> Row | None` — Find row where `end_time IS NULL`.
- `close_stale_intervals(conn, idle_timeout_seconds=300)` — **Startup recovery**: find any `end_time IS NULL` row and set `end_time = start_time + idle_timeout`. This handles crash/force-quit/power-loss where shutdown cleanup never ran.
- `query_human_hours(conn, since_date: str) -> list[dict]` — Aggregate hours per local date. **Must replicate** the exact same logic as `query_worker_hours`: clamp open intervals to `now`, split cross-midnight intervals at local midnight using `_add_interval`. Returns `[{date, hours}]`.
- `query_human_hours_detail(conn, date: str) -> list[dict]` — Per-interval detail for a specific local date. Returns `[{start, end, hours}]` clamped to the target date boundaries (same pattern as `query_worker_hours_detail`).
- `cleanup_old_events(conn, retention_days=180)` — Delete rows where `start_time < cutoff`.

#### 4c. Activity tracker: `orchestrator/core/human_tracker.py`

A lightweight async background task:

```python
IDLE_TIMEOUT = 300  # 5 minutes

class HumanActivityTracker:
    def __init__(self, conn_factory: ConnectionFactory):
        self._last_heartbeat: float = 0
        self._conn_factory = conn_factory
        self._task: asyncio.Task | None = None

    def record_heartbeat(self):
        """Called from WebSocket handler or terminal input. In-memory only."""
        self._last_heartbeat = time.time()

    async def start(self):
        """Start the background polling loop."""
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        """Stop the loop and close any open interval."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Graceful shutdown: close open interval with last heartbeat time
        if self._last_heartbeat > 0:
            await asyncio.get_event_loop().run_in_executor(
                None, self._close_open_interval
            )

    def _close_open_interval(self):
        """Sync helper: close any open interval in DB."""
        with self._conn_factory.connection() as conn:
            open_iv = human_activity.get_open_interval(conn)
            if open_iv:
                end_time = datetime.fromtimestamp(
                    self._last_heartbeat, tz=UTC
                ).isoformat()
                human_activity.close_interval(conn, open_iv["id"], end_time)
                conn.commit()

    async def _run(self):
        """Background loop, checks every 30s."""
        loop = asyncio.get_event_loop()
        while True:
            try:
                await asyncio.sleep(30)
                await loop.run_in_executor(None, self._tick)
            except asyncio.CancelledError:
                logger.info("Human activity tracker stopped")
                break
            except Exception:
                logger.exception("Human activity tracker error (non-fatal)")

    def _tick(self):
        """One poll cycle. Runs in executor to avoid blocking event loop."""
        now = time.time()
        is_active = (now - self._last_heartbeat) < IDLE_TIMEOUT

        with self._conn_factory.connection() as conn:
            open_interval = human_activity.get_open_interval(conn)

            if is_active and open_interval is None:
                human_activity.start_interval(conn)
                conn.commit()
            elif not is_active and open_interval is not None:
                end_time = datetime.fromtimestamp(
                    self._last_heartbeat, tz=UTC
                ).isoformat()
                human_activity.close_interval(
                    conn, open_interval["id"], end_time
                )
                conn.commit()
```

**Key design decisions**:
- **`run_in_executor`** for all DB operations — prevents blocking the async event loop (existing pattern used by backup schedule).
- **`CancelledError` caught** in the loop — matches every other background task in the codebase (orchestrator, backup, rdev refresh).
- **Error handling inside loop body** — a DB error won't kill the background task permanently.
- **No in-memory `_current_interval_id`** — the loop always reads from DB (`get_open_interval`) to decide state. Simpler and crash-safe.
- **`stop()` closes open interval** — graceful shutdown sets `end_time` to last heartbeat.

**Performance**: One DB write every ~5 minutes (interval open/close). The heartbeat itself is just an in-memory timestamp update — zero DB cost per heartbeat.

#### 4d. WebSocket handler update (`websocket.py`)

Add handler for `user_activity` message type:

```python
elif msg_type == "user_activity":
    tracker = getattr(websocket.app.state, "human_tracker", None)
    if tracker:
        tracker.record_heartbeat()
```

#### 4e. Terminal input bridge (`ws_terminal.py`)

In the existing `record_user_input()` function, also call the human tracker:

```python
def record_user_input(session_id: str) -> None:
    """Record that user sent input to a session."""
    _session_last_input[session_id] = time.time()
    # Also count as human activity for hours tracking
    # (import at module level or use lazy import to avoid circular deps)
    _notify_human_tracker()
```

The `_notify_human_tracker()` helper accesses the tracker from the app state or a module-level reference set during startup.

#### 4f. Trends API update (`routes/trends.py`)

**`GET /api/trends`** response gains `human_hours` field:
```python
human_hours = human_activity.query_human_hours(conn, since)
return {
    "range": ...,
    "throughput": ...,
    "heatmap": ...,
    "worker_hours": ...,
    "human_hours": human_hours,  # NEW
}
```

**`GET /api/trends/detail`** gains `human_hours` chart type:
```python
elif chart == "human_hours":
    if not date:
        raise HTTPException(status_code=400, detail="date required")
    items = human_activity.query_human_hours_detail(conn, date)
    return {"chart": chart, "date": date, "items": items}
```

#### 4g. Lifespan wiring (`app.py`)

```python
# In lifespan(), after StateManager start:
human_tracker = None
if db_path:
    from orchestrator.core.human_tracker import HumanActivityTracker
    human_tracker = HumanActivityTracker(app.state.conn_factory)
    app.state.human_tracker = human_tracker
    # Startup recovery: close stale intervals from crash/force-quit
    with app.state.conn_factory.connection() as conn:
        human_activity.close_stale_intervals(conn)
    await human_tracker.start()

# ... yield ...

# In shutdown, BEFORE stopping orchestrator:
if human_tracker:
    await human_tracker.stop()

# Cleanup old events (alongside existing status_events cleanup):
try:
    with app.state.conn_factory.connection() as conn:
        human_activity.cleanup_old_events(conn, retention_days=180)
except Exception:
    logger.exception("Human activity cleanup failed (non-fatal)")
```

**Startup recovery** is critical: after a crash or force-quit, there will be a dangling `end_time IS NULL` row. `close_stale_intervals()` finds it and sets `end_time = start_time + IDLE_TIMEOUT` (conservative — better to undercount than overcount).

**Guard on `if db_path:`** matches existing patterns (`StateManager`, `start_background_refresh`).

### 5. Frontend changes

#### 5a. Activity heartbeat (`AppContext.tsx`)

Add a throttled heartbeat sender. Fires on user interaction events:

```typescript
// In AppProvider, after WebSocket setup:
useEffect(() => {
  let lastSent = 0
  const THROTTLE_MS = 30_000 // At most once per 30s

  const onActivity = () => {
    const now = Date.now()
    if (now - lastSent > THROTTLE_MS && wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'user_activity' }))
      lastSent = now
    }
  }

  // Click, keyboard, and scroll — but NOT mousemove (too noisy)
  window.addEventListener('keydown', onActivity, { passive: true })
  window.addEventListener('click', onActivity, { passive: true })
  window.addEventListener('scroll', onActivity, { passive: true })

  return () => {
    window.removeEventListener('keydown', onActivity)
    window.removeEventListener('click', onActivity)
    window.removeEventListener('scroll', onActivity)
  }
}, [])
```

**Also send heartbeat on WebSocket reconnect** — add to the existing `ws.onopen` callback:

```typescript
ws.onopen = () => {
  setConnected(true)
  ws.send(JSON.stringify({ type: 'focus_update', url: locationRef.current }))
  ws.send(JSON.stringify({ type: 'user_activity' }))  // NEW
}
```

This prevents false idle gaps during brief network disconnections.

**Why no `mousemove`**: Mouse movement alone doesn't indicate active engagement. Desk vibrations, idle cursor drift, or the user reading a document in another window while the cursor sits over Orchestrator would all register as activity. Using only click/keyboard/scroll gives a much more accurate signal.

**Why no `visibilitychange`**: In Tauri, `document.hidden` behavior differs from browsers. The Tauri window can be visible but unfocused (the user is in VS Code). We don't want to count "visible but unfocused" as active time. A future enhancement could use Tauri's native `appWindow.onFocusChanged()` API for precise window focus tracking, but v1 relies on interaction events which are always accurate.

**Performance**: Throttled to 1 WebSocket message per 30 seconds max. Event listeners use `{ passive: true }`. Zero impact on UI responsiveness.

#### 5b. Types update (`api/types.ts`)

```typescript
// Existing — no change
export interface WorkerHoursDay {
  date: string
  hours: number
}

// Reuse WorkerHoursDay for human hours (same shape)
export type HumanHoursDay = WorkerHoursDay

export interface TrendsData {
  range: string
  throughput: ThroughputDay[]
  heatmap: HeatmapCell[]
  worker_hours: WorkerHoursDay[]
  human_hours: HumanHoursDay[]  // NEW
}

// Detail item for human hours modal
export interface HumanHoursDetailItem {
  start: string
  end: string
  hours: number
}

// Extend the detail selection union
export type TrendDetailSelection =
  | { chart: 'throughput'; date: string }
  | { chart: 'worker_hours'; date: string }
  | { chart: 'human_hours'; date: string }    // NEW
  | { chart: 'heatmap'; day_of_week: number; hour: number }
```

#### 5c. WorkerHoursChart update — dual series with separate Y-axes

Overlay human-hours as a second `<Area>` on the existing `WorkerHoursChart`. Key challenge: worker-hours can reach 20-50h/day (parallel workers) while human-hours caps at ~16h/day. A shared Y-axis would make the human line nearly invisible.

**Solution: Dual Y-axes.**

```typescript
interface Props {
  workerData: WorkerHoursDay[]
  humanData: HumanHoursDay[]   // NEW
  range: string
  onWorkerClick?: (date: string) => void
  onHumanClick?: (date: string) => void   // NEW
}
```

Chart changes:
- **Left Y-axis**: Worker-hours (green area, existing)
- **Right Y-axis**: Human-hours (blue/purple area, new), independently scaled
- **fillDays** updated to produce merged records: `{ date, workerHours, humanHours }`
- **Header**: `"Worker: 4.2h/day avg  ·  Human: 3.1h/day avg"`
- **Tooltip**: Shows both values, color-coded
- **Gradient**: New `humanHoursGradient` definition (blue/purple tones)

**Click disambiguation**: Since two areas overlap, use the `onClick` on the `AreaChart` level. The detail modal shows a combined view (worker breakdown + human intervals) for the clicked date. The modal title includes both totals. This avoids needing to detect which area was clicked (which Recharts doesn't natively support for overlapping areas).

Alternatively, if the combined modal is too busy: on click, open the modal with a tab bar (Worker | Human) defaulting to Worker.

**Graceful degradation**: If `humanData` is empty (new install, or data hasn't accumulated yet), hide the human area, the right Y-axis, and the human stat in the header. Show only the existing worker-hours chart.

#### 5d. TrendDetailModal update

Add `HumanHoursContent` component for `chart === 'human_hours'`:

```typescript
function HumanHoursContent({ items }: { items: HumanHoursDetailItem[] }) {
  const totalHours = items.reduce((s, i) => s + i.hours, 0)
  return (
    <>
      <p className="trend-detail-summary">
        {totalHours.toFixed(1)}h active, {items.length} session{items.length !== 1 ? 's' : ''}
      </p>
      {/* Reuse the same TimelineBar component from worker-hours */}
      <div className="worker-hours-table">
        <div className="worker-hours-axis-row">...</div>
        <div className="worker-hours-row">
          <div className="worker-hours-label-col">
            <span className="worker-hours-label">You</span>
          </div>
          <div className="worker-hours-timeline-col">
            <TimelineBar intervals={items} />
          </div>
        </div>
      </div>
    </>
  )
}
```

Also update `buildDetailQuery` to handle `chart === 'human_hours'` and `getTitle` to return `"Your Hours — {date}"`.

#### 5e. TrendsPanel + useTrends updates

- `useTrends` already fetches `/api/trends` — the new `human_hours` field comes for free.
- `TrendsPanel` passes `data.human_hours` to `WorkerHoursChart` as the `humanData` prop.
- Pass a new `onHumanClick` handler that sets `detailSelection` with `chart: 'human_hours'`.

### 6. Terminal input as activity signal

`ws_terminal.py` already calls `record_user_input(session_id)` when the user types. We also call `tracker.record_heartbeat()` from this path so terminal typing counts as human activity without requiring the frontend heartbeat.

This handles the case where the backend runs headless or the user interacts only through terminals.

---

## Edge Cases & Mitigations

### Crash / Force-Quit Recovery
**Problem**: If the app crashes, `SIGKILL`s, or loses power, the lifespan shutdown never runs. An open interval (`end_time IS NULL`) is left in the DB. On next startup, `query_human_hours` would clamp it to "now," producing a massively inflated hours count.

**Mitigation**: `close_stale_intervals()` runs at startup (section 4g). It finds any `end_time IS NULL` row and sets `end_time = start_time + IDLE_TIMEOUT`. This conservatively caps the orphaned interval at 5 minutes rather than hours/days.

### WebSocket Disconnection Gap
**Problem**: When the WebSocket disconnects (network blip), no heartbeats are sent. If the disconnect exceeds 5 minutes, the backend closes the interval. On reconnect, the user must interact before a new interval opens — creating a false idle gap.

**Mitigation**: Send a `user_activity` heartbeat immediately on `ws.onopen` (section 5a). This ensures the new interval starts promptly after reconnection.

### Event Loop Blocking
**Problem**: The tracker's `_tick()` does synchronous SQLite operations. If the DB is locked (30s busy_timeout), it blocks the calling thread.

**Mitigation**: All DB operations run in `run_in_executor()` (section 4c), so they execute in the threadpool, never blocking the async event loop. This matches the pattern used by the backup schedule.

### App Running Without Frontend
**Problem**: If the backend runs headless, no WebSocket clients exist and no UI heartbeats arrive. Terminal input can still trigger heartbeats via `ws_terminal.py`.

**Mitigation**: This is correct behavior — if the user is typing in terminals but not using the UI, that still counts as active human time. If no one is interacting at all, no intervals are created. The chart simply shows 0h for those days.

### Cross-Midnight Intervals
**Problem**: A user is active from 11 PM to 1 AM. The interval spans two calendar dates.

**Mitigation**: `query_human_hours` uses the same `_add_interval` helper (or equivalent) from `status_events.py` that splits intervals at local midnight boundaries and attributes hours to the correct calendar date.

### First-Time / Empty Data
**Problem**: When a user upgrades to a version with this feature, `human_hours` is empty. Showing "Human: 0.0h/day" alongside real worker-hours is confusing.

**Mitigation**: The frontend hides the human-hours series, right Y-axis, and header stat when `humanData` is empty. The chart degrades to the existing worker-hours-only view.

---

## Implementation Steps

1. **Migration** — `032_add_human_activity_events.sql`
2. **Repository** — `human_activity.py` with interval CRUD, query functions, startup recovery, cleanup
3. **Tracker** — `human_tracker.py` background task with `start()`/`stop()`/`record_heartbeat()`
4. **Backend wiring** — WebSocket handler, terminal input bridge, trends API, lifespan (startup recovery + shutdown + cleanup)
5. **Frontend heartbeat** — `AppContext.tsx` throttled activity sender + reconnect heartbeat
6. **Frontend types** — Update `types.ts` with `HumanHoursDay`, `HumanHoursDetailItem`, extended `TrendDetailSelection`
7. **Chart update** — Dual Y-axis overlay on `WorkerHoursChart`, merged `fillDays`, dual header stats
8. **Detail modal** — `HumanHoursContent` component, updated `buildDetailQuery` and `getTitle`
9. **TrendsPanel wiring** — Pass `humanData` and `onHumanClick` through
10. **Tests** — Unit tests for repository (interval CRUD, query with midnight splitting, stale cleanup), tracker (active/idle transitions, shutdown, error recovery), API (trends response shape, detail endpoint), frontend type checks

## Non-Goals (v1)

- Tracking time per-project or per-task (tracks total app usage only)
- Using Tauri's native `appWindow.onFocusChanged()` API (future enhancement — would allow counting "window focused but idle" separately)
- Tracking time outside the Orchestrator app
- Breaking down human time by activity type (terminal vs. dashboard)
- Mousemove as an activity signal (too noisy, overcounts)
