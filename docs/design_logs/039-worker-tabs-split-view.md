# 039 — Worker Tabs & Split View

**Status:** Draft
**Date:** 2026-03-27

## Problem

The current worker detail page (`/workers/:id`) is a single-worker, full-page view. Switching between workers requires navigating back to the workers list, finding the target card, and clicking through — a minimum of 2 clicks and a full page transition. For users managing 5-15 concurrent workers, this friction compounds:

1. **Slow switching** — List → detail → back → list → different detail. Each transition loses terminal scroll position and mental context.
2. **No comparison** — Users cannot watch two workers simultaneously (e.g., monitoring a pair of workers on the same task, or comparing a working worker's output with a stuck one).
3. **Lost context** — Navigating away from a worker discards the xterm.js terminal buffer. Returning requires reconnecting the WebSocket and re-scrolling.

## Goals

- **Instant switching** between recently viewed workers via persistent tabs (zero navigation, zero re-render delay).
- **Side-by-side view** of exactly 2 workers for comparison, monitoring, and debugging.
- **Zero regression** on the existing single-worker experience — tabs add capability without adding clutter.
- **Familiar interaction model** — follow the proven VS Code editor-group pattern that developers already know.

## Non-Goals

- More than 2 simultaneous panes (YAGNI — adds layout complexity for marginal benefit).
- Tabs for non-worker pages (tasks, projects, etc.) — scope this to worker detail only.
- Drag-and-drop tab reordering in V1 (nice-to-have, not required).

---

## Design

### Mental Model

Think of the worker detail area as a code editor workspace:

- **Tabs** = open files. Each tab is a worker. You open them, switch between them, close them.
- **Split view** = editor groups. You can have 1 or 2 groups side-by-side. Each group has its own tab bar and shows one worker at a time.
- **Focused pane** = the active editor group. Keyboard shortcuts and global actions apply to the focused pane.

### Architecture Overview

```
WorkerWorkspace (new component, replaces direct SessionDetailPage route)
 |
 +-- [Single Pane Mode]
 |   |
 |   +-- WorkerTabBar
 |   |     +-- Tab (worker A, active)
 |   |     +-- Tab (worker B)
 |   |     +-- Tab (worker C)
 |   |     +-- [+] button (quick-open picker)
 |   |     +-- [Split] button
 |   |
 |   +-- WorkerPane (contains SessionDetailPage internals)
 |         +-- sd-topbar (controls only, name removed — shown in tab)
 |         +-- fe-content-area (file explorer + terminal)
 |         +-- sd-footer
 |
 +-- [Split Mode]
     |
     +-- Left Pane
     |   +-- WorkerTabBar (own tabs)
     |   +-- WorkerPane
     |
     +-- Resize Handle (vertical, draggable)
     |
     +-- Right Pane
         +-- WorkerTabBar (own tabs)
         +-- WorkerPane
```

---

### Tab Bar

#### Visual Design

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │  ● worker-1   ×  │  ● worker-2   ×  │  ○ worker-3   ×  │  [+] [⫽] │
 └──────────────────────────────────────────────────────────────────────┘
      ↑ active           ↑ idle              ↑ disconnected
```

- **Height:** 36px — compact but easy to click.
- **Background:** `var(--bg)` (same as page background, recessed like the existing topbar).
- **Bottom border:** `1px solid var(--border-subtle)`, same as current `sd-topbar`.
- **Tab item:**
  - Status dot (6px circle, colored per `WORKER_STATUS_COLORS` — same palette as worker cards).
  - Worker name — `font-size: 13px`, `font-weight: 500`, truncated with ellipsis at ~140px max-width.
  - Close button (x) — `12px`, appears on hover or when tab is active. Click stops propagation and closes the tab.
- **Active tab:**
  - Background: `var(--surface)` — lifted from the `--bg` tab bar, creating a "connected" feel with the content below.
  - Bottom: 2px accent underline (`var(--accent)`), flush with the content area.
  - Text: `var(--text-primary)`.
- **Inactive tab:**
  - Background: transparent.
  - Text: `var(--text-secondary)`.
  - Hover: `var(--surface-hover)` background, text shifts to `--text-primary`.
- **Overflow:** Horizontal scroll with CSS `overflow-x: auto` and scroll-fade masks (same pattern as `.page-content`). No wrapping.
- **Right-side controls** (pinned, never scroll):
  - `[+]` button — opens a dropdown picker to add a worker tab (searchable, shows worker name + status, excludes already-open tabs).
  - `[Split]` button — toggles split view on/off. Icon: vertical split bar (`⫽`). Highlighted when split is active.

#### Tab Behavior

| Action | Result |
|---|---|
| Click worker card on `/workers` list | Opens tab (or focuses existing), navigates to detail |
| Click a tab | Switches to that worker in the current pane |
| Click `×` on a tab | Closes tab. If it was active, activates the nearest tab (right, then left). If last tab in pane, see "Closing" below |
| Middle-click a tab | Same as clicking `×` |
| `[+]` button | Opens dropdown with searchable worker list. Selecting opens a new tab |
| Right-click a tab | Context menu: Close / Close Others / Close All |
| Navigate to `/workers/:id` (deep link) | Opens tab for that worker (or focuses existing), adds to current tab set |
| Worker removed (via API) | Tab auto-closes with no animation stutter |
| Worker disconnects | Tab stays open, shows disconnected status dot — user may want to reconnect |

#### Tab Ordering

- New tabs append to the right end of the tab bar.
- Tabs maintain insertion order (no auto-sorting). This gives users spatial memory — "worker A is the third tab from the left."
- V2 enhancement: drag-and-drop reordering.

---

### Split View

#### Activation

| Method | Behavior |
|---|---|
| Click `[Split]` button in tab bar | Creates right pane. Moves the second-most-recently-active tab to the right pane. If only one tab exists, right pane opens empty with the `[+]` picker shown. |
| `Cmd+\` (keyboard shortcut) | Same as clicking `[Split]` |
| Option+click a tab | Opens that tab in a new split pane (or the other pane if split already active) |
| Context menu → "Open in Split" | Same as Option+click |

#### Layout

```
 ┌──────────────────────────────────┬──────────────────────────────────┐
 │ ● worker-1  × │ ● worker-3  ×  │ ● worker-2  × │ ○ worker-4  ×  │
 ├──────────────────────────────────┼──────────────────────────────────┤
 │  worker-1     ● working [ctrl]  │  worker-2     ● idle    [ctrl]  │
 ├──────────────────────────────────┤──────────────────────────────────┤
 │                                  │                                  │
 │         Terminal / Files         │         Terminal / Files         │
 │                                  │                                  │
 ├──────────────────────────────────┼──────────────────────────────────┤
 │  Task | Tunnels | [F] [T]       │  Task | Tunnels | [F] [T]       │
 └──────────────────────────────────┴──────────────────────────────────┘
                                    ↑
                              resize handle
```

- **Default split:** 50/50, each pane gets half the available width.
- **Resize handle:** 4px wide, `var(--border-subtle)` at rest, `var(--accent)` on hover. `cursor: col-resize`. Same interaction pattern as the existing file-explorer resize handle.
- **Minimum pane width:** 360px. If the container is < 720px, split view is unavailable (button disabled with tooltip: "Window too narrow for split view").
- **Each pane is fully independent:** own tab bar, own topbar controls, own terminal/file-explorer, own footer. They share nothing except the global AppContext data.

#### Focused Pane

- Clicking anywhere in a pane focuses it.
- The focused pane's tab bar has a subtle accent indicator: a 2px top-border line in `var(--accent)` on the active tab, vs. `var(--border-subtle)` on the unfocused pane's active tab.
- Keyboard shortcuts (tab switching, close) apply to the focused pane.
- The URL reflects the focused pane's active worker: `/workers/:id`.

#### Exiting Split View

| Action | Result |
|---|---|
| Click `[Split]` button (toggle off) | Right pane closes. Its tabs merge back into the left pane's tab bar (appended at end). The left pane keeps its current active tab. |
| `Cmd+\` | Same as clicking `[Split]` |
| Close all tabs in one pane | That pane closes, returning to single-pane mode. Remaining pane becomes full-width. |
| Closing the last tab in the left pane | Left pane is removed; right pane becomes the sole pane and shifts left. |

---

### Topbar Changes

The current topbar shows: `[Worker Name] [type tags] [status badge] [check btn] ... [control buttons] [remove]`.

With tabs, the worker name + type tags + status badge are **redundant** — the tab already shows name and status. The topbar simplifies to a **control strip**:

```
Before:  [worker-1] [rdev] [● idle] [↻] .................. [⏸] [▶] [■] [🗑]
After:   [rdev] [● idle] [↻] ................................. [⏸] [▶] [■] [🗑]
```

- **Remove the worker name** from the topbar (it's in the tab).
- **Keep:** type tag (rdev/ssh), status badge, check-status button, all control buttons, remove button.
- **Keep:** the same `sd-topbar` styling (background, padding, border).
- **Add:** A small back-arrow link (`←`) at the far left of the topbar, navigating to `/workers` list. This replaces the current `<Link to="/workers">` breadcrumb that was in the name area.

In split mode, each pane has its own topbar instance (controls are per-worker).

---

### State Management

#### New: `useWorkerTabs` Hook

```typescript
interface WorkerTab {
  workerId: string
  openedAt: number      // timestamp, for "most recently opened" fallback
  lastActiveAt: number  // timestamp, for "most recently active" ordering
}

interface PaneState {
  tabs: WorkerTab[]
  activeTabId: string | null
}

interface WorkerTabsState {
  panes: [PaneState] | [PaneState, PaneState]  // 1 or 2 panes
  focusedPane: 0 | 1
  splitRatio: number  // 0.0-1.0, default 0.5
}
```

- Stored in a React context (`WorkerTabsContext`) wrapping the worker detail routes.
- Persisted to `localStorage` key `orchestrator-worker-tabs`.
- Syncs `focusedPane`'s active tab to the URL via `useNavigate()` / `useParams()`.

#### Terminal Instance Preservation

**Critical requirement:** Switching tabs must not destroy the terminal. Each open tab maintains a live xterm.js instance and WebSocket connection.

**Approach: Hidden DOM preservation**

```tsx
// WorkerPane renders ALL tabbed workers, but only the active one is visible
{pane.tabs.map(tab => (
  <div
    key={tab.workerId}
    className="worker-pane-content"
    style={{ display: tab.workerId === pane.activeTabId ? 'flex' : 'none' }}
  >
    <WorkerDetail workerId={tab.workerId} />
  </div>
))}
```

- All terminal instances stay mounted in the DOM (display: none when inactive).
- WebSocket connections remain open — terminal output continues buffering.
- When a tab is re-activated, the terminal is instantly visible with full scroll history.
- **Resource limit:** If > 8 tabs are open, the oldest inactive tabs (by `lastActiveAt`) are unmounted and will re-initialize when activated. This prevents unbounded memory/connection growth.

#### Refactoring `SessionDetailPage`

The current `SessionDetailPage` reads `id` from `useParams()` and manages its own lifecycle. To support tabs:

1. **Extract** the core content into a `WorkerDetail` component that accepts `workerId` as a prop (not from URL params).
2. `WorkerDetail` receives `isFocused: boolean` prop — only the active tab in the focused pane receives keyboard events and terminal focus.
3. The outer `WorkerWorkspace` component handles routing, tab management, and split layout.
4. `SessionDetailPage` becomes a thin wrapper: `<WorkerWorkspace />`.

---

### URL Routing

**Route structure unchanged:** `/workers/:id` still renders the worker detail view.

**Behavior:**
- The `:id` in the URL always reflects the **focused pane's active tab**.
- Switching tabs updates the URL (push to history, enabling browser back/forward).
- Opening a split does NOT change the URL (the left pane stays focused by default).
- Clicking in the right pane updates the URL to that pane's active worker.
- Deep-linking `/workers/:id` opens/focuses that worker's tab, adding it if not already open.

**History navigation:**
- Browser Back/Forward cycles through the tab activation history (not the tab bar order).
- This matches VS Code and browser tab behavior.

---

### Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Cmd+Shift+[` | Previous tab in current pane |
| `Cmd+Shift+]` | Next tab in current pane |
| `Cmd+W` | Close active tab in focused pane |
| `Cmd+\` | Toggle split view |
| `Ctrl+1` / `Ctrl+2` | Focus left / right pane (split mode only) |
| `Cmd+Shift+T` | Reopen last closed tab (keeps a stack of recently closed, max 5) |

Note: `Cmd` = `Ctrl` on Linux/Windows. These mirror browser/VS Code conventions.

Shortcuts are registered at the `WorkerWorkspace` level and only active when a worker detail page is shown. They do not conflict with existing shortcuts (sidebar `D/P/T/W` shortcuts are single-key and only active when no input is focused).

---

### Opening Tabs from Other Pages

Tabs are opened from several entry points:

| Entry Point | Behavior |
|---|---|
| Worker card click (`/workers` list) | `navigate('/workers/:id')` — `WorkerWorkspace` picks up the ID and opens/focuses a tab |
| Task detail → "View Worker" link | Same navigation |
| Dashboard worker widget click | Same navigation |
| Notification click (worker-related) | Same navigation |
| Direct URL entry / bookmark | Same — tab opened from URL param |

The `WorkerWorkspace` component intercepts the route param on mount/update:
```
if (urlWorkerId && !isTabOpen(urlWorkerId)) {
  openTab(urlWorkerId, focusedPane)
}
activateTab(urlWorkerId, focusedPane)
```

---

### Edge Cases

#### All tabs closed
When the last tab in single-pane mode is closed, navigate to `/workers` (the list page). The tab state resets to empty.

#### Worker deleted while tab is open
The `useEffect` watching `sessions` from AppContext detects that the worker no longer exists. The tab auto-closes. A toast notification confirms: "Worker 'name' was removed."

#### Split mode with brain panel open
The brain panel is an app-level right sidebar, outside the main content area. Split view operates within `app-main`. Both can coexist — the split panes share the horizontal space left after the brain panel takes its width. If the remaining width < 720px, split is auto-disabled with a toast: "Split view closed — not enough space."

#### Window resize
If the window shrinks below 720px available width while in split mode, split auto-collapses (right pane tabs merge left). A non-intrusive toast explains why. When the window re-expands, split does not auto-restore (user must re-enable manually to avoid surprise).

#### Duplicate worker in both panes
Allowed. A user may want the same worker open in both panes (e.g., terminal in left, file explorer in right with different files open). Each pane maintains independent file-explorer and editor-tab state. They share the same terminal WebSocket (fan-out via `PtyStreamPool`), so both terminals show identical output.

#### Tab for a worker that's currently connecting/reconnecting
Tab shows the reconnect overlay within its pane — same as current behavior, just scoped to the pane.

---

### Visual Polish

#### Tab transitions
- Tab activation: instant (no fade/slide — speed matters here).
- Tab close: the tab shrinks to 0 width over 150ms, siblings slide left. The `gap` transition creates a smooth collapse.
- New tab: appears at full width immediately (no grow animation — avoid delay).
- Split open/close: the panes animate to their target widths over 200ms with `cubic-bezier(0.4, 0, 0.2, 1)`.

#### Status dot animation
- Reuse the existing pulsing animation for "working" status from `WorkerCard`.
- The status dot in the tab pulses when the worker is actively working — a persistent, ambient indicator visible without clicking the tab.

#### Split resize handle
Follow the established file-explorer resize pattern:
- At rest: `1px` wide, `var(--border-subtle)`.
- On hover: widens to `4px`, color shifts to `var(--accent)`.
- During drag: stays `4px` accent, all transitions disabled in content areas (`.resizing *` rule).

#### Dark/light mode
All new elements use CSS variables exclusively. The tab bar uses `--bg` / `--surface` / `--surface-hover` for backgrounds, which are already defined in both themes. No new color values needed.

#### Focused pane indicator
In split mode, the focused pane gets a subtle `2px` top accent bar on its tab bar area (using `box-shadow: inset 0 2px 0 0 var(--accent)`). The unfocused pane has no bar. This is minimal but sufficient — the user always knows which pane will receive keyboard input.

---

### Accessibility

- All tabs are keyboard-navigable (`tabindex`, arrow keys within the tab bar).
- `role="tablist"` on the tab bar, `role="tab"` on each tab, `role="tabpanel"` on the content area.
- `aria-selected="true"` on the active tab.
- `aria-label` on the split toggle button and `[+]` button.
- Focus management: when a tab is closed, focus moves to the newly active tab. When split is toggled, focus stays in the originating pane.
- The close button on each tab has `aria-label="Close worker-name"`.

---

### Implementation Plan

#### Phase 1: Tab Bar (Single Pane)
1. Create `WorkerTabsContext` and `useWorkerTabs` hook with localStorage persistence.
2. Create `WorkerTabBar` component.
3. Create `WorkerWorkspace` component that wraps `WorkerDetail`.
4. Refactor `SessionDetailPage` → extract `WorkerDetail` (accepts `workerId` prop).
5. Implement hidden-DOM preservation for terminal instances.
6. Update route in `App.tsx`: `/workers/:id` → `<WorkerWorkspace />`.
7. Wire up tab opening from workers list, dashboard, task detail links.
8. Add keyboard shortcuts (`Cmd+Shift+[/]`, `Cmd+W`).
9. Add `[+]` quick-picker dropdown.
10. Handle edge cases (worker deletion, all-tabs-closed navigation).

#### Phase 2: Split View
1. Add split state to `WorkerTabsState` (2-pane support).
2. Implement split layout with resize handle in `WorkerWorkspace`.
3. Add focused-pane tracking and visual indicator.
4. Implement split activation methods (button, keyboard, Option+click).
5. Handle merge-on-close and minimum-width constraints.
6. Add `Ctrl+1/2` pane focus shortcuts.
7. Test with brain panel open, narrow windows, duplicate workers.

#### Phase 3: Polish
1. Tab close animation (shrink transition).
2. Split open/close animation.
3. Reopen-closed-tab stack (`Cmd+Shift+T`).
4. Right-click context menu (Close / Close Others / Close All).
5. Tab overflow scroll with fade masks.
6. Responsive: disable split on narrow viewports, adjust tab max-width.

---

### File Changes Summary

| File | Change |
|---|---|
| `App.tsx` | Route `/workers/:id` renders `WorkerWorkspace` instead of `SessionDetailPage` |
| `pages/SessionDetailPage.tsx` | Rename to `WorkerDetail.tsx`, accept `workerId` prop instead of `useParams()` |
| **New** `components/workers/WorkerWorkspace.tsx` | Orchestrates tabs, split, panes |
| **New** `components/workers/WorkerTabBar.tsx` | Tab bar UI |
| **New** `components/workers/WorkerTabBar.css` | Tab bar styles |
| **New** `components/workers/WorkerWorkspace.css` | Split layout, pane styles |
| **New** `context/WorkerTabsContext.tsx` | Tab state management + persistence |
| **New** `hooks/useWorkerTabs.ts` | Hook for tab operations (open, close, switch, split) |
| `components/workers/WorkerCard.tsx` | Click handler now calls `navigate()` (unchanged behavior, tabs picked up by workspace) |
| `pages/SessionDetailPage.css` | Minor adjustments — remove name from topbar, adjust margins |

---

### Open Questions

1. **Tab limit before performance degrades?** — Need to benchmark xterm.js memory per hidden instance. Initial cap at 8 live instances; oldest get unmounted. May need tuning.
2. **Should tab order be draggable in V1?** — Current decision: no. Adds interaction complexity and requires drag-and-drop library or custom implementation. Revisit after user feedback.
3. **Persist tabs across app restarts?** — Yes, via localStorage. But should we auto-close tabs for workers that no longer exist on reload? Proposed: yes, silently prune stale tabs on mount.
