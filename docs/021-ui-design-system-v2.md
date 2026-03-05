# 021 — UI Design System v2: Borderless, Shade-Based

## Overview

A redesign of the UI foundation moving from a border-heavy style to a cleaner, shade-based approach. The goal is to reduce visual noise by eliminating unnecessary lines and borders, using background contrast and spacing to create hierarchy instead.

## Design Principles

1. **Borderless cards** — Cards have no `border`. The contrast between `--bg` (#0d1117) and `--surface` (#161b22) provides sufficient visual separation.

2. **Background shading over lines** — Replace `<hr>` dividers and `border-bottom` separators with spacing and subtle background differences. Use recessed backgrounds (`--bg` inside `--surface` cards) for content areas.

3. **Quiet inputs, loud on focus** — Form fields have no visible border at rest (just a background tint). A subtle border appears on hover, and an accent ring appears on focus.

4. **Borderless badges** — Status and priority badges use background tint + text color only, no border outlines.

5. **Pill-shaped tags, rectangular buttons** — Tags/badges use `border-radius: 999px` (full pill). Buttons use `var(--radius)` (8px). This visual distinction makes it immediately clear which elements are status indicators vs interactive controls.

6. **Consistent elevation hierarchy** — `--bg` (darkest) -> `--surface` -> `--surface-hover` -> `--surface-raised` -> `--surface-overlay` (lightest). Shadows are reserved for truly floating elements (modals, dropdowns).

7. **Semantic grouping through spacing** — Related fields are grouped via wrapper elements with consistent internal gaps rather than divider lines.

## Design Tokens

### New Variables Added

| Token | Value | Purpose |
|---|---|---|
| `--surface-overlay` | `#2d333b` | Floating elements (dropdowns, tooltips) |
| `--border-muted` | `rgba(48, 54, 61, 0.4)` | Near-invisible borders for float outlines |
| `--radius-xl` | `16px` | Extra-large radius |
| `--shadow-float` | composite | Float shadow with subtle border ring |

### Changed Variables

| Token | Old | New | Reason |
|---|---|---|---|
| `--radius` | 6px | 8px | Softer, more modern corners |
| `--radius-lg` | 10px | 12px | Proportional increase |
| `--shadow-sm` | `0 1px 0 rgba(27,31,36,0.04)` | `0 1px 2px rgba(0,0,0,0.12)` | More visible |
| `--shadow-md` | `0 3px 6px rgba(0,0,0,0.3)` | `0 3px 8px rgba(0,0,0,0.24)` | Refined |

## Component Patterns

### Cards

```css
/* No border, background contrast only */
.card {
  background: var(--surface);
  border-radius: var(--radius-lg);
  padding: 16px;
}
```

### Tags / Badges (pill-shaped)

```css
.tag {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  /* color-specific background tint + text color, no border */
}
```

### Buttons (rectangular, borderless)

```css
.btn-secondary {
  background: var(--surface-raised);
  border-color: transparent;
}

.btn-danger {
  background: rgba(248, 81, 73, 0.08);
  color: var(--red);
  border-color: transparent;
}
```

Buttons should include icons for clarity (pencil for edit, plus for add, trash for delete, clipboard for paste).

### Form Inputs (quiet at rest)

```css
input {
  background: var(--bg);
  border: 1px solid transparent;       /* invisible at rest */
}
input:hover {
  border-color: var(--border-subtle);   /* subtle on hover */
}
input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-muted);  /* accent ring */
}
```

### Floating Elements (dropdowns, modals)

Keep a subtle border — floating elements need visual separation from content behind them.

```css
.dropdown {
  background: var(--surface-overlay);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius);
  box-shadow: var(--shadow-float);
}
```

### Sidebar Pill Links (worker, project)

Interactive links in the sidebar use pill styling with `align-self: flex-start` so they shrink to fit content. Hover uses `filter: brightness(1.2)` for consistency. No underline on hover.

```css
.sidebar-pill {
  display: inline-flex;
  align-self: flex-start;
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
}
```

### Sidebar Field Grouping

Group related fields with wrapper divs using consistent gap spacing (24px between fields). Avoid `<hr>` dividers entirely.

```css
.sidebar-group {
  display: flex;
  flex-direction: column;
  gap: 24px;
}
```

Metadata fields (created/updated timestamps) sit in their own row, using relative time (`timeAgo`) with a CSS tooltip showing the exact date on hover (via `data-tooltip` attribute, not `title` which shows a native tooltip).

### CSS Tooltips

Use `data-tooltip` attribute with `::after` pseudo-element instead of native `title` attribute. Native tooltips don't work reliably in Tauri webview and can't be styled.

```css
.has-tooltip:hover::after {
  content: attr(data-tooltip);
  position: absolute;
  bottom: calc(100% + 6px);
  padding: 4px 8px;
  background: var(--surface-overlay);
  border-radius: var(--radius);
  box-shadow: var(--shadow-float);
  white-space: nowrap;
  z-index: 10;
  pointer-events: none;
}
```

## Learnings & Gotchas

1. **Don't mix `title` and CSS tooltips** — Using both `title` attribute and `::after` pseudo-element creates duplicate tooltips. Use `data-tooltip` for CSS-only tooltips.

2. **Tag dropdown menus need their own badge class** — The sidebar uses `TagDropdown` which renders badges via `renderTag`. Create a dedicated `.sidebar-tag` class rather than reusing `.status-badge` / `.priority-badge` which have conflicting global styles (different border-radius, padding).

3. **`align-self: flex-start` for fitted pills** — Pill-shaped links inside flex column containers stretch to full width by default. Add `align-self: flex-start` to make them shrink-wrap their content.

4. **Global `a:hover` underline** — The global `a:hover { text-decoration: underline }` rule bleeds into pill-styled links. Override with `text-decoration: none` on hover for pill links.

5. **Inset shadows are too subtle in dark themes** — `box-shadow: inset` with small values is nearly invisible on dark backgrounds. Avoid relying on inset shadows for visual separation in dark themes. Background color contrast (`--bg` vs `--surface`) is the reliable approach.

6. **`--bg-alt` (#010409) is too dark** — Using `--bg-alt` for recessed content areas creates too much contrast. Stick with `--bg` (#0d1117) for content areas inside cards.

7. **Border radius changes are global** — Changing `--radius` from 6px to 8px affects every component using the variable. This is intentional and creates a uniformly softer look, but be aware of it when debugging layout shifts.

## Applied Pages

- **TaskDetailPage** — Fully redesigned with all principles above.
- **Global foundation** — Variables, panels, buttons, badges, toggles, modals, form inputs all updated.
- **TagDropdown** — Dropdown menus updated to use `--surface-overlay` and `--shadow-float`.

## Remaining Work

Other pages need to be updated to follow the new patterns:
- TasksPage (task list cards)
- WorkersPage / WorkerDetailPage
- ProjectsPage / ProjectDetailPage
- SettingsPage
- NotificationsPage
- DashboardPage
