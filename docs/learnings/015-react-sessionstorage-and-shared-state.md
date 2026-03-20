# React: sessionStorage Is Not Reactive & Hook Instances Are Not Shared

**Date**: 2026-03-19
**Related**: Phase 2 — Preserve Filters on Navigation

## Mistake 1: useMemo with sessionStorage — Missing Invalidation Trigger

`SidebarItem` used `useMemo` to build a URL from sessionStorage:

```tsx
const effectiveTo = useMemo(() => {
  const saved = getPageFilters(to)  // reads sessionStorage
  return saved ? `${to}?${saved}` : to
}, [to, preserveFilters])  // BUG: neither dep changes when storage updates
```

sessionStorage is external to React — writes to it don't trigger re-renders. The memo was computed once on mount and never recomputed, even though `FilterSync` was updating sessionStorage on every navigation.

**Fix**: Add `location.pathname` and `location.search` as dependencies. Navigation changes location (which is React state via the router), which triggers a re-render, which causes the memo to recompute and read the now-updated sessionStorage.

**Rule**: When `useMemo`/`useEffect` reads from an external store (sessionStorage, localStorage, module-level variables), you must include a React-tracked value that changes in sync with that store as a dependency. The external store itself cannot trigger React updates.

## Mistake 2: Plain Hook Creates Independent State Per Component

`useSettings()` was a plain custom hook (useState + useEffect + fetch). Both Sidebar and SettingsPage called it, creating two independent copies of settings state. When SettingsPage saved a new value and re-fetched, the Sidebar's copy was stale.

**Fix**: Converted `useSettings` to a React Context (`SettingsProvider`) so all consumers share a single state instance. When any consumer calls `save()`, the shared state updates and all consumers re-render.

**Rule**: If multiple components need to read AND react to changes in the same server-side data, use a Context (or state management library), not a plain hook. A plain hook is fine when only one component uses it, or when each component independently fetching is acceptable.

## Mistake 3: Derived State From Async Loading Flag

```tsx
const preserveFilters = !loading && Boolean(getValue('ui.preserve_filters'))
```

When `save()` triggered a re-fetch, `loading` flipped to `true`, making `preserveFilters` momentarily `false`. The toggle handler read `!preserveFilters` = `!false` = `true` — saving the same value back (no-op). The toggle appeared to do nothing.

**Fix**: Use optimistic local state (`useState` + `setState` before `save()`), same pattern as the existing `claudeUpdateBeforeStart` toggle. The UI updates immediately; the async save happens in the background.

**Rule**: Toggle/switch UI should use optimistic local state, not values derived from async-loading flags. The pattern is: `setState(newValue)` then `await save(newValue)`. Never derive toggle state from `!loading && getValue(...)`.
