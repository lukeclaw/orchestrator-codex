# 016 — Light Mode Color Consistency

## Bug

Light mode had three categories of color issues that were missed across multiple "audit" passes:

1. **Waiting badge/filter/border color mismatch**: The filter dot used `--status-waiting` (dark `#9a6700`), badge background used `--yellow-rgb` (vivid `234, 179, 8`), producing visually different hues — brown dot next to golden tint.

2. **Yellow reads as brown, not amber**: `--yellow: #9a6700` (HSL 40deg, 100%, 30%) reads as dark brown on white backgrounds. Users expect "waiting/warning" to feel warm amber/orange, not brown.

3. **Progress bar fills too dark**: Segments used `var(--green)` and `var(--accent)` at full opacity — the same dark text-contrast colors (#1a7f37, #0969da) used as solid background fills, appearing heavy/dark.

4. **Base `--*-rgb` values were identical to dark text colors**: `rgba(154, 103, 0, 0.15)` on white produces muddy beige, not yellow. All background tints were barely visible.

## Root Cause

The light mode color system was designed with a single set of dark values for everything — text, borders, dots, badge backgrounds. In dark mode this works because the base colors are already bright (`--green: #3fb950`). In light mode, the base colors are dark for text contrast (`--green: #1a7f37`), but the same dark values were used for background tints and fills where they look muddy.

## Fix

Introduced a **two-tier color system** for light mode:

- **Tier 1 (hex variables)**: Dark values for text contrast. `--green: #1a7f37`, `--accent: #0969da`, `--yellow: #b45309`.
- **Tier 2 (RGB variables)**: Vivid values for background tints. `--green-rgb: 34, 197, 94`, `--accent-rgb: 56, 139, 253`, `--yellow-rgb: 245, 158, 11`.
- **Progress bar fills**: Added `[data-theme="light"]` overrides using brighter colors (`--green-muted`, `#218bff`, `#e5534b`) since full-opacity fills need mid-range brightness, not text-dark values.
- **Yellow → amber shift**: Changed `--yellow` from `#9a6700` (HSL 40deg, brown) to `#b45309` (HSL 25deg, warm amber) so filter dots, card borders, and badge text all feel consistently warm/orange.

## Rules

1. **Never use text-contrast colors for background fills at full opacity.** In light mode, text colors are deliberately dark (~4.5:1 contrast on white). Solid fills (progress bars, charts, indicators) need brighter mid-range values. Add `[data-theme="light"]` overrides.

2. **RGB variables must diverge from hex variables in light mode.** `--green-rgb` is NOT the decomposition of `--green` — it's a vivid color optimized for `rgba()` at fractional opacity. Document this divergence clearly.

3. **Test color consistency by comparing related elements.** When auditing, check that semantically related elements (filter dot, card border, badge text, badge background) produce a consistent visual hue. Programmatic verification: `getComputedStyle(el).backgroundColor` across all instances of a status.

4. **Hue matters, not just brightness.** `#9a6700` (hue 40deg) reads as brown, not yellow/amber. For "warning/waiting" states, target hue 25-30deg (warm amber/orange) for a warm feel that users recognize as caution. Reference: Tailwind amber-700 `#b45309`.

## How the Audit Failed

Three passes declared "no issues" because the evaluation was:
- **Screenshot-level, not component-level**: Glancing at full-page screenshots instead of zooming into specific elements and comparing computed values.
- **Existence-checking, not consistency-checking**: "The badge renders and has color" is not the same as "the badge color matches the filter dot and card border."
- **Missing cross-element comparison**: Never compared `getComputedStyle()` of related elements side by side.

### Better audit process

1. For each status color, trace the variable chain: `--status-waiting` → `--yellow` → hex value, AND `--yellow-rgb` → RGB value. Verify the hue relationship.
2. Use `page.evaluate()` to extract and compare computed styles of related elements programmatically.
3. Check full-opacity usage (progress bars, chart colors, border colors) separately from low-opacity usage (badge tints, gradients) — they have different brightness requirements.
4. Compare with reference design systems (GitHub Primer, Tailwind) for hue/saturation calibration.
