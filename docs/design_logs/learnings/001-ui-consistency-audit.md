# UI Consistency Audit — Learnings

Lessons learned from a comprehensive UI consistency pass across the Orchestrator frontend.

## CSS Border Shorthand vs Longhand Specificity

When a component uses `border-left-color` for semantic meaning (e.g., worker card status colors), never override it with the `border-color` shorthand — the shorthand sets all four sides and silently destroys the left border color.

**Wrong:**
```css
.pd-worker-grid .worker-card {
  border-color: var(--border);  /* overrides status-specific border-left-color */
}
```

**Right:**
```css
.pd-worker-grid .worker-card {
  border-top-color: var(--border);
  border-right-color: var(--border);
  border-bottom-color: var(--border);
  /* border-left-color preserved from base .worker-card status rules */
}
```

Same applies to hover states — use individual `border-top/right/bottom-color` to add hover accents without clobbering the status left border.

## Surface Hierarchy for Nested Components

When a component (e.g., worker card) is placed inside a section that shares the same background token, it visually disappears. Fix by stepping down one level in the surface hierarchy:

| Container       | Content          | Result        |
|-----------------|------------------|---------------|
| `--surface`     | `--surface`      | No contrast   |
| `--surface`     | `--bg`           | Recessed look |

For worker cards on the project page: card body uses `var(--bg)`, header/footer use `var(--surface-hover)` to create visible structure within the recessed card.

## Hover Brightening on Overlay Surfaces

Standard surface tokens (`--surface-hover`) can appear *darker* than `--surface-overlay` backgrounds (e.g., dropdown menus). Use additive white overlay instead:

```css
.dropdown-item:hover {
  background: rgba(255, 255, 255, 0.06);  /* always brightens, regardless of base */
}
```

This works universally because it's additive light — it makes any background slightly brighter rather than jumping to a fixed token that may be darker.

## Dropdown Menu Items Not Stretching Full Width

Button elements inside a dropdown don't stretch to full width by default. Fix by adding flex column layout to the dropdown container:

```css
.dropdown-container {
  display: flex;
  flex-direction: column;
}
```

## No Nested Confirmations in Dropdown Menus

A dropdown menu is already a deliberate user action (click kebab, then click item). Wrapping dropdown items in `ConfirmPopover` creates a double-confirmation pattern — a popover inside a popover — which is awkward and visually broken. Destructive dropdown actions should execute directly on click.

Reserve `ConfirmPopover` for inline buttons where a single click could trigger an irreversible action without any prior deliberate step.

## Hardcoded Colors Drift Over Time

Hex values like `#58a6ff`, `#3fb950`, `#f85149` were scattered across components instead of using CSS variables (`--accent`, `--green`, `--red`). These silently diverge if the design system palette changes. Always use CSS variables for semantic colors — even in `rgba()` backgrounds where the variable resolves to the same hex today.

## `border-radius: 10px` vs `999px` for Pill Badges

Use `999px` (not `10px`) for pill-shaped badges. The `10px` value only creates a pill shape at certain sizes — if the badge grows taller, it becomes a rounded rectangle instead of staying fully rounded. `999px` guarantees a pill regardless of content height.

## `transition: all` is an Anti-Pattern

Avoid `transition: all` — it transitions every property including `z-index`, `visibility`, `opacity` from ancestor changes, and layout properties. Be explicit:

```css
/* Wrong */
transition: all 0.15s;

/* Right */
transition: background-color 0.15s, border-color 0.15s;
```

## `title` Attribute Doesn't Work in Tauri

Native browser tooltips (`title="..."`) don't render in the Tauri webview. Use `data-tooltip="..."` with a custom CSS tooltip system instead. This is documented in CLAUDE.md but easy to miss when copy-pasting patterns.

## Spacing Should Follow an 8px Grid

Random spacing values (5px, 10px, 15px, 20px) create visual inconsistency. The design system uses an 8px grid: `4 / 8 / 12 / 16 / 24 / 32 / 48px`. When auditing spacing, round to the nearest grid value.
