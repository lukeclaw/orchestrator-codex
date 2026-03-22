# 022 — UI Design Principles

The visual design language for Orchestrator. This document is the source of truth for how the UI should look and feel — not a description of what currently exists, but what we build toward. Reference this when improving existing pages and building new ones.

## Identity

Orchestrator is a control center for engineers managing parallel AI workers. It should feel like a professional instrument panel — calm, information-dense, and precise. Think Linear meets a flight deck: everything has a purpose, nothing is decorative for its own sake. It supports dark, light, and system-follow themes.

**One-line summary (dark):** Calm, borderless. Depth through shade, clarity through spacing, meaning through color.
**One-line summary (light):** Gray canvas, white cards, shadow-driven depth. Same information hierarchy, different physics.

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

## Light Mode

### Philosophy

Light mode is not an inversion of dark mode — it is a parallel design system that shares the same values (information density, calm under complexity, instant legibility) but uses fundamentally different depth and elevation techniques. Where dark mode builds depth by making surfaces *lighter*, light mode builds depth through *shadows and surface contrast against a tinted page background*.

**One-line summary:** Gray canvas, white cards, shadow-driven depth. Same information hierarchy, different physics.

### The Fundamental Shift

In dark mode, the eye perceives depth as "lighter = closer." A `--surface` card (#161b22) pops against `--bg` (#0d1117) purely through luminance contrast. Shadows are nearly invisible because the background is already near-black.

In light mode, the relationship inverts:

- **Shadows become the primary depth mechanism.** A white card on a light gray page is only distinguishable through shadow — without it, the card looks stamped onto the page like printed paper.
- **Surface contrast reverses.** The page background is *darker* than cards: `--bg: #f0f2f5` (gray canvas) with `--surface: #ffffff` (white cards). This gray-canvas-white-card pattern is the industry standard (GitHub, Linear, Notion, Apple) because it provides just enough contrast for cards to register as floating layers.
- **Borders become useful again.** Dark mode suppresses decorative borders because surface contrast is sufficient. Light mode benefits from a subtle `1px solid var(--border-subtle)` on cards — it defines the card edge crisply where shadow alone might feel soft or ambiguous.

### Surface Hierarchy (Light Mode)

```
Level 0  --bg              #f0f2f5   Page canvas — neutral gray, never pure white
Level 1  --surface         #ffffff   Cards, panels, modals — true white, max contrast with canvas
Level 2  --surface-inset   #f6f8fa   Recessed areas inside cards (code blocks, sidebar sections)
Level 3  --surface-hover   #f3f5f7   Hovered interactive surfaces
Level 4  --surface-raised  #eaeef2   Raised controls (tab bar background, button surfaces)
Level 5  --surface-overlay #ffffff   Floating: dropdowns, tooltips — white with strong shadow
```

**Key difference from dark mode:** In dark mode, floating elements are *lighter* than cards. In light mode, floating elements are the same white as cards but differentiated by stronger shadow (`--shadow-float`). The overlay surface doesn't need to be a different shade — shadow does all the work.

**Why not pure white for the page?** A `#ffffff` page with `#ffffff` cards creates zero surface contrast. The only way to separate them would be heavy borders or aggressive shadows, both of which feel noisy. The gray canvas (`#f0f2f5`) provides ~3% luminance contrast with white cards — subtle but sufficient when combined with shadow.

### Depth Model (Light Mode)

Light mode uses three primary depth techniques, all of which were ineffective or unnecessary in dark mode:

**1. Box shadows (primary technique)**

Light backgrounds give shadows maximum dynamic range. A `rgba(0,0,0,0.08)` shadow on white is clearly visible, whereas the same shadow on `#0d1117` is imperceptible. Every elevated element — cards, stats, popovers — must have a shadow.

```
Cards/panels:     var(--shadow-sm)    Subtle lift off canvas
Hovering cards:   var(--shadow-md)    Increased elevation on hover (optional)
Dropdowns:        var(--shadow-float) Shadow + border ring
Modals:           var(--shadow-lg)    Maximum elevation
```

**2. Borders — sparingly**

Borders are a supporting technique in light mode, not a primary one. Shadow provides edge definition for most cards. Adding borders to every card makes the UI feel heavy and "wireframed" — the opposite of the clean, shadow-driven look we want (Linear-style).

**Rule:** Only large structural containers (`.panel`, `.tb-column`) get `border: 1px solid var(--border-subtle)` in light mode. Individual cards (project cards, worker cards, task cards, notification cards, skill cards, stat bars) rely on shadow alone — the shadow blur naturally defines the card edge against the gray canvas. Floating elements (modals, dropdowns) get `--shadow-float` which includes a ring via `border-muted`.

**3. Surface color contrast**

Still useful but no longer sufficient alone. A white card on a gray canvas has visible contrast, but without shadow it looks *flat* — like a window into a lighter space, not a floating element. Surface contrast is the foundation; shadow is the finish.

### Color Adjustments

Status colors shift to deeper, richer variants to maintain contrast against light backgrounds. Material Design recommends *desaturating* colors on dark backgrounds because bright saturated colors vibrate on near-black surfaces. We intentionally keep dark mode colors saturated (#3fb950, #58a6ff) because status indicators in a dense information UI need to pop — this is a conscious departure from Material guidance. For light mode, colors shift to deeper/darker values (following GitHub's Primer system) because bright colors wash out on white:

```
                Dark Mode        Light Mode       Reason
Green           #3fb950          #1a7f37          Bright green washes out on white
Yellow          #d29922          #9a6700          Deep amber reads better on light
Red             #f85149          #d1242f          Vivid red → rich crimson
Orange          #db6d28          #bc4c00          Deeper for contrast
Purple          #a371f7          #8250df          More saturated
Accent (blue)   #58a6ff          #0969da          Bright sky → deep ocean
```

**Text contrast targets remain identical** — the light palette is chosen so that `--text-primary` (#1f2328) on `--surface` (#ffffff) yields ~16:1, and `--text-secondary` (#656d76) yields ~5.7:1.

### Status Tints in Light Mode

The `rgba(color, 0.15)` badge pattern works differently on white backgrounds. On dark backgrounds, the tint is applied over near-black — the color appears muted and dark. On white backgrounds, the same opacity produces a lighter, more pastel appearance.

**This is desirable.** Light mode badges should look like soft watercolor washes, not bold fills. The 0.10-0.15 opacity range produces the right effect: visible, semantic, but not aggressive.

### Two-Tier Color System (Light Mode)

Light mode uses **two tiers of color values** for different purposes:

**Tier 1 — Base colors** (`--green`, `--red`, `--yellow`, `--accent`, etc.): Deep, dark values optimized for **text contrast** on white. These follow GitHub Primer and must maintain ≥4.5:1 WCAG AA contrast against `#ffffff`. They're used for status text, link text, and badge foreground color.

**Tier 2 — ALL RGB values** (`--*-rgb`, `*-bright-rgb`, `*-alt-rgb`, `*-vivid-rgb`, `*-emphasis-rgb`): Vivid, saturated values optimized for **background tints** at low opacity. These are used in `rgba()` calls at 0.08-0.30 opacity for badge backgrounds, status highlights, gradients, glows, and accent fills. The base `--*-rgb` values intentionally diverge from their hex counterparts (e.g., `--green-rgb` is vivid green `34, 197, 94` while `--green` is dark `#1a7f37`).

```
                    Tier 1 (text hex)    Tier 2 (base RGB)           Tier 2+ (bright RGB)
Green               #1a7f37 (dark)       rgb(34, 197, 94) (vivid)   rgb(46, 213, 115) (brighter)
Yellow              #9a6700 (deep)       rgb(234, 179, 8) (golden)  rgb(250, 204, 21) (brighter)
Red                 #d1242f (crimson)    rgb(239, 68, 68) (bright)  rgb(248, 113, 113) (softer)
Purple              #8250df (deep)       rgb(139, 92, 246) (vivid)  rgb(192, 132, 252) (lighter)
Accent (blue)       #0969da (dark)       rgb(56, 139, 253) (bright) rgb(59, 130, 246) (similar)
Orange              #bc4c00 (deep)       rgb(249, 115, 22) (vivid)  rgb(251, 146, 60) (softer)
```

**Why diverge RGB from hex?** The hex values (`--green`, `--accent`, etc.) are used as `color:` for text — they must be dark enough for ≥4.5:1 contrast on white. The RGB values are used exclusively in `rgba()` at fractional opacities — nobody writes `rgba(var(--green-rgb), 1.0)`. Using dark source colors at low opacity produces muddy, grayish washes: `rgba(154, 103, 0, 0.15)` on white reads as dirty beige, not yellow. Vivid source colors produce clear, correctly-colored watercolor tints: `rgba(234, 179, 8, 0.15)` reads as golden yellow.

### Button Treatment

Primary buttons require different treatment because the text-on-background contrast flips:

```
Dark mode:   background: var(--green-muted) #238636   text: var(--text-primary) #e6edf3   (light on dark)
Light mode:  background: var(--green-muted) #2da44e   text: #ffffff                       (white on green)
```

Light mode primary buttons use white text on vibrant colored backgrounds. This is achieved through `--btn-primary-text: #ffffff` in the light theme block. The button background color itself shifts to a slightly brighter variant so the white text maintains >7:1 contrast.

### Hover Behavior

In dark mode, hover *lightens* surfaces (closer to user = brighter). In light mode, hover *slightly darkens* or *grays* surfaces:

```
Dark mode hover:  --surface → --surface-hover (lighter)
Light mode hover: --surface (#fff) → --surface-hover (#f3f5f7) (slightly gray)
```

For stat cards and gradient-background elements, avoid `filter: brightness(1.15)` in light mode — it washes out already-light surfaces. Use `filter: brightness(0.97)` instead, which subtly deepens the color.

### Sidebar and Header

The sidebar uses `--bg` (gray canvas) in both themes. In dark mode, this makes the sidebar the darkest element. In light mode, the sidebar becomes a gray stripe that frames the white content area — a common pattern in macOS apps (Finder, Mail, Notes).

The header also uses `--bg`, creating a unified gray chrome around the white workspace.

### Font Rendering

Light mode reverses the font rendering challenge. In dark mode, light text on dark backgrounds appears thinner (hence minimum 300 weight and antialiased rendering). In light mode, dark text on light backgrounds appears slightly heavier.

Keep `-webkit-font-smoothing: antialiased` in light mode — it produces crisper, more accurate weight rendering regardless of theme.

### Scrollbars

Light mode scrollbar thumbs use the same `rgba(var(--text-muted-rgb), 0.4)` pattern. Because `--text-muted-rgb` shifts to a darker gray in light mode (139, 148, 158 → same value), the scrollbar naturally adapts and remains visible against light surfaces.

### Terminal and Code

xterm.js and Monaco editor require separate theme objects because they don't read CSS variables. The `useTheme` hook and MutationObserver pattern syncs the CSS `data-theme` attribute to terminal/editor theme objects.

Terminal light theme: white background, dark text, GitHub Light color palette. This is the one place where we use near-white as a *surface* — terminals are traditionally self-contained viewports with their own background.

### Flash Prevention

Because theme preference is stored in the database (SQLite via settings API) and takes time to load, the page would briefly flash in the wrong theme. A synchronous `<script>` in `<head>` reads `localStorage` (which caches the last-known theme) and applies `data-theme="light"` before any CSS renders. The React `useTheme` hook later confirms or corrects this from the authoritative database value.

### Theme Transition

When switching themes, a brief 250ms transition on `background-color`, `color`, `border-color`, and `box-shadow` prevents a jarring instant flip. This is applied via `.theme-transitioning` class on `<html>`, added during the switch and removed after 300ms. The class is *not* permanent — it would cause unwanted transitions during normal interaction.

### What Not to Do in Light Mode

1. **Never use a pure white page background.** `#ffffff` for `--bg` creates zero contrast with `#ffffff` cards. Always use a tinted gray canvas.

2. **Never drop shadows on light cards.** Every card and panel must have at least `var(--shadow-sm)` in light mode. Without shadow, white-on-gray cards look printed, not floating.

3. **Never reuse dark mode shadow opacities.** Dark mode uses 0.12-0.4 because dark backgrounds absorb shadow. Light mode uses 0.06-0.14 — lower opacity but higher visibility.

4. **Never keep `rgba(255, 255, 255, 0.03)` subtle overlays.** These are dark-mode micro-elevation tricks (barely-perceptible white wash). On white surfaces they're literally invisible. Replace with `var(--surface-inset)` or `var(--surface-hover)` in light mode.

5. **Never use `filter: brightness(>1.0)` on light elements.** It washes them out toward white. Use `brightness(0.97)` to subtly deepen, or switch to background-color change.

6. **Never add borders to every card.** Borders on every card make the light UI feel heavy and wireframed. Shadow alone defines card edges (Linear-style). Reserve borders for large structural panels and adjacent containers that need crisp separation.

### Why Gray Canvas + White Cards

The `--bg: #f0f2f5` (gray) + `--surface: #ffffff` (white) pattern is the industry standard for information-dense tools (GitHub, Linear, Notion, Apple). Alternatives considered:
- **All-white page**: Zero contrast between page and cards — requires heavy borders or shadows to create any hierarchy. Feels like a blank document, not a structured tool.
- **All-gray surfaces**: Flatter, harder to parse groups at a glance. Works for minimal content apps but not for a dense control panel with 10+ cards per page.
- **Tinted backgrounds** (warm gray, blue-gray): Adds personality but competes with semantic status colors. Our UI uses color only for meaning — a tinted canvas would undermine that principle.

The gray canvas provides just enough contrast (~3% luminance) for white cards to register as floating layers when combined with shadow. It also creates a natural visual frame — the gray sidebar/header chrome wraps the white workspace, mimicking macOS's native window hierarchy.

### Depth Assignment Table (Light Mode)

| Need | Primary technique | Supporting technique |
|---|---|---|
| Card on page | Shadow (`--shadow-sm`) | White surface on gray canvas |
| Card hover | Shadow increase or surface darken (`--surface-hover`) | — |
| Floating element | Strong shadow (`--shadow-float`) + border | White surface, same as cards |
| Modal | Backdrop dim + blur + `--shadow-lg` | White surface with crisp border |
| Focus indication | Colored ring (same as dark mode) | — |
| Status emphasis | Same colored glow, slightly reduced opacity | Works well on both themes |
| Recessed content | `--surface-inset` (#f6f8fa) inside white card | Reverse of dark mode (lighter → gray) |

---

## Anti-Patterns

Things we explicitly don't do:

1. **No decorative borders on cards in dark mode.** Surface contrast is sufficient. Adding `border: 1px solid` makes the dark UI noisy. (Light mode *does* use subtle borders — see Light Mode section.)

2. **No invisible shadows.** In dark mode: `rgba(0,0,0, < 0.2)` is wasted CSS — use surface color, ring, or glow instead. In light mode: shadows are highly visible, so use the token values (`--shadow-sm` through `--shadow-lg`).

3. **No pure black or white for backgrounds.** `#000000` background causes halation in dark mode. `#ffffff` page background in light mode creates zero contrast with white cards. Use the token values (`--bg` in each theme).

4. **No `transition: all`.** It's slow (transitions every property including layout) and unpredictable (catches properties you didn't intend to animate).

5. **No `title` attribute for tooltips.** Native browser tooltips are unstyled, delayed, and don't work in Tauri. Use `data-tooltip` + CSS `::after`.

6. **No `window.confirm()` / `window.alert()`.** Blocked in Tauri webview. Use `<ConfirmPopover>`.

7. **No bold (700) headings.** Weight 600 is the maximum. Dark backgrounds amplify perceived weight.

8. **No color without meaning.** If a UI element is colored, it must correspond to a semantic status. Decorative color creates visual noise and dilutes the signal of status colors.

9. **No hardcoded color values.** Every color must come from a CSS variable. Hardcoded hex values in component CSS files bypass theming and create maintenance debt.

10. **No font weight below 300.** Thin text on dark backgrounds becomes unreadable as it bleeds into the background.

11. **No missing `text-decoration: none` on hover for styled links.** Any `<a>` or `<Link>` with custom styling (background, padding, border-radius) must override the global `a:hover { text-decoration: underline }` in its `:hover` rule. The global rule has higher specificity than a base class selector, so the override must be on `:hover` specifically.

12. **No dark-mode-only CSS.** Every component must work in both themes. Use CSS variables from `variables.css` — never hardcode hex values. When a component needs different treatment in light mode (shadow, border, background), add a `[data-theme="light"]` override block. When writing new components, test both themes before shipping.

13. **No `rgba(255, 255, 255, 0.0x)` without a light-mode fallback.** These subtle white washes create micro-elevation in dark mode but are invisible on white surfaces. Always pair them with a `[data-theme="light"]` rule using `var(--surface-inset)` or `var(--surface-hover)`.
