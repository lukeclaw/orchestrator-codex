# 039 — Worker Tabs & Split View

**Status:** Draft
**Date:** 2026-03-27

## Problem

The current worker detail page (`/workers/:id`) is a single-worker, full-page view. Switching between workers requires navigating back to the workers list, finding the target card, and clicking through — a minimum of 2 clicks and a full page transition. For users managing 5-15 concurrent workers, this friction compounds:

1. **Slow switching** — List -> detail -> back -> list -> different detail. Each transition loses terminal scroll position and mental context.
2. **No comparison** — Users cannot watch two workers simultaneously (e.g., monitoring a pair of workers on the same task, or comparing a working worker's output with a stuck one).
3. **Lost context** — Navigating away from a worker discards the xterm.js terminal buffer. Returning requires reconnecting the WebSocket and re-scrolling.

## Goals

- **Instant switching** between recently viewed workers via persistent tabs (zero navigation, zero re-render delay).
- **Side-by-side view** of exactly 2 workers for comparison, monitoring, and debugging.
- **Zero regression** on the existing single-worker experience — tabs add capability without adding clutter.
- **Familiar interaction model** — inspired by Chrome's split-tab UX: one shared tab bar, two active selections.

## Non-Goals

- More than 2 simultaneous panes (YAGNI — adds layout complexity for marginal benefit).
- Tabs for non-worker pages (tasks, projects, etc.) — scope this to worker detail only.
- Drag-and-drop tab reordering in V1 (nice-to-have, not required).

---

## Design

### Mental Model

Think of the worker detail area as a Chrome window with split-tab support:

- **Tabs** = browser tabs. Each tab is a worker. You open them, switch between them, close them. All tabs live in one shared tab bar.
- **Preview tab** = VS Code's preview file. When you single-click a worker from the list, it opens in a transient "preview" slot that gets replaced the next time you click a different worker. This prevents casual browsing from cluttering the tab bar. The preview tab auto-promotes to a permanent tab when you engage with the worker (type in terminal, open file explorer, etc.).
- **Split view** = Chrome split-screen. The content area divides into two panes. Each pane has one active tab. The tab bar shows two highlighted tabs simultaneously — one per pane, color-coded.
- **Focused pane** = the pane that receives tab clicks and keyboard shortcuts. Click in a pane to focus it, then click any tab to show that worker there.

### Architecture Overview

```
WorkerWorkspace (new component, replaces direct SessionDetailPage route)
 |
 +-- WorkerTabBar (ALWAYS one shared bar — never duplicated)
 |     +-- Tab (worker A) — may have left-pane or right-pane active indicator
 |     +-- Tab (worker B) — may have left-pane or right-pane active indicator
 |     +-- Tab (worker C)
 |     +-- [+] button (quick-open picker)
 |     +-- [Split] button
 |
 +-- Pane Container (flex row)
     |
     +-- [Single Pane Mode]
     |   +-- WorkerPane
     |         +-- sd-topbar (controls)
     |         +-- fe-content-area (file explorer + terminal)
     |         +-- sd-footer
     |
     +-- [Split Mode]
         +-- Left WorkerPane
         +-- Resize Handle (vertical, draggable)
         +-- Right WorkerPane
```

Key difference from VS Code model: there is always exactly **one tab bar**, even in split mode. The tab bar uses color-coded underlines to show which tab is active in which pane.

---

### Tab Bar

#### Visual Design — Single Pane Mode

```
[● worker-1] [● worker-2] [○ worker-3] [● worker-4]  [+] [⫽]
  ══════════
  active (accent underline)
```

#### Visual Design — Split Mode (two active tabs, color-coded)

```
[● worker-1] [● worker-2] [○ worker-3] [● worker-4] [● worker-5] [● worker-6]  [+] [⫽]
  ══════════                              ──────────
  Left pane                               Right pane
  (accent/blue underline)                 (purple underline)
```

Two tabs are highlighted simultaneously. The colors match the focused-pane indicator on the content area below, creating a clear visual link between "this tab" and "this pane."

#### Styling

- **Height:** 36px — compact but easy to click.
- **Background:** `var(--bg)` (same as page background, recessed like the existing topbar).
- **Bottom border:** `1px solid var(--border-subtle)`, same as current `sd-topbar`.
- **Tab item:**
  - Status dot (6px circle, colored per `WORKER_STATUS_COLORS` — same palette as worker cards).
  - Worker name — `font-size: 13px`, `font-weight: 500`, truncated with ellipsis at ~140px max-width.
  - Close button (x) — `12px`, appears on hover or when tab is active. Click stops propagation and closes the tab.
- **Preview tab** (transient):
  - Same layout as pinned tabs, but worker name is rendered in *italic* (`font-style: italic`) — a subtle "I'm temporary" signal, matching the VS Code convention.
  - Always positioned as the **rightmost tab** in the bar, after all pinned tabs.
  - Only one preview tab exists at a time.
- **Pinned tab** (persistent):
  - Normal (non-italic) worker name. Stays until explicitly closed via `x`.
- **Active tab (single pane mode):**
  - Background: `var(--surface)` — lifted from the `--bg` tab bar, creating a "connected" feel with the content below.
  - Bottom: 2px accent underline (`var(--accent)`).
  - Text: `var(--text-primary)`.
- **Active tab (split mode — left pane):**
  - Same as single-pane active, with `var(--accent)` (blue) 2px underline.
- **Active tab (split mode — right pane):**
  - Background: `var(--surface)`.
  - Bottom: 2px underline in `var(--purple)`.
  - Text: `var(--text-primary)`.
- **Same worker in both panes:**
  - Split underline — left half `var(--accent)`, right half `var(--purple)`. A clear visual cue that this single tab is driving both panes.
- **Inactive tab:**
  - Background: transparent.
  - Text: `var(--text-secondary)`.
  - Hover: `var(--surface-hover)` background, text shifts to `--text-primary`.
- **Overflow:** Horizontal scroll with CSS `overflow-x: auto` and scroll-fade masks (same pattern as `.page-content`). No wrapping.
- **Right-side controls** (pinned, never scroll):
  - `[+]` button — opens a dropdown picker to add a worker tab (searchable, shows worker name + status, excludes already-open tabs). Workers opened via `[+]` are pinned immediately (explicit intent).
  - `[Split]` button — toggles split view on/off. Icon: vertical split bar. Highlighted with `var(--accent)` when split is active.

#### Preview vs. Pinned Tabs

The tab bar uses a two-tier system (inspired by VS Code) to prevent casual browsing from cluttering the tab bar:

**Preview tab** (transient) — italic name, rightmost position:
- Created when the user single-clicks a worker card on the `/workers` list page, a "View Worker" link from task detail, a dashboard widget, or any other navigation link.
- Only **one preview tab** exists at a time. Clicking a different worker from the list **replaces** the current preview tab instead of adding a new one.
- If the target worker already has a pinned tab, the pinned tab is focused instead — no preview tab created.

**Pinned tab** (persistent) — normal name, stable position:
- Created when the user explicitly intends to keep a worker open (double-click, `[+]` picker, engagement).
- Stays in the tab bar until explicitly closed via `x`.

**Auto-pin triggers** — a preview tab promotes to pinned when:

| Action | Rationale |
|---|---|
| Double-click the worker card on the list | Explicit "I want to keep this open" |
| Double-click the preview tab itself | Same |
| Send any input to the terminal (keystrokes, paste) | Active interaction = intent to stay |
| Open file explorer or toggle any panel | Customizing the view = intent to stay |
| Assign a task to the worker | Deliberate action on this worker |
| Open in split view (Option+click or split button) | Comparison implies monitoring intent |
| Right-click tab -> "Keep Open" | Explicit pin |
| Open via `[+]` picker | Explicit search = deliberate intent (pinned immediately) |

When a preview tab is pinned, it stays in its current position (rightmost among pinned tabs). A new preview slot opens at the end if the user browses again.

#### Tab Click Behavior

**Single pane mode:** Clicking a tab activates it. Simple.

**Split mode — the focused-pane rule:** Clicking a tab activates it in whichever pane is currently focused. The user controls which pane is focused by clicking anywhere inside that pane's content area.

This is the core interaction: **focus a pane (click in it), then click tabs to control what it shows.**

| Action | Single Pane | Split Mode |
|---|---|---|
| Click a tab | Activates that worker | Activates that worker in the **focused** pane |
| Click `x` on a tab | Closes tab; nearest tab activates | Closes tab; if it was active in a pane, nearest tab activates in that pane |
| Middle-click a tab | Same as `x` | Same as `x` |
| Option+click a tab | No effect (single pane) | Activates that worker in the **other** (unfocused) pane; auto-pins if preview |
| Right-click a tab | Context menu: Close / Close Others / Close All / Keep Open | Same + "Open in Left Pane" / "Open in Right Pane" |
| Click `[+]` button | Opens picker; selected worker opens as **pinned** tab and activates | Opens picker; selected worker opens as **pinned** tab and activates in focused pane |
| Single-click worker card on `/workers` list | Opens as **preview** tab (replaces existing preview), or focuses existing pinned tab | Same, activates in focused pane |
| Double-click worker card on `/workers` list | Opens as **pinned** tab, or focuses existing pinned tab | Same, activates in focused pane |
| Navigate to `/workers/:id` (deep link, other page link) | Opens as **preview** tab (or focuses existing pinned tab) | Same, activates in focused pane |
| Worker removed (via API) | Tab auto-closes (preview or pinned) | Tab auto-closes; if active in a pane, nearest tab takes over |
| Worker disconnects | Tab stays, shows disconnected dot | Same — user may want to reconnect |

#### Tab Ordering

- Pinned tabs maintain insertion order. This gives users spatial memory — "worker A is the third tab from the left."
- The preview tab (if any) is always the **rightmost** tab, after all pinned tabs.
- When a preview tab is pinned, it stays at the right end of the pinned tabs (its current position).
- Tabs do NOT group by pane. A tab's position is stable regardless of which pane it's active in. The colored underlines are the only pane indicators.
- V2 enhancement: drag-and-drop reordering.

---

### Split View

#### Activation

| Method | Behavior |
|---|---|
| Click `[Split]` button | Creates right pane with the second-most-recently-active tab. If only one tab exists, right pane opens with the `[+]` picker auto-shown. |
| `Cmd+\` (keyboard shortcut) | Same as clicking `[Split]` |
| Option+click a tab | If not split: enters split mode with current tab in left pane and Option-clicked tab in right pane. If already split: opens that tab in the other pane. |
| Right-click tab -> "Open in Right/Left Pane" | Same as Option+click, targeting the specified pane. |

#### Layout

```
                     One shared tab bar
┌──────────────────────────────────────────────────────────────────────┐
│ [● w1] [● w2] [○ w3] [● w4] [● w5] [● w6]  [+] [⫽]               │
│   ════                  ────                                         │
│   Left (accent)         Right (purple)                               │
├──────────────────────────────────┬───────────────────────────────────┤
│  [rdev] ● working  [↻] [⏸][■]  │  [ssh] ● idle  [↻]  [⏸][▶][■]   │
├──────────────────────────────────┤───────────────────────────────────┤
│                                  │                                   │
│       Terminal / Files           │       Terminal / Files             │
│                                  │                                   │
├──────────────────────────────────┼───────────────────────────────────┤
│  Task | Tunnels | [F] [T] [B]   │  Task | Tunnels | [F] [T] [B]    │
└──────────────────────────────────┴───────────────────────────────────┘
      FOCUSED (accent top glow)          unfocused
```

- **Default split:** 50/50, each pane gets half the available width.
- **Resize handle:** 4px wide, `var(--border-subtle)` at rest, `var(--accent)` on hover. `cursor: col-resize`. Same interaction pattern as the existing file-explorer resize handle.
- **Minimum pane width:** 360px. If the container is < 720px, split view is unavailable (button disabled with tooltip: "Window too narrow for split view").
- **Each pane is fully independent:** own topbar controls, own terminal, own file-explorer, own footer. They share the tab bar and global AppContext data.

#### Focused Pane

- Clicking anywhere inside a pane's content area focuses it.
- The focused pane gets a subtle accent indicator: `box-shadow: inset 0 2px 0 0 var(--accent)` on its topbar. The unfocused pane has no indicator.
- The focused pane's color matches its tab underline color:
  - If left pane is focused: left pane topbar has accent glow, and the left-active tab has the accent (blue) underline.
  - If right pane is focused: right pane topbar has purple glow, and the right-active tab has the purple underline.
- Keyboard shortcuts (tab switching, close) apply to the focused pane.
- The URL reflects the focused pane's active worker: `/workers/:id`.

#### User Story: 16 Workers, 2 Panes

Alice manages 16 workers. Here's her workflow:

**1. Morning scan — preview tab keeps it clean**

Alice opens the workers list, sees 16 cards. She clicks `frontend-1` to check on it:
```
[● frontend-1]  [+] [⫽]
  ══════════════
  (italic — preview tab)
```
Looks fine. She goes back to the list, clicks `backend-3`:
```
[● backend-3]  [+] [⫽]
  ════════════
  (italic — preview tab, REPLACED frontend-1)
```
She scans through 5 more workers this way. The tab bar only ever shows **one** preview tab — no accumulation. She's just browsing.

**2. Found something — auto-pin via engagement**

She clicks `ml-train-2` from the list (preview tab). She sees something odd in the terminal and starts typing a command. The preview tab auto-pins:
```
[● ml-train-2]  [+] [⫽]
  ═════════════
  (normal text — now pinned, she typed in the terminal)
```
She goes back to the list, clicks `data-sync` (new preview tab):
```
[● ml-train-2] [● data-sync]  [+] [⫽]
                  ════════════
  pinned          (italic — preview)
```
Clicking another worker from the list replaces `data-sync` (preview), not `ml-train-2` (pinned).

**3. Building a working set**

Over the next hour, she pins 4 workers through natural interaction (typing, opening file explorer, assigning tasks). She also double-clicks `frontend-4` from the list to pin it explicitly:
```
[● ml-train-2] [● frontend-1] [● backend-3] [● api-7] [● frontend-4] [○ data-sync]  [+] [⫽]
                                                                          ════════════
  pinned         pinned          pinned        pinned    pinned           (italic — preview)
```
5 pinned tabs + 1 preview. Clean and intentional.

**4. Quick re-check — preview reuses the slot**

Alice wants to peek at `infra-9`. She clicks it from the list. It replaces `data-sync` in the preview slot:
```
[● ml-train-2] [● frontend-1] [● backend-3] [● api-7] [● frontend-4] [● infra-9]  [+] [⫽]
                                                                          ══════════
  pinned tabs unchanged                                                   (italic — preview)
```
She glances at it, looks fine. Clicks `worker-12` from the list — preview replaces again. Zero tab accumulation from browsing.

**5. Clicking a worker that's already pinned**

Alice clicks `backend-3` from the workers list. Since `backend-3` already has a pinned tab, it simply **focuses the existing tab**. No new tab created, no preview tab involved:
```
[● ml-train-2] [● frontend-1] [● backend-3] [● api-7] [● frontend-4] [● worker-12]  [+] [⫽]
                                  ═════════════
                                  focused (existing pinned tab)           (italic — preview, unchanged)
```

**6. Entering split mode**

Alice wants to compare `backend-3` (currently active) with `ml-train-2`. She Option+clicks the `ml-train-2` tab.

The screen splits. The tab bar stays as **one row** with **two underlined tabs**:
```
[● ml-train-2] [● frontend-1] [● backend-3] [● api-7] [● frontend-4] [● worker-12]  [+] [⫽]
  ────────────                    ═════════════
  Right (purple)                  Left (accent)                          (italic — preview, still there)
```

**7. Switching workers within a pane**

Alice wants to check `api-7` in the right pane. She:
1. Clicks anywhere in the right pane content area (focuses it).
2. Clicks the `api-7` tab.
3. The right pane now shows `api-7`. Underlines update:
```
... [● backend-3] [● api-7] [● frontend-4] ...
       ═══════════   ────────
       Left          Right
```

**8. Opening new workers while split**

Alice clicks `[+]`, selects `gpu-worker-5`. A new **pinned** tab appears (opened via picker = explicit intent) and activates in the focused pane:
```
... [● frontend-4] [● gpu-worker-5] [● worker-12]  [+] [⫽]
                      ════════════════
                      Right (focused)  (italic — preview)
```

**9. Quick cross-pane action with Option+click**

Alice Option+clicks `frontend-1` — it opens in the other (left) pane without changing focus:
```
[● ml-train-2] [● frontend-1] [● backend-3] [● api-7] [● frontend-4] [● gpu-worker-5] [● worker-12]  [+] [⫽]
                  ════════════                            ────────────────
                  Left                                    Right (still focused)             (italic — preview)
```

**10. Exiting split**

Alice clicks `[⫽]` again. The right pane closes. Single pane returns with the left pane's active tab:
```
[● ml-train-2] [● frontend-1] [● backend-3] [● api-7] [● frontend-4] [● gpu-worker-5] [● worker-12]  [+] [⫽]
                  ═════════════
```
All tabs preserved. Nothing lost.

#### Exiting Split View

| Action | Result |
|---|---|
| Click `[Split]` button (toggle off) | Right pane closes. Single pane shows the left pane's active tab. All tabs stay in the bar. |
| `Cmd+\` | Same as clicking `[Split]` |
| Close the only active tab in a pane | That pane closes; single-pane mode resumes with the remaining pane's active tab. |

---

### Topbar Changes

The current topbar shows: `[Worker Name] [type tags] [status badge] [check btn] ... [control buttons] [remove]`.

With tabs, the worker name is shown in the tab. The topbar simplifies to a **control strip**:

```
Before:  [worker-1] [rdev] [● idle] [↻] .................. [⏸] [▶] [■] [remove]
After:   [← back] [rdev] [● idle] [↻] .................... [⏸] [▶] [■] [remove]
```

- **Remove the worker name** from the topbar (it's in the tab).
- **Add:** A small back-arrow link at the far left, navigating to `/workers` list.
- **Keep:** type tag (rdev/ssh), status badge, check-status button, all control buttons, remove button.
- **Keep:** the same `sd-topbar` styling (background, padding, border).

In split mode, each pane has its own topbar instance (controls are per-worker).

---

### State Management

#### New: `useWorkerTabs` Hook

```typescript
interface WorkerTab {
  workerId: string
  isPreview: boolean    // true = transient preview tab, false = pinned
  openedAt: number      // timestamp, for insertion order tiebreaking
  lastActiveAt: number  // timestamp, for "most recently active" ordering
}

interface WorkerTabsState {
  tabs: WorkerTab[]            // shared flat list, always one array
  leftActiveId: string | null  // active tab for left pane
  rightActiveId: string | null // active tab for right pane (null if not split)
  isSplit: boolean
  focusedPane: 'left' | 'right'
  splitRatio: number           // 0.0-1.0, default 0.5
  recentlyClosed: string[]     // stack of recently closed worker IDs (max 5)
}
```

Key operations:

```typescript
// Open a worker — handles preview/pin logic
openTab(workerId: string, pin: boolean = false): void
// If worker already has a pinned tab → focus it (no-op on tab list)
// If pin=true → add as pinned tab (or pin existing preview)
// If pin=false → replace existing preview tab with this worker

// Promote preview to pinned
pinTab(workerId: string): void

// Close a tab
closeTab(workerId: string): void
```

- Stored in a React context (`WorkerTabsContext`) wrapping the worker detail routes.
- Persisted to `localStorage` key `orchestrator-worker-tabs`.
- Syncs focused pane's active tab to the URL via `useNavigate()` / `useParams()`.
- On mount, prunes tabs for workers that no longer exist (stale tab cleanup).

Note the simplification vs. per-pane tab arrays: tabs are a single flat list with two "cursor" pointers (`leftActiveId`, `rightActiveId`). The `isPreview` flag controls transient vs. persistent behavior.

#### Terminal Instance Preservation

**Critical requirement:** Switching tabs must not destroy the terminal. Each open tab maintains a live xterm.js instance and WebSocket connection.

**Approach: Hidden DOM preservation**

```tsx
// WorkerWorkspace renders ALL tabbed workers, but only the visible ones are shown
// in their respective panes
{tabs.map(tab => (
  <div
    key={tab.workerId}
    className="worker-pane-content"
    style={{
      display: tab.workerId === activePaneId ? 'flex' : 'none'
    }}
  >
    <WorkerDetail workerId={tab.workerId} isFocused={...} />
  </div>
))}
```

- All terminal instances stay mounted in the DOM (`display: none` when inactive).
- WebSocket connections remain open — terminal output continues buffering.
- When a tab is re-activated, the terminal is instantly visible with full scroll history.
- **Resource limit:** If > 8 tabs are open, the oldest inactive tabs (by `lastActiveAt`) are unmounted and will re-initialize when activated. This prevents unbounded memory/connection growth.

In split mode, two workers are visible simultaneously. Each pane has its own set of hidden-DOM instances. A worker shown in both panes at once renders two independent `WorkerDetail` instances (separate xterm.js, shared WebSocket via `PtyStreamPool` fan-out).

#### Refactoring `SessionDetailPage`

The current `SessionDetailPage` reads `id` from `useParams()` and manages its own lifecycle. To support tabs:

1. **Extract** the core content into a `WorkerDetail` component that accepts `workerId` as a prop.
2. `WorkerDetail` receives `isFocused: boolean` prop — only the active tab in the focused pane receives keyboard events and terminal focus.
3. The outer `WorkerWorkspace` component handles routing, tab management, and split layout.
4. `SessionDetailPage` becomes a thin wrapper: `<WorkerWorkspace />`.

---

### URL Routing

**Route structure unchanged:** `/workers/:id` still renders the worker detail view.

**Behavior:**
- The `:id` in the URL always reflects the **focused pane's active tab**.
- Switching tabs updates the URL (push to history, enabling browser back/forward).
- Opening/closing split does NOT change the URL (the focused pane stays the same).
- Clicking in the other pane focuses it and updates the URL to that pane's active worker.
- Deep-linking `/workers/:id` opens/focuses that worker's tab, adding it if not already open.

**History navigation:**
- Browser Back/Forward cycles through the tab activation history (not the tab bar order).
- This matches browser tab behavior.

---

### Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Cmd+Shift+[` | Previous tab (by tab bar order, wraps around) |
| `Cmd+Shift+]` | Next tab (by tab bar order, wraps around) |
| `Cmd+W` | Close active tab in focused pane |
| `Cmd+\` | Toggle split view |
| `Ctrl+1` / `Ctrl+2` | Focus left / right pane (split mode only) |
| `Cmd+Shift+T` | Reopen last closed tab (stack of recently closed, max 5) |

Note: `Cmd` = `Ctrl` on Linux/Windows. These mirror browser/VS Code conventions.

Shortcuts are registered at the `WorkerWorkspace` level and only active when a worker detail page is shown. They do not conflict with existing shortcuts (sidebar `D/P/T/W` shortcuts are single-key and only active when no input is focused).

---

### Opening Tabs from Other Pages

Navigation from any page to `/workers/:id` is the universal entry point. The `WorkerWorkspace` decides whether to create a preview tab, focus an existing pinned tab, or pin immediately based on the source:

| Entry Point | Tab Type | Behavior |
|---|---|---|
| Single-click worker card (`/workers` list) | **Preview** | Opens as preview (replaces existing preview). If worker already pinned, focuses existing tab instead. |
| Double-click worker card (`/workers` list) | **Pinned** | Opens as pinned tab (or focuses existing pinned tab). |
| Task detail -> "View Worker" link | **Preview** | Same as single-click from list. |
| Dashboard worker widget click | **Preview** | Same as single-click from list. |
| Notification click (worker-related) | **Preview** | Same as single-click from list. |
| `[+]` picker in tab bar | **Pinned** | Explicit search = deliberate intent. Pinned immediately. |
| Direct URL entry / bookmark | **Preview** | Treats as casual navigation. If already pinned, focuses existing tab. |

The `WorkerWorkspace` component intercepts the route param on mount/update:
```
const existingPinned = tabs.find(t => t.workerId === urlWorkerId && !t.isPreview)
if (existingPinned) {
  activateTab(urlWorkerId, focusedPane)
} else {
  openTab(urlWorkerId, pin: isPinnedSource)  // replaces preview or creates pinned
  activateTab(urlWorkerId, focusedPane)
}
```

**Tab persistence across pages:** Tab state is preserved in `WorkerTabsContext` + localStorage when navigating away from the worker detail area (to workers list, tasks, dashboard, etc.). The tab bar is only visible on worker detail pages (`/workers/:id`), but the state survives navigation. Returning to any worker detail page restores the full tab bar.

---

### Edge Cases

#### All tabs closed
When the last tab (preview or pinned) is closed, navigate to `/workers` (the list page). The tab state resets to empty.

#### Preview tab is the only tab and gets replaced
If the only tab is a preview and the user navigates to a different worker from the list, the preview is replaced in-place. The tab bar always shows at least one tab while on a worker detail page.

#### Worker deleted while tab is open
The `useEffect` watching `sessions` from AppContext detects that the worker no longer exists. The tab auto-closes (preview or pinned). A toast notification confirms: "Worker 'name' was removed."

#### Split mode with brain panel open
The brain panel is an app-level right sidebar, outside the main content area. Split view operates within `app-main`. Both can coexist — the split panes share the horizontal space left after the brain panel takes its width. If the remaining width < 720px, split is auto-disabled with a toast: "Split view closed -- not enough space."

#### Window resize
If the window shrinks below 720px available width while in split mode, split auto-collapses. A non-intrusive toast explains why. When the window re-expands, split does not auto-restore (user must re-enable manually to avoid surprise).

#### Duplicate worker in both panes
Allowed. A user may want the same worker open in both panes (e.g., terminal in left, file explorer in right with different files open). The tab shows a split underline — left half accent, right half purple. Each pane maintains independent file-explorer and editor-tab state. They share the same terminal WebSocket (fan-out via `PtyStreamPool`), so both terminals show identical output.

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
In split mode, the focused pane's topbar gets a colored top glow matching its tab underline:
- Left pane focused: `box-shadow: inset 0 2px 0 0 var(--accent)`
- Right pane focused: `box-shadow: inset 0 2px 0 0 var(--purple)`
This creates a visual link: tab underline color = pane border color = "this is where my clicks go."

---

### Accessibility

- All tabs are keyboard-navigable (`tabindex`, arrow keys within the tab bar).
- `role="tablist"` on the tab bar, `role="tab"` on each tab, `role="tabpanel"` on the content area.
- `aria-selected="true"` on the active tab(s).
- `aria-label` on the split toggle button and `[+]` button.
- Focus management: when a tab is closed, focus moves to the newly active tab. When split is toggled, focus stays in the originating pane.
- The close button on each tab has `aria-label="Close worker-name"`.
- In split mode, panes have `aria-label="Left pane"` / `"Right pane"` for screen readers.

---

### Implementation Plan

#### Phase 1: Tab Bar (Single Pane)
1. Create `WorkerTabsContext` and `useWorkerTabs` hook with localStorage persistence.
2. Create `WorkerTabBar` component (with preview tab italic styling).
3. Create `WorkerWorkspace` component that wraps `WorkerDetail`.
4. Refactor `SessionDetailPage` -> extract `WorkerDetail` (accepts `workerId` prop).
5. Implement hidden-DOM preservation for terminal instances.
6. Implement preview/pinned tab logic: single-click = preview, double-click = pin, auto-pin on engagement.
7. Update route in `App.tsx`: `/workers/:id` -> `<WorkerWorkspace />`.
8. Wire up tab opening from workers list (single-click = preview, double-click = pin), dashboard, task detail links.
9. Add keyboard shortcuts (`Cmd+Shift+[/]`, `Cmd+W`).
10. Add `[+]` quick-picker dropdown (opens as pinned).
11. Handle edge cases (worker deletion, all-tabs-closed navigation, stale tab pruning on mount).

#### Phase 2: Split View
1. Add split state to `WorkerTabsState` (`isSplit`, `rightActiveId`, `focusedPane`).
2. Implement split layout with resize handle in `WorkerWorkspace`.
3. Add focused-pane tracking and color-coded visual indicators (accent vs. purple).
4. Implement split activation methods (button, keyboard, Option+click, context menu).
5. Handle exit-split scenarios (toggle off, close last tab in a pane).
6. Add `Ctrl+1/2` pane focus shortcuts.
7. Handle minimum-width constraints and auto-collapse on resize.
8. Test with brain panel open, narrow windows, duplicate workers in both panes.

#### Phase 3: Polish
1. Tab close animation (shrink transition).
2. Split open/close animation.
3. Reopen-closed-tab stack (`Cmd+Shift+T`).
4. Right-click context menu (Close / Close Others / Close All / Open in Left|Right Pane).
5. Tab overflow scroll with fade masks.
6. Responsive: disable split on narrow viewports, adjust tab max-width.

---

### File Changes Summary

| File | Change |
|---|---|
| `App.tsx` | Route `/workers/:id` renders `WorkerWorkspace` instead of `SessionDetailPage` |
| `pages/SessionDetailPage.tsx` | Rename to `WorkerDetail.tsx`, accept `workerId` prop instead of `useParams()` |
| **New** `components/workers/WorkerWorkspace.tsx` | Orchestrates tab bar, split layout, pane rendering |
| **New** `components/workers/WorkerTabBar.tsx` | Shared tab bar UI (single row, color-coded underlines) |
| **New** `components/workers/WorkerTabBar.css` | Tab bar styles |
| **New** `components/workers/WorkerWorkspace.css` | Split layout, pane styles, resize handle |
| **New** `context/WorkerTabsContext.tsx` | Tab state management + localStorage persistence |
| **New** `hooks/useWorkerTabs.ts` | Hook for tab operations (open, close, switch, split, focus) |
| `components/workers/WorkerCard.tsx` | Click handler uses `navigate()` (unchanged behavior, tabs picked up by workspace) |
| `pages/SessionDetailPage.css` | Remove name from topbar, add back-arrow, adjust margins |

---

### Open Questions

1. **Tab limit before performance degrades?** — Need to benchmark xterm.js memory per hidden instance. Initial cap at 8 live instances; oldest get unmounted. May need tuning.
2. **Should tab order be draggable in V1?** — Current decision: no. Adds interaction complexity and requires drag-and-drop library or custom implementation. Revisit after user feedback.
3. **Persist tabs across app restarts?** — Yes, via localStorage. But should we auto-close tabs for workers that no longer exist on reload? Proposed: yes, silently prune stale tabs on mount.
