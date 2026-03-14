# 022 — UI Design Principles

The visual design language for Orchestrator. This document is the source of truth for how the UI should look and feel — not a description of what currently exists, but what we build toward. Reference this when improving existing pages and building new ones.

## Identity

Orchestrator is a dark-themed control center for engineers managing parallel AI workers. It should feel like a professional instrument panel — calm, information-dense, and precise. Think Linear meets a flight deck: everything has a purpose, nothing is decorative for its own sake.

**One-line summary:** Calm, dark, borderless. Depth through shade, clarity through spacing, meaning through color.

## Core Values

### 1. Information over decoration

Every pixel should communicate something. If a border, shadow, or visual element doesn't help the user understand state, find information, or take action, remove it. Negative space and typographic hierarchy do the heavy lifting — not chrome.

### 2. Calm under complexity

The UI manages 10+ workers, dozens of tasks, and live terminal sessions simultaneously. It must stay visually calm as complexity scales. Muted backgrounds, restrained color, and consistent patterns prevent cognitive overload. Color is reserved for meaning — status, focus, and emphasis — never for aesthetics alone.

### 3. Instant legibility

A user glancing at any screen should immediately understand: what's the status, what needs attention, what can I act on. This is achieved through a strict visual hierarchy: size → weight → color → position. If you need more than one visual signal to convey importance, the design is too subtle.

### 4. Predictable everywhere

Every card, button, dropdown, and hover state should behave identically across every page. A user who learns how the Workers page works should already know how the Projects page works. Consistency is not boring — it's fast.

---

## Color System

### Philosophy

Color is semantic. Every color in the palette has a specific meaning. We never use color for decoration or to "make things look nicer." If two elements use the same color, they should represent the same concept.

### Surface Hierarchy

The foundation of visual depth. Instead of borders and shadows, we separate elements through background lightness. Each level is ~5-7% lighter than the previous one.

```
Level 0  --bg              #0d1117   Page background, recessed content areas
Level 1  --surface         #161b22   Cards, panels, sidebar
Level 2  --surface-hover   #1c2129   Hovered interactive surfaces
Level 3  --surface-raised  #21262d   Buttons, raised elements, selected states
Level 4  --surface-overlay #2d333b   Floating: dropdowns, tooltips, modals
```

**Rules:**
- A child element is always the same level or lighter than its parent, never darker (except for intentionally recessed content areas, which use `--bg` inside a `--surface` card).
- No pure black (`#000000`). It creates excessive contrast (21:1 with white text), causes halation (text appears to glow), and makes elevated surfaces impossible to distinguish.
- No pure white (`#ffffff`) for text. Use `--text-primary` (#e6edf3) for ~13:1 contrast — high enough for readability, low enough to prevent eye strain.

### Text Hierarchy

Three levels of text emphasis, each with a clear purpose:

```
--text-primary    #e6edf3   ~13:1 contrast   Headings, body text, values
--text-secondary  #8b949e    ~6:1 contrast   Labels, field names, supporting text
--text-muted      #6e7681   ~3.7:1 contrast  Timestamps, hints, disabled text
```

`--text-muted` is intentionally below WCAG AA for small text (4.5:1). It is only used for supplementary, non-essential information (relative timestamps, secondary metadata). Never use it for primary content or labels.

### Status Colors

Every status color carries a specific meaning across the entire app. These never change meaning based on context.

```
--green    #3fb950   Success, idle, done, healthy, completed
--yellow   #d29922   Warning, waiting, needs attention, in review
--red      #f85149   Error, disconnected, blocked, destructive action
--orange   #db6d28   Paused, detached, degraded, caution
--purple   #a371f7   Special, completed tasks (distinguished from active green)
--accent   #58a6ff   Active, selected, focused, links, primary action
```

**Status color usage:** Always as a background tint at 10-15% opacity with the full color as text. Never as a solid fill for large areas (too intense). Status badges: `background: rgba(color, 0.15); color: full-color`.

### Accent Color

`--accent` (#58a6ff) is the app's primary interactive color. It means "this is active, selected, or will take you somewhere." Used for:
- Links and navigation highlights
- Focus rings
- Primary buttons
- Active tab indicators
- Selected state backgrounds (at `--accent-muted` opacity)

A single accent color keeps the UI calm. We don't use multiple brand colors.

---

## Depth and Elevation

### The Dark Theme Depth Problem

Traditional drop shadows (`rgba(0,0,0,...)`) are nearly invisible on dark backgrounds. On `--bg` (#0d1117, ~7% lightness), a shadow has almost no room to go darker — the difference between the background and the shadow is imperceptible. This means the entire shadow-based depth model used in light themes doesn't transfer.

### Our Depth Model

We use five techniques instead of shadows, each suited to a specific need:

**1. Surface color contrast (primary technique)**

The main depth mechanism. Cards at `--surface` on a `--bg` page, hover states at `--surface-hover`, floating elements at `--surface-overlay`. Each step creates a ~5% lightness increase — small but cumulative and perceptible.

**2. 1px light rings**

A subtle `0 0 0 1px rgba(255,255,255,0.06)` creates a perceivable edge where darkness-based shadows cannot. Used on floating elements (dropdowns, tooltips, modals) combined with a blur shadow for ambient depth. This is why the `--shadow-float` token pairs a blur shadow with a ring.

**3. Colored glows**

Saturated colors glow naturally on dark backgrounds. `0 0 6px rgba(63, 185, 80, 0.5)` creates a green status glow that's immediately visible. Used for status emphasis (active indicators, connected states, pulsing alerts).

**4. Colored focus rings**

`box-shadow: 0 0 0 2px var(--accent-muted)` works perfectly on any background because it relies on color contrast, not lightness contrast. The standard technique for keyboard focus indication.

**5. Backdrop blur**

`backdrop-filter: blur(8px)` with a semi-transparent background creates a frosted glass effect for modals and overlays. The blurred content behind naturally separates the float from the page without any shadow.

### What Doesn't Work on Dark Backgrounds

- **Inset shadows** — invisible at any reasonable opacity
- **Low-opacity shadows** (`rgba(0,0,0, 0.06-0.15)`) — wasted CSS, no visual effect
- **Single-layer blur shadows alone** — a standalone `0 4px 16px rgba(0,0,0,0.3)` without a ring or surface change is barely noticeable

### Depth Assignment Table

| Need | Primary technique | Supporting technique |
|---|---|---|
| Card on page | Surface contrast (`--surface` on `--bg`) | None needed |
| Card hover | Surface color shift (`--surface-hover`) | Optional `--shadow-md` for atmosphere |
| Floating element | 1px ring + higher surface (`--surface-overlay`) | `--shadow-float` for ambient depth |
| Modal | Backdrop dim + blur + surface + ring | `--shadow-lg` |
| Focus indication | Colored ring (`0 0 0 2px var(--accent-muted)`) | — |
| Status emphasis | Colored glow (`0 0 6px rgba(color, 0.5)`) | — |
| Recessed content | Darker background (`--bg` inside `--surface`) | — |

### Shadow Tokens

When shadows are used, they must be from the token set, never hardcoded:

```
--shadow-sm     0 1px 2px rgba(0,0,0,0.12)                                    Subtle lift
--shadow-md     0 3px 8px rgba(0,0,0,0.24)                                    Card hover atmosphere
--shadow-lg     0 8px 24px rgba(0,0,0,0.4)                                    Large floating elements
--shadow-float  0 4px 16px rgba(0,0,0,0.3), 0 0 0 1px var(--border-muted)     Dropdowns, tooltips (shadow + ring)
```

Dark theme shadows use higher opacity (0.12-0.4) than light themes (0.04-0.12) because dark backgrounds absorb shadow.

---

## Borders

### Two Categories

**Decorative borders** separate visual areas. They are the old-fashioned way to define cards, panels, and sections. In this design system, we don't use them. Surface contrast and spacing do this job.

**Semantic borders** communicate information. They are kept:

| Border type | Purpose | Example |
|---|---|---|
| Status accent bar | `border-left: 3px solid` on cards, colored by worker/task status | Worker card left edge: green=idle, blue=working, red=error |
| Focus ring | `border-color: var(--accent)` on focused inputs | Required for keyboard accessibility |
| Float outline | `1px solid var(--border-subtle)` on dropdowns/modals | Floating elements need edge definition against arbitrary backgrounds |
| Validation border | `border-color: var(--red)` on invalid inputs | Communicates form error state |

**Rule:** If removing a border loses information or breaks accessibility, it's semantic — keep it. If removing it just makes the UI quieter, it was decorative — remove it.

### Border Tokens

```
--border         #30363d                  Standard border (use sparingly — semantic only)
--border-subtle  #21262d                  Float outlines, hover input borders
--border-muted   rgba(48, 54, 61, 0.4)   Near-invisible ring for shadow-float combo
```

Prefer semi-transparent borders (`rgba(...)`) over opaque hex. They adapt to any surface level.

---

## Spacing

### 8px Grid

All spacing is multiples of 4px, with 8px as the base unit. This constrains choices enough to maintain consistency while providing sufficient granularity.

```
4px    Icon-to-text gap, tight inline spacing
8px    Related element gap, compact padding
12px   Form field internal padding, small component gaps
16px   Default card padding, section gaps within a card
24px   Between sidebar fields, generous card padding, field group gaps
32px   Between card groups, major section gaps
48px   Page section separation (rare)
```

### Spacing Principles

- **Proximity = relationship.** Elements that belong together are closer together. The gap between a label and its value (4-8px) is much smaller than the gap between separate fields (24px).
- **Start generous, then tighten.** More whitespace makes designs look more polished. When in doubt, add more space, not less.
- **Consistent internal padding.** All cards use 16px padding. All sidebar groups use 24px gap. These values never vary per-instance.

---

## Typography

### Font Smoothing

Always use `-webkit-font-smoothing: antialiased` globally. macOS default subpixel antialiasing makes light text on dark backgrounds appear bolder and thicker than intended. Antialiased rendering produces accurate weight rendering.

### Font Stacks

```
--font-sans   -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif
--font-mono   'SF Mono', 'Menlo', 'Monaco', 'Consolas', monospace
```

### Weight Rules

- **Body text:** weight 400 (regular). Never go below 300 on dark backgrounds — thin text bleeds into the background.
- **Labels and small text:** weight 500 (medium) to compensate for reduced size.
- **Headings:** weight 600 (semibold). Avoid 700 (bold) — dark backgrounds amplify perceived heaviness.

### Size Scale

| Role | Size | Line Height | Weight | Use |
|---|---|---|---|---|
| Caption | 11px | 16px | 400 | Timestamps, tertiary metadata |
| Small | 12px | 16px | 500 | Labels, badge text, tag text |
| Body | 14px | 20px | 400 | Default body text, form values |
| Subheading | 16px | 24px | 500 | Section headers within cards |
| Heading | 20px | 28px | 600 | Page titles, card titles |

Line height for body text should be at least 1.4x font size. For headings, 1.2-1.4x is appropriate.

---

## Interactive States

### State Progression

Every interactive element expresses a clear, consistent state chain:

| State | Visual treatment | Timing |
|---|---|---|
| Rest | Base surface color, transparent border | — |
| Hover | One step lighter surface (`--surface-hover`), optional accent hint | 150ms ease |
| Active / Pressed | `transform: scale(0.97)` or two steps lighter surface | 100ms ease |
| Focus (keyboard) | 2px accent ring via `box-shadow`, applied on `:focus-visible` only | Instant |
| Disabled | `opacity: 0.5`, `pointer-events: none` | — |
| Selected | Accent background at low opacity (`--accent-muted`) | 150ms ease |

### Hover Philosophy

- Hover enter is **snappy** (100-150ms) — the UI should feel immediately responsive.
- Hover exit is **relaxed** (200-300ms) — prevents flickering during casual mouse movement.
- Always specify exact transition properties (`background-color`, `border-color`, `transform`). Never `transition: all`.

### Link Hover and Text-Decoration

The global CSS sets `a:hover { text-decoration: underline }` for inline text links (paragraphs, markdown content). However, many `<a>` and `<Link>` elements are styled as **badges, cards, pills, or buttons** — not text links. These must explicitly override the underline on hover.

**Why this is easy to miss:** The global `a:hover` has specificity `(0,1,1)`. A class selector like `.my-badge` has specificity `(0,1,0)`. Even if the base class sets `text-decoration: none`, the global hover rule wins during hover because `(0,1,1) > (0,1,0)`. The fix must be on the `:hover` pseudo-class: `.my-badge:hover { text-decoration: none; }`.

**Rule:** If a link has a custom background, padding, or border-radius, it is not a text link — always add `text-decoration: none` to its `:hover` state. This applies to: task badges, tunnel badges, sidebar pill links, worker links, breadcrumb links, subtask row links, and any card-like anchor element.

### Focus

- Use `:focus-visible`, never `:focus`. Focus rings appear for keyboard navigation only, not mouse clicks.
- Focus ring: `box-shadow: 0 0 0 2px var(--accent-muted)` with `border-color: var(--accent)`.
- Every interactive element must be keyboard-focusable and show a visible focus indicator. No exceptions.

---

## Motion

### Duration Scale

```
100ms   Button press, toggle switch, tiny state changes
150ms   Hover states, background transitions, most interactive feedback
250ms   Dropdown open/close, panel slides, accordion expand
400ms   Modal entrance, page transitions (rare)
```

### Easing

```
Standard    cubic-bezier(0.4, 0, 0.2, 1)     Most transitions (elements moving within viewport)
Decelerate  cubic-bezier(0, 0, 0.2, 1)        Elements entering screen (dropdowns opening, modals appearing)
Accelerate  cubic-bezier(0.4, 0, 1, 1)        Elements leaving screen (modals closing, toasts dismissing)
```

Never use `linear` for UI transitions. Nothing in nature moves at constant speed.

### Performance

- Prefer animating `transform` and `opacity` only. They are GPU-composited and don't trigger layout or paint.
- Respect `prefers-reduced-motion: reduce`. Disable or greatly simplify all animations for users who request it.
- Use `will-change: transform` on elements that animate frequently (e.g., panels being dragged).

---

## Component Patterns

### Cards

The primary container for grouped information. Always borderless — background contrast with the page provides separation.

```
Background:    var(--surface)
Border:        none
Border-radius: var(--radius-lg)  (12px)
Padding:       16px
```

Cards can contain recessed content areas (description, notes) at `background: var(--bg)` to create a visible inset effect via reverse elevation.

### Status Accent Cards

Cards that represent stateful entities (workers, terminals) use a colored left accent bar instead of a full border. The accent color communicates status at a glance.

```
Border-left:       3px solid (status color)
Hover:             accent bar shifts to --accent, background to --surface-hover, shadow-md appears
Status overrides:  green=idle, blue=working, yellow=waiting, red=error, orange=paused
```

This left-bar pattern is a semantic border — it communicates information, so it stays even in a borderless design.

### Tags and Badges (pill-shaped)

Status indicators that are **read-only and informational**. Full pill shape distinguishes them from interactive buttons.

```
Shape:           border-radius: 999px (full pill)
Padding:         3px 10px
Font:            12px, weight 500
Color:           Status color at full saturation
Background:      Status color at 10-15% opacity
Border:          none
```

### Buttons (rectangular)

Interactive controls that **take action**. Rectangular shape distinguishes them from passive tags.

```
Shape:           border-radius: var(--radius)  (8px)
Padding:         6px 12px
Font:            13px, weight 500
```

**Variants:**

| Variant | Background | Text color | Border | Use |
|---|---|---|---|---|
| Primary | `var(--accent)` | white | none | Main CTA, one per section max |
| Secondary | `var(--surface-raised)` | `--text-primary` | transparent | Default action, most common |
| Danger | `rgba(248,81,73, 0.08)` | `--red` | transparent | Destructive actions |
| Ghost | transparent | `--text-secondary` | transparent | Tertiary actions, icon-only |

**Icons in buttons:** Include a 16px icon with 4-6px gap to the label. Icons provide instant recognition: pencil for edit, plus for add, trash for delete, clipboard for paste.

### Pill Links

Interactive links styled as pills (sidebar worker links, project links). Pill shape + tinted background distinguishes them from plain text links.

```
Shape:           border-radius: 999px
Padding:         4px 12px
Display:         inline-flex (shrink-wraps content)
Align-self:      flex-start (prevents stretching in flex containers)
Hover:           filter: brightness(1.2), no underline
Text-decoration: none (overrides global a:hover underline)
```

### Form Inputs

Quiet at rest, visible on interaction. Three-state progression: rest → hover → focus.

```
Rest:    background: var(--bg), border: 1px solid transparent
Hover:   border-color: var(--border-subtle)
Focus:   border-color: var(--accent), box-shadow: 0 0 0 2px var(--accent-muted)
```

Dropdowns/selects use the same pattern, with a custom chevron via `appearance: none` + background SVG.

### Floating Elements

Dropdowns, tooltips, popovers, and context menus that overlay the page. These are the one place where borders, shadows, and elevated surfaces all combine — they need maximum separation from arbitrary content behind them.

```
Background:    var(--surface-overlay)
Border:        1px solid var(--border-subtle)
Border-radius: var(--radius)
Box-shadow:    var(--shadow-float)
```

For modals, additionally consider `backdrop-filter: blur(8px)` on the overlay backdrop.

### Tooltips (CSS-only)

Use `data-tooltip` attribute with `::after` pseudo-element. Never use the native `title` attribute — it creates unstyled, uncontrollable browser tooltips that don't work in Tauri.

```css
.element[data-tooltip]:hover::after {
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

### Tables

Tables are the densest information display. They keep subtle structural borders for row readability but remove outer container borders.

```
Row borders:       1px solid var(--border-subtle) or none (use row hover instead)
Container border:  none — surface contrast separates the table from the page
Row hover:         background: var(--surface-hover)
Header:            font-weight 500, --text-secondary color, no background change
Minimum row height: 36px for comfortable click targets
```

For very dense tables (15+ columns), consider alternating row backgrounds (`--bg` / `--surface`) instead of row borders.

### Modals

Maximum elevation. The content behind should be visually suppressed.

```
Overlay:       rgba(0, 0, 0, 0.5), optionally with backdrop-filter: blur(8px)
Content:       background: var(--surface-overlay), border: 1px solid var(--border-subtle)
Shadow:        var(--shadow-lg)
Border-radius: var(--radius-lg)
Max-width:     560px (forms), 800px (content-heavy), 90vw cap
```

### Confirmation Popovers

Inline confirmation for destructive actions. Positioned near the trigger element, not centered like modals.

```
Background:    var(--surface-overlay)
Border:        1px solid var(--border-subtle), or status color for danger/warning variants
Shadow:        var(--shadow-float)
Animation:     fade + slight translate-y, 150ms ease-out
```

Never use `window.confirm()`, `window.alert()`, or `window.prompt()`. They don't work in Tauri.

### Notification Toasts

Transient messages that appear and auto-dismiss. Should feel lightweight and non-blocking.

```
Background:    var(--surface-overlay) with backdrop-filter: blur(12px)
Border-top:    3px solid (status color for success/warning/error/info)
Shadow:        0 4px 24px rgba(0,0,0,0.35)
Animation:     slide-in from top, 250ms ease-out; slide-out 300ms ease-in
Auto-dismiss:  5 seconds for info, 8 seconds for errors, manual dismiss available
```

---

## Layout Patterns

### Sidebar Field Groups

When displaying metadata in a sidebar (task detail, worker detail), group related fields into **sections** — inset panels with a subtle elevated background.

```
Section background:  rgba(255, 255, 255, 0.03) — barely lighter than parent card
Section padding:     12px
Section border-radius: var(--radius)
Within-section gap:  12px between fields
Between-section gap: 12px (card gap)
Label-value gap:     4-6px (label directly above value)
Timestamps:          Horizontal row inside a section, relative format (timeAgo), CSS tooltip for exact date
```

**Grouping:** Split fields into logical sections (e.g., Status+Priority | Assigned+Project | Dates). This creates visual structure through the card-in-card depth effect — the sections are one shade lighter than the sidebar card, following the dark-theme elevation principle (closer to user = lighter).

**Why `rgba(255,255,255,0.03)` instead of a token?** The effect needs to be *relative* to the parent surface, not an absolute step. A fixed token like `--surface-raised` may be too strong when nested inside a `--surface` card. The transparent white overlay adapts to any parent.

### Page Structure

Every page follows the same visual rhythm:

```
Page title + action buttons (top row)
  ↓  16px gap
Filter bar or tab bar (if applicable)
  ↓  16px gap
Main content (cards, tables, detail panels)
```

### Detail Pages (two-column)

Entity detail pages (task, worker, project) use a two-column layout:

```
┌─────────────────────────────┬──────────────────┐
│  Main content (flex: 1)     │  Sidebar (320px)  │
│  Description, notes,        │  Status, metadata │
│  subtasks, terminal         │  links, actions   │
└─────────────────────────────┴──────────────────┘
```

Both columns are `--surface` cards on a `--bg` page. The sidebar card uses `--surface` background, same as the main content — they're separated by the `--bg` gap between them, not by borders.

---

## Sizing Tokens

```
--radius       8px    Buttons, inputs, small cards
--radius-lg    12px   Large cards, main content panels
--radius-xl    16px   Hero containers (rare)
999px          —      Pills: tags, badges, sidebar links (literal, not a token)
```

---

## Accessibility

### Contrast

- Primary text (`--text-primary`) on `--bg`: ~13:1 — exceeds WCAG AAA (7:1).
- Secondary text (`--text-secondary`) on `--bg`: ~6:1 — exceeds WCAG AA (4.5:1).
- Muted text (`--text-muted`) on `--bg`: ~3.7:1 — below WCAG AA. Only for non-essential supplementary text.
- All status badge text on its own tinted background must maintain at least 4.5:1 contrast.

### Focus Management

- Every interactive element has a `:focus-visible` style.
- Focus rings use `var(--accent)` — visible on every surface level.
- Tab order follows visual reading order. No `tabindex` hacks that break natural flow.
- Modals trap focus. Escape dismisses.

### Reduced Motion

Respect `prefers-reduced-motion: reduce`:
- Disable all `transition` and `animation` properties, or reduce to opacity-only fades.
- Never rely on animation to communicate state. State changes must be visible without motion.

### Keyboard Shortcuts

Global navigation: `D` (Dashboard), `P` (Projects), `T` (Tasks), `W` (Workers), `K` (Context), `N` (Notifications). These work from any page and do not conflict with input fields (disabled when an input is focused).

---

## Anti-Patterns

Things we explicitly don't do:

1. **No decorative borders on cards.** Surface contrast is sufficient. Adding `border: 1px solid` makes the UI noisy.

2. **No invisible shadows.** Any `box-shadow` with `rgba(0,0,0, < 0.2)` on dark backgrounds is wasted CSS. Either remove it or use a technique that actually works (surface color, ring, glow).

3. **No pure black or white.** `#000000` background causes halation. `#ffffff` text causes eye strain. Use the token values.

4. **No `transition: all`.** It's slow (transitions every property including layout) and unpredictable (catches properties you didn't intend to animate).

5. **No `title` attribute for tooltips.** Native browser tooltips are unstyled, delayed, and don't work in Tauri. Use `data-tooltip` + CSS `::after`.

6. **No `window.confirm()` / `window.alert()`.** Blocked in Tauri webview. Use `<ConfirmPopover>`.

7. **No bold (700) headings.** Weight 600 is the maximum. Dark backgrounds amplify perceived weight.

8. **No color without meaning.** If a UI element is colored, it must correspond to a semantic status. Decorative color creates visual noise and dilutes the signal of status colors.

9. **No hardcoded color values.** Every color must come from a CSS variable. Hardcoded hex values in component CSS files bypass theming and create maintenance debt.

10. **No font weight below 300.** Thin text on dark backgrounds becomes unreadable as it bleeds into the background.

11. **No missing `text-decoration: none` on hover for styled links.** Any `<a>` or `<Link>` with custom styling (background, padding, border-radius) must override the global `a:hover { text-decoration: underline }` in its `:hover` rule. The global rule has higher specificity than a base class selector, so the override must be on `:hover` specifically.
