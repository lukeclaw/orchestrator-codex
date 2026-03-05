# 021 — UI Design System v2: Shade-Based, Minimal Chrome

## Overview

A redesign of the UI foundation moving from a border-heavy style to a cleaner, shade-based approach. The goal is to reduce visual noise by eliminating decorative lines and borders, using background contrast, spacing, and semantic color to create hierarchy instead.

**Influences:** GitHub Primer (surface/border token architecture), Linear (information density, left accent bars), Material Design 3 (tonal elevation, state layers), Radix UI (12-step color scale philosophy), Refactoring UI (border alternatives, shadow composition).

## Design Principles

### 1. Decorative borders → background contrast

Cards and sections have no outline `border`. The contrast between `--bg` (#0d1117) and `--surface` (#161b22) provides sufficient visual separation. Replace `<hr>` dividers and `border-bottom` separators with spacing and subtle background differences. Use recessed backgrounds (`--bg` inside `--surface` cards) for content areas.

### 2. Semantic borders are kept

Not all borders are decorative. **Keep borders that communicate state:**
- **Status accent bars** — A colored `border-left` on worker/terminal cards indicating worker status (green=idle, blue=working, red=error). On hover, shifts to accent blue as a focus affordance.
- **Focus rings** — `border-color: var(--accent)` + `box-shadow: 0 0 0 2px` on focused inputs. Required for keyboard accessibility.
- **Float outlines** — `1px solid var(--border-subtle)` on dropdowns/modals that overlay arbitrary content.

**Rule of thumb:** if removing the border loses information or breaks accessibility, it's semantic — keep it. If removing it just makes the UI cleaner, it was decorative — remove it.

### 3. Quiet inputs, loud on focus

Form fields have no visible border at rest (just a background tint). A subtle border appears on hover, and an accent ring appears on focus. This three-state progression (rest → hover → focus) follows Material Design 3's state layer model.

### 4. Tonal elevation over shadows

Surface hierarchy is expressed through background lightness, not shadows. Shadows are reserved for truly floating elements (modals, dropdowns) where the element overlaps arbitrary content behind it.

**Elevation scale (darkest → lightest):**

| Level | Token | Hex | Use |
|---|---|---|---|
| 0 | `--bg` | `#0d1117` | Page background, recessed content areas |
| 1 | `--surface` | `#161b22` | Cards, panels, sidebar |
| 2 | `--surface-hover` | `#1c2129` | Hovered interactive surfaces |
| 3 | `--surface-raised` | `#21262d` | Secondary buttons, raised elements |
| 4 | `--surface-overlay` | `#2d333b` | Floating elements (dropdowns, tooltips, modals) |

This follows Material Design 3's tonal elevation principle: instead of relying on drop shadows (which are nearly invisible on dark backgrounds), use progressively lighter surface colors to convey height.

### 5. Pill-shaped tags, rectangular buttons

Tags/badges use `border-radius: 999px` (full pill). Buttons use `var(--radius)` (8px). This creates an instant visual distinction between status indicators (passive, informational) and interactive controls (clickable, actionable).

### 6. Semantic grouping through spacing

Related fields are grouped via wrapper elements with consistent internal gaps (24px between fields) rather than divider lines. The proximity principle: elements that belong together are closer together. The gap between a label and its value (4-8px) should be much smaller than the gap between separate fields (24px).

### 7. Interactive state progression

Every interactive element should express a clear rest → hover → active progression. Based on Material Design 3's state layer model:

| State | Visual Treatment |
|---|---|
| Rest | Base surface color, no border (or transparent border) |
| Hover | One step lighter surface (`--surface-hover`), optional accent border |
| Active/Pressed | `transform: scale(0.97)` or two steps lighter surface |
| Focus (keyboard) | 2px accent ring via `box-shadow`, `:focus-visible` only |
| Disabled | `opacity: 0.5`, `pointer-events: none` |

### 8. Consistent icon + label pairing on buttons

Buttons include icons for instant recognition: pencil for edit, plus for add, trash for delete, clipboard for paste. Icon size 16px with 4-6px gap to label text. This follows Fitts's law — the icon enlarges the scannable target area and reduces cognitive load.

## Design Tokens

### Surface & Background

| Token | Value | Purpose |
|---|---|---|
| `--bg` | `#0d1117` | Page background, recessed content |
| `--surface` | `#161b22` | Cards, panels |
| `--surface-hover` | `#1c2129` | Hovered surfaces |
| `--surface-raised` | `#21262d` | Secondary buttons, raised elements |
| `--surface-overlay` | `#2d333b` | Floating elements |

### Borders

| Token | Value | Purpose |
|---|---|---|
| `--border` | `#30363d` | Standard border (use sparingly) |
| `--border-subtle` | `#21262d` | Float outlines, hover input borders |
| `--border-muted` | `rgba(48, 54, 61, 0.4)` | Near-invisible float ring |

### Text

| Token | Value | Contrast | Purpose |
|---|---|---|---|
| `--text-primary` | `#e6edf3` | ~13:1 on `--bg` | Headings, body text |
| `--text-secondary` | `#8b949e` | ~6:1 on `--bg` | Labels, secondary info |
| `--text-muted` | `#6e7681` | ~3.7:1 on `--bg` | Timestamps, hints, disabled text |

Note: `--text-muted` is below WCAG AA for small body text (4.5:1) but acceptable for large text (3:1) and non-essential metadata per WCAG. Use only for supplementary information, never for primary content.

### Spacing (8px grid)

All spacing values are multiples of 4px, with 8px as the base unit:

| Token | Value | Use |
|---|---|---|
| `--space-1` | `4px` | Icon-to-text gap, tight inline spacing |
| `--space-2` | `8px` | Related element gap, compact padding |
| `--space-3` | `12px` | Form field internal padding |
| `--space-4` | `16px` | Default card padding, section gaps |
| `--space-6` | `24px` | Between sidebar fields, generous card padding |
| `--space-8` | `32px` | Between card groups, large section gaps |

Currently sidebar field gap is 24px, card padding is 16px. These values should be tokenized as the system matures.

### Radius

| Token | Value | Use |
|---|---|---|
| `--radius` | `8px` | Buttons, inputs, cards |
| `--radius-lg` | `12px` | Large cards, main content panels |
| `--radius-xl` | `16px` | Hero sections, extra-large containers |
| `999px` | (literal) | Pills: tags, badges, sidebar links |

### Shadows

| Token | Value | Use |
|---|---|---|
| `--shadow-sm` | `0 1px 2px rgba(0,0,0,0.12)` | Subtle lift for small elements |
| `--shadow-md` | `0 3px 8px rgba(0,0,0,0.24)` | Card hover lift |
| `--shadow-lg` | `0 8px 24px rgba(0,0,0,0.4)` | Prominent floating elements |
| `--shadow-float` | `0 4px 16px rgba(0,0,0,0.3), 0 0 0 1px var(--border-muted)` | Dropdowns, tooltips (shadow + ring) |

See [Shadows & Depth in Dark Themes](#shadows--depth-in-dark-themes) for why traditional shadows fail in dark UIs and what alternatives to use.

### Motion

| Token | Value | Use |
|---|---|---|
| `--duration-fast` | `100ms` | Button press, small toggles |
| `--duration-normal` | `150ms` | Hover states, background color transitions |
| `--duration-moderate` | `250ms` | Dropdown open/close, panel slides |
| `--ease-standard` | `cubic-bezier(0.4, 0, 0.2, 1)` | Most UI transitions |
| `--ease-decelerate` | `cubic-bezier(0, 0, 0.2, 1)` | Elements entering screen |

Guidelines:
- Never use `transition: all` — always specify exact properties (`background-color`, `transform`, `opacity`, `border-color`).
- Prefer animating only `transform` and `opacity` (GPU-composited, no layout cost).
- Hover enter should feel snappy (100-150ms), hover exit can be slightly relaxed (200-300ms).
- Respect `prefers-reduced-motion: reduce` — disable or greatly simplify animations.

## Component Patterns

### Cards

```css
.card {
  background: var(--surface);
  border-radius: var(--radius-lg);
  padding: 16px;
  /* No border — background contrast with --bg provides separation */
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
  /* color-specific background tint (10-15% opacity) + text color, no border */
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

### Form Inputs (quiet at rest)

```css
input {
  background: var(--bg);
  border: 1px solid transparent;
}
input:hover {
  border-color: var(--border-subtle);
}
input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-muted);
}
```

### Floating Elements (dropdowns, modals)

Keep a subtle border — floating elements need visual separation from arbitrary content behind them.

```css
.dropdown {
  background: var(--surface-overlay);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius);
  box-shadow: var(--shadow-float);
}
```

### Status Accent Cards (worker/terminal preview)

Clickable cards with a left accent bar showing status. On hover, the accent bar shifts to `--accent` blue and the card lifts with a shadow. This "hover previews the focused state" pattern creates a smooth rest → hover → click progression.

```css
.accent-card {
  border-left: 3px solid var(--text-muted);
  transition: border-color 150ms, box-shadow 150ms, background 150ms;
}
.accent-card:hover {
  border-color: var(--accent);
  background: var(--surface-hover);
  box-shadow: var(--shadow-md);
}
/* Status-specific accent colors override at rest */
.accent-card.status-working { border-left-color: #58a6ff; }
.accent-card.status-idle    { border-left-color: var(--green); }
.accent-card.status-error   { border-left-color: var(--red); }
```

### Sidebar Pill Links

Interactive links in the sidebar use pill styling with `align-self: flex-start` so they shrink to fit content. Hover uses `filter: brightness(1.2)` for consistency. No underline on hover.

```css
.sidebar-pill {
  display: inline-flex;
  align-self: flex-start;
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  text-decoration: none;
  transition: filter 150ms;
}
.sidebar-pill:hover {
  filter: brightness(1.2);
  text-decoration: none;  /* override global a:hover */
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
.has-tooltip {
  position: relative;
}
.has-tooltip:hover::after {
  content: attr(data-tooltip);
  position: absolute;
  bottom: calc(100% + 6px);
  padding: 4px 8px;
  background: var(--surface-overlay);
  border-radius: var(--radius);
  box-shadow: var(--shadow-float);
  font-size: 12px;
  white-space: nowrap;
  z-index: 10;
  pointer-events: none;
}
```

## Typography

### Font Smoothing

Use `-webkit-font-smoothing: antialiased` globally. macOS default subpixel antialiasing makes light text on dark backgrounds appear bolder/thicker than intended. Antialiased rendering produces accurate weight rendering in dark themes.

### Weight Guidelines

- Body text: weight 400 (regular). Do not go below 300 on dark backgrounds.
- Labels, small text: weight 500 (medium) to compensate for small size.
- Headings: weight 600 (semibold). Avoid 700 (bold) — dark backgrounds amplify visual heaviness.

### Size Hierarchy

| Role | Size | Line Height | Weight |
|---|---|---|---|
| Caption / meta | 11px | 16px | 400 |
| Small / label | 12px | 16px | 500 |
| Body (default) | 14px | 20px | 400 |
| Subheading | 16px | 24px | 500 |
| Heading | 20px | 28px | 600 |

## Shadows & Depth in Dark Themes

### The core problem

Traditional drop shadows use `rgba(0, 0, 0, opacity)` — darker than the background. On `--bg` (#0d1117, ~7% lightness), there's almost no room to go darker. A shadow at 0.15 opacity on `#0d1117` produces a difference of ~2 lightness steps — imperceptible to the human eye. This means **darkness-based shadows don't create visual separation on dark backgrounds; they only add faint ambient depth at best.**

### What works instead

Five techniques reliably create depth and separation in dark themes, ranked by effectiveness:

**1. Surface color contrast (primary technique)**

The most reliable separator. A card at `--surface` (#161b22) on `--bg` (#0d1117) creates a clear ~5% lightness difference. This is why tonal elevation is Principle #4.

**2. 1px light rings**

A subtle `0 0 0 1px rgba(255,255,255,0.06)` (or `var(--border-muted)`) creates a perceivable edge where shadow alone cannot. This is why `--shadow-float` pairs a blur shadow with a 1px ring — the ring does the real separation work, the shadow adds ambient atmosphere.

**3. Colored glows**

Instead of `rgba(0,0,0,...)`, use a saturated color: `0 0 6px rgba(63, 185, 80, 0.5)`. Bright colors naturally glow against dark backgrounds. Already used successfully in the codebase for status indicators (BrainPanel active glow, TerminalView connected glow, Header status pulse).

**4. Colored focus rings**

`box-shadow: 0 0 0 2px var(--accent-muted)` works perfectly on any background because it relies on color contrast, not lightness contrast. This is the standard technique for focus indication.

**5. Backdrop blur (for modals/overlays)**

`backdrop-filter: blur(8px)` with a semi-transparent background creates a frosted glass effect that naturally separates floating content from what's behind it — no shadow needed. Consider for modals: `background: rgba(22, 27, 34, 0.85); backdrop-filter: blur(8px)`.

### What doesn't work

- **Inset shadows** — `box-shadow: inset 0 2px 4px rgba(0,0,0,0.25)` is invisible on dark surfaces. We tested this on content areas and confirmed it.
- **Low-opacity shadows** — Anything at `rgba(0,0,0, 0.1-0.15)` is wasted CSS on dark backgrounds. Several pages still have these from the old design and should be cleaned up.
- **Single-layer blur shadows alone** — A standalone `0 4px 16px rgba(0,0,0,0.3)` without a ring or surface color change is barely noticeable.

### Shadow role assignments

Match each depth need to the right technique:

| Need | Primary technique | Supporting technique |
|---|---|---|
| **Card on page** | Surface contrast (`--surface` on `--bg`) | None needed |
| **Card hover** | Surface shift (`--surface-hover`) | `--shadow-md` for subtle atmosphere |
| **Floating element** (dropdown, tooltip) | 1px ring + higher surface (`--surface-overlay`) | `--shadow-float` for ambient depth |
| **Modal** | Backdrop dim + surface + ring | `--shadow-lg`, consider `backdrop-filter: blur` |
| **Focus indication** | Colored ring (`0 0 0 2px var(--accent-muted)`) | — |
| **Status emphasis** | Colored glow (`0 0 6px rgba(color, 0.5)`) | — |
| **Recessed content** | Darker background (`--bg` inside `--surface`) | — |

### Cleanup needed

These existing shadows in the codebase are effectively invisible and should be removed or bumped to 0.3+:

- `WorkerCard.css` / `WorkerCardCompact.css` / `ProjectCard.css` / `SkillCard.css`: `0 2px 8px rgba(0,0,0,0.15)` — too faint
- `SettingsPage.css`: `0 1px 3px rgba(0,0,0,0.1)` — invisible
- `NotificationsPage.css`: `0 1px 3px rgba(0,0,0,0.1)` and `0 2px 8px rgba(0,0,0,0.06)` — invisible
- `ConfirmPopover.css`: `0 4px 12px rgba(0,0,0,0.15)` — barely visible
- `RdevTable.css`: `0 1px 4px rgba(0,0,0,0.15)` — too faint

### Optional enhancement: top-light rim

A very subtle `inset 0 1px 0 rgba(255,255,255,0.04)` on the top edge of elevated cards simulates overhead light catching a raised surface (used in macOS dark mode and Raycast). Extremely subtle individually, but when applied consistently across all cards, the cumulative effect adds dimensionality. Not yet implemented — consider during the page rollout phase.

## Learnings & Gotchas

1. **Don't mix `title` and CSS tooltips** — Using both `title` attribute and `::after` pseudo-element creates duplicate tooltips. Use `data-tooltip` for CSS-only tooltips.

2. **Tag dropdown menus need their own badge class** — The sidebar uses `TagDropdown` which renders badges via `renderTag`. Create a dedicated `.sidebar-tag` class rather than reusing `.status-badge` / `.priority-badge` which have conflicting global styles (different border-radius, padding).

3. **`align-self: flex-start` for fitted pills** — Pill-shaped links inside flex column containers stretch to full width by default. Add `align-self: flex-start` to make them shrink-wrap their content.

4. **Global `a:hover` underline** — The global `a:hover { text-decoration: underline }` rule bleeds into pill-styled links. Override with `text-decoration: none` on hover for pill links.

5. **Inset shadows are too subtle in dark themes** — `box-shadow: inset` with small values is nearly invisible on dark backgrounds. Avoid relying on inset shadows for visual separation. Background color contrast (`--bg` vs `--surface`) is the reliable approach. See [Shadows & Depth in Dark Themes](#shadows--depth-in-dark-themes) for the full analysis.

6. **`--bg-alt` (#010409) is too dark** — Using `--bg-alt` for recessed content areas creates too much contrast. Stick with `--bg` (#0d1117) for content areas inside cards.

7. **Border radius changes are global** — Changing `--radius` from 6px to 8px affects every component using the variable. This is intentional and creates a uniformly softer look, but be aware of it when debugging layout shifts.

8. **Semi-transparent borders adapt to any surface** — When borders are needed, prefer `rgba(...)` values over opaque hex. `rgba(48, 54, 61, 0.4)` works on any surface level, while `#30363d` only looks right on one specific background.

9. **Hover enter ≠ hover exit timing** — Hover enter should feel snappy (100-150ms) so the UI feels responsive. Hover exit can be slower (200-300ms) so elements don't flicker during casual mouse movement. Use separate `transition` values or accept the single value that best balances both (150ms).

## Applied Pages

- **TaskDetailPage** — Fully redesigned with all principles above.
- **Global foundation** — Variables, panels, buttons, badges, toggles, modals, form inputs all updated.
- **TagDropdown** — Dropdown menus updated to use `--surface-overlay` and `--shadow-float`.

## Page-by-Page Audit

Visual inspection of every page via Playwright (screenshots at `tmp/01-*.png` through `tmp/09-*.png`) combined with a full CSS audit of all page and component stylesheets.

### Dashboard (`/`)

**Current state:** Uses `.panel` containers with stat cards, activity feed, and charts. The global `.panel` border was already removed in the foundation work, so this page partially benefits.

**What's already good:** Stat cards use global panel styling (now borderless). Activity feed items use spacing not borders. Charts sit on `--surface`.

**What to fix:** Minor — check for any remaining hardcoded colors. Low effort.

**Risk:** None.

### Tasks Page (`/tasks`)

**Current state:** Table layout with colored status/priority badges, bordered table wrapper (`1px solid var(--border)`), dropdown with hardcoded shadow `0 4px 12px rgba(0,0,0,0.3)`.

**What to fix:**
- Remove table wrapper border — use surface contrast instead
- Replace dropdown shadow with `--shadow-float` (adds the ring component)
- Check if row separators use `border-bottom` — replace with `--border-subtle` or subtle alternating backgrounds

**Risk:** Low. Table readability is maintained by row hover states and column alignment. Test with real data to confirm.

### Workers Page (`/workers`)

**Current state:** `WorkerCard` components with:
- `border: 1px solid var(--border)` — full outline border
- `border-left: 3px solid` — status accent bar (semantic, keep)
- Hover shadow: `0 2px 8px rgba(0,0,0,0.15)` — **invisible** on dark background
- Hardcoded terminal bg: `#0d1117` instead of `var(--bg)`

**What to fix:**
- Remove card outline border, rely on surface contrast + spacing
- Keep the left accent bar (semantic border)
- Replace invisible hover shadow with `--surface-hover` background shift + optional `--shadow-md`
- Replace hardcoded `#0d1117` with `var(--bg)`

**Risk:** Medium. Worker cards are dense with terminal output previews. Removing the outline border may make adjacent cards harder to distinguish. **Mitigation:** ensure sufficient gap between cards (16-24px).

### Projects Page (`/projects`)

**Current state:** Grid of `ProjectCard` components with progress bars:
- `border: 1px solid var(--border)` — full outline
- Hover shadow: `0 2px 8px rgba(0,0,0,0.15)` — invisible
- Layout picker popover: `1px solid var(--border)` + `--shadow-lg`

**What to fix:**
- Remove card borders, use surface contrast (grid gap provides separation)
- Kill invisible hover shadow, use surface color shift
- Popover should use `--shadow-float` pattern (ring + shadow)
- Progress bar colors should reference tokens where possible

**Risk:** Low. Project cards are well-spaced in a grid with gaps.

### Settings Page (`/settings`)

**Current state:** Tab-based layout with pill-style tabs. Content panels use:
- `border: 1px solid var(--border-subtle)` — lighter border than most pages
- Shadow: `0 1px 3px rgba(0,0,0,0.1)` — **invisible**
- References `var(--bg-secondary)` — **undefined variable**

**What to fix:**
- Fix undefined `--bg-secondary` — replace with `var(--surface)` or `var(--bg)`
- Remove invisible shadow
- Remove panel borders, use surface contrast
- Pill tabs are already a good pattern — keep

**Risk:** Low. Settings is a simple form-based layout.

### Notifications Page (`/notifications`)

**Current state:** Notification cards with:
- `border: 1px solid var(--border)` — full outlines
- Hover shadow: `0 2px 8px rgba(0,0,0,0.06)` — **the most invisible shadow in the codebase**
- Focus: `0 0 0 1px var(--accent)` — good, colored ring works
- Tab filters use hardcoded `rgba(96, 165, 250, 0.15)` instead of accent token
- References `--text` — **undefined variable** (should be `--text-primary`)

**What to fix:**
- Fix undefined `--text` variable
- Remove card borders
- Kill the 0.06 opacity shadow (wasted CSS)
- Replace hardcoded tab color with accent-based token

**Risk:** Low. Notifications are a list — spacing/surface contrast works well for list layouts.

### Worker Detail / Rdevs Page

**Current state:** Table-based layout for remote dev environments:
- Row borders for table structure
- Status indicators with colored backgrounds
- Dropdown shadow: `0 1px 4px rgba(0,0,0,0.15)` — barely visible

**What to fix:**
- Replace row borders with `--border-subtle` or subtle alternating row backgrounds
- Fix dropdown shadow opacity

**Risk:** Medium. Tables need some form of row separation — can't remove all lines without an alternative (zebra striping or increased row gap).

### Session Detail Page

**Current state:** Split layout with topbar/footer and tab sections:
- `border: 1px solid var(--border)` on topbar/footer
- `box-shadow: 0 1px 4px rgba(0,0,0,0.3)` on topbar — acceptable opacity
- Tab underline indicators using inset shadows — functional, keep
- Footer: `0 -1px 4px rgba(0,0,0,0.4)` — acceptable

**What to fix:**
- Minor: consider replacing topbar border with surface contrast
- Tab underline indicators work well as semantic borders — keep

**Risk:** Low. Session detail has a unique layout that benefits from some structural borders.

### Sidebar + Header (global layout)

**Current state:**
- Sidebar: `box-shadow: 0 -1px 4px rgba(0,0,0,0.4)` on bottom section — reasonable opacity
- Header: colored glows for status indicators (`0 0 6px rgba(63,185,80,0.5)`) — correct pattern per design doc

**What to fix:** Already mostly good. Minor token fixes only.

**Risk:** None.

### Floating Elements (modals, popovers, toasts)

**Current state:**
- ConfirmPopover: `border: 1px solid var(--border)` + shadow `0 4px 12px rgba(0,0,0,0.15)` — shadow too faint
- NotificationToast: `backdrop-filter: blur(12px)` + shadow at 0.35 — the most modern-looking component
- GettingStartedModal: `backdrop-filter: blur(2px)` on overlay + white ring `rgba(255,255,255,0.05)` — effective
- CustomSelect dropdown: uses `var(--shadow-md)` — good token usage

**What to fix:**
- ConfirmPopover: replace shadow with `--shadow-float`
- Consider extending `backdrop-filter: blur` to all modal overlays for consistency
- Standardize all floating element shadows to use `--shadow-float` token

**Risk:** Low. Floating elements benefit from maximum separation techniques.

## Cross-Cutting Issues

### Undefined CSS Variables

These variables are referenced but don't exist in `variables.css`:

| Variable | Used in | Fix |
|---|---|---|
| `--text` | ConfirmPopover.css, NotificationToast.css | Replace with `--text-primary` |
| `--bg-secondary` | SettingsPage.css | Replace with `var(--surface)` or `var(--bg)` |
| `--text-tertiary` | TerminalView.css | Replace with `var(--text-muted)` |

### Hardcoded Colors (should use tokens)

| Value | Used in | Replace with |
|---|---|---|
| `#0d1117` | WorkerCard.css, WorkerCardCompact.css, TerminalView.css | `var(--bg)` |
| `#2A2D2E` | FileExplorerPanel.css (node hover) | `var(--surface-hover)` |
| `#04395E` | FileExplorerPanel.css (node selected) | `rgba(88,166,255,0.15)` or accent-muted |
| `#000` | BrowserView.css (canvas) | `var(--bg)` or keep (true black for canvas) |
| `rgba(96,165,250,0.15)` | NotificationsPage.css (tabs) | `var(--accent-muted)` |
| `#3b82f6`, `#8b5cf6`, `#f59e0b` | BrowserView.css, TerminalView.css | `var(--accent)`, `var(--purple)`, `var(--yellow)` |

### Invisible Shadows (should remove or fix)

| File | Current value | Action |
|---|---|---|
| WorkerCard.css | `0 2px 8px rgba(0,0,0,0.15)` | Remove; use `--surface-hover` bg shift on hover |
| WorkerCardCompact.css | `0 2px 8px rgba(0,0,0,0.15)` | Same |
| ProjectCard.css | `0 2px 8px rgba(0,0,0,0.15)` | Same |
| SkillCard.css | `0 2px 8px rgba(0,0,0,0.15)` | Same |
| SettingsPage.css | `0 1px 3px rgba(0,0,0,0.1)` | Remove |
| NotificationsPage.css | `0 2px 8px rgba(0,0,0,0.06)` | Remove |
| ConfirmPopover.css | `0 4px 12px rgba(0,0,0,0.15)` | Replace with `--shadow-float` |
| RdevTable.css | `0 1px 4px rgba(0,0,0,0.15)` | Remove or bump to 0.3 |

## Rollout Plan

Recommended order, balancing visual impact with risk:

| Phase | Pages/Components | Effort | Impact |
|---|---|---|---|
| 1 | WorkerCard, ProjectCard, SkillCard | Medium | High — most visible cards across the app |
| 2 | NotificationsPage | Low | Medium — fix undefined vars, remove borders/shadows |
| 3 | SettingsPage | Low | Low — fix undefined var, remove border/shadow |
| 4 | TasksPage, ContextPage | Medium | Medium — table wrapper borders, dropdown shadows |
| 5 | Floating elements (ConfirmPopover, CustomSelect) | Low | Medium — shadow/ring consistency |
| 6 | SessionDetailPage, RdevTable | Medium | Low — table patterns, lowest priority |
| 7 | Token cleanup (hardcoded colors, undefined vars) | Low | Low — maintenance, no visual change |

### Future Token Work

- Tokenize spacing values (`--space-1` through `--space-8`) in `variables.css` and migrate hardcoded `px` values.
- Tokenize motion values (`--duration-*`, `--ease-*`) in `variables.css`.
- Tokenize typography sizes (`--text-sm`, `--text-base`, etc.) for consistent type scale.
- Consider adding `prefers-reduced-motion` media query to disable/simplify all transitions.
- Audit `--text-muted` usage — ensure it's never used for essential information (below WCAG AA for small text).
- Extend `backdrop-filter: blur` consistently to all modal/overlay components.
