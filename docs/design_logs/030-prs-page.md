# PRs Page — Top-Level PR Dashboard

## 1. User Story

The operator manages 5-15 parallel Claude Code workers from a single dashboard. Workers produce pull requests. When the operator visits the PRs page, they are asking one question: **"What's the status of my PRs, and which ones need my attention right now?"**

Concretely, they need to answer — in order of urgency:

1. **Which PRs are on fire?** CI is failing or a reviewer requested changes. I need to route a worker to fix it, or read the feedback myself.
2. **Which PRs are ready to ship?** Approved and CI is green. I can merge them immediately — quick wins.
3. **Which PRs are waiting on others?** Reviewers haven't responded yet, or CI is still running. Nothing for me to do — just awareness.
4. **Which PRs are parked?** Drafts, not yet ready for review. Low priority but I want to see them.
5. **What's the connection back to my tasks and workers?** When I spot a PR that needs action, I want to jump to the worker terminal and give it instructions, or to the task to update status.

This is an **attention-based** mental model, not a state-based one. The user doesn't think "show me open PRs" — they think "show me what needs my attention." The page design must reflect this.

### What the user does NOT need here

- Full PR diffs or code review (that's GitHub's job)
- Detailed CI log output (that's GitHub Actions' job)
- PR creation or editing (workers create PRs; the operator reviews and merges)

The PRs page is a **triage dashboard**: scan, prioritize, act or delegate.

---

## 2. Attention Model

Every open PR has an **attention level** computed from its review and CI state. This is the organizing principle of the entire page — it drives the accent bar color, the default sort order, and the sidebar badge count.

### Inputs

From the GitHub GraphQL API, per PR:

| Field | Source | Purpose |
|-------|--------|---------|
| `reviewDecision` | `APPROVED`, `CHANGES_REQUESTED`, `REVIEW_REQUIRED`, or null | Review summary |
| `statusCheckRollup.state` | `SUCCESS`, `FAILURE`, `PENDING`, `ERROR`, `EXPECTED` | CI summary |
| `isDraft` | boolean | Draft state |
| `autoMergeRequest` | present or null | Auto-merge enabled |
| `reviewRequests` | list of pending reviewer logins | Who hasn't reviewed yet |

### Levels

| Level | Name | Accent Color | Condition | User Action |
|-------|------|-------------|-----------|-------------|
| 1 | **Needs action** | `--red` | CI failing/error OR changes requested | Fix CI, address feedback |
| 2 | **Ready to ship** | `--green` | Approved AND CI passing AND not draft | Merge or enable auto-merge |
| 3 | **In review** | `--accent` (blue) | Not draft, has pending reviewers or CI running | Wait (or nudge reviewer) |
| 4 | **Draft** | `--text-muted` (gray) | isDraft = true | Continue working, or mark ready |

If a PR doesn't match any of 1-3 and isn't a draft (e.g., open with no reviewers requested and CI passing but not approved), it falls into level 3 as a default.

### Default Sort

Primary: attention level ascending (red first, then green, then blue, then gray).
Secondary: `updated_at` descending within each level.

This puts "needs action" PRs at the top of the page where the user's eyes land first.

### Sidebar Badge

```
badge_count = PRs at attention level 1 (needs action)
```

Displayed as a warning-variant badge on the PRs sidebar item. Only shown when count > 0. This gives the user a reason to visit the page without having to navigate to it.

---

## 3. Page Layout

### Header Row

```
PRs    [Active | Recent]    [7 days v]    ...    [fetched 2m ago] [refresh]
```

- **Title**: "PRs" — consistent with other page titles (20px, weight 600)
- **Tabs**: `SlidingTabs` component, two tabs: Active (default) and Recent
- **Days dropdown**: Only visible on the Recent tab. Uses `.form-group select` styling per design conventions. Options: 7 / 14 / 30 days.
- **Fetched timestamp**: Muted text showing when data was last fetched, relative format (`timeAgo`). Tooltip shows absolute time.
- **Refresh button**: Icon button, spins while loading.

### Filter Bar

Below the header, a horizontal bar with filter pills and an inline search input.

**Active tab pills:**

```
[*] Needs action (3)    [*] Ready to ship (2)    [*] In review (5)    [*] Draft (1)    [___Filter...___]
```

Each pill has a colored dot matching its attention level color. The count is derived from the PR list. Clicking a pill filters the table to that attention level. "All" is implicit — clicking the active pill again deselects it and shows all.

This replaces the current "All | Open | Draft" pills, which organize by GitHub state rather than user intent.

**Recent tab pills:**

```
[*] All (14)    [*] Merged (12)    [*] Closed (2)    [___Filter...___]
```

For the Recent tab, state-based filtering makes sense because there's no urgency model — these PRs are done.

**Inline search**: Filters by repo name, PR title, or `#number`. Same capsule-shaped input as current implementation.

### Table

The main content area. Four columns with sortable headers.

---

## 4. Row Design

Each table row represents one PR. The row communicates identity, urgency, status details, linkage, and freshness — all without expanding.

### Column Layout

```
| [accent] PR                          | Status                      | Task / Worker       | Updated |
|--------------------------------------|-----------------------------|---------------------|---------|
| [3px]  repo #123 · +45 -12          | Approved · CI passing       | TSK-1               |  2h ago |
|        Fix the widget alignment...   |                             | ember-cli-checkout  |         |
```

#### Column 1: PR (flex, min-width ~300px)

Two-line cell with a 3px left accent bar colored by attention level.

- **Line 1** (12px, `--text-secondary`): `repo-short #number` as a clickable link (opens GitHub via `openUrl()`), followed by a compact size indicator: `+45 -12` with green/red coloring. The size gives an instant sense of PR complexity.
- **Line 2** (13px, `--text-primary`): PR title, truncated with ellipsis. This is the main readable content.

The accent bar is the **primary visual scanning signal**. The user can glance down the left edge of the table and instantly see: red, red, green, blue, blue, blue, gray — "two things need attention, one is ready to ship, three are in review, one draft."

#### Column 2: Status (~200px)

Compact inline chips showing the specific review and CI signals. These explain *why* the accent bar is the color it is.

**Review chip** (always shown for non-draft open PRs):

| State | Chip | Color |
|-------|------|-------|
| Approved | `Approved` | `--green` |
| Changes requested | `Changes requested` | `--red` |
| Review pending | `Pending review` | `--yellow` |
| No reviewers | `No reviewers` | `--text-muted` |

**CI chip** (shown when checks exist):

| State | Chip | Color |
|-------|------|-------|
| All passing | `CI passing` | `--green` |
| Any failing | `CI failing` | `--red` |
| Running | `CI running` | `--yellow` |

**Additional chips** (when applicable):

- `Auto-merge` in `--accent` — auto-merge is enabled
- `Draft` in `--text-muted` — shown instead of review/CI chips for drafts

Chips are separated by a middle-dot (`·`) to keep them visually tight. Color carries the meaning; the text is for specificity.

For the **Recent tab** (merged/closed PRs), the status column shows:
- `Merged` (purple) or `Closed` (red)
- `by @username` in muted text

#### Column 3: Task / Worker (~160px)

Two-line cell connecting the PR back to the orchestrator's task and worker system.

- **Line 1**: Task key as a pill link (e.g., `TSK-1`), styled with `--accent-muted` background. Links to `/tasks/:id`. If no linked task, show `--` in muted.
- **Line 2** (11px, `--text-muted`): Worker name as a subtle text link. Links to `/workers/:id`. If no linked worker, omit.

The task link is primary (the organizational unit), the worker link is secondary (the execution unit). Both are clickable and stop event propagation so they don't trigger row expansion.

#### Column 4: Updated (~80px)

Relative time via `timeAgo()`. Tooltip shows absolute date. Styled in `--text-secondary`, 12px.

### Row Interaction

- **Hover**: `background: var(--surface-hover)`, accent bar transitions to slightly brighter.
- **Click**: Toggles the expanded detail view below the row.
- **Expanded state**: Row background stays at `--surface-hover`, accent bar stays highlighted.

### Skeleton Loading

During Phase 1 fetch: 5 skeleton rows matching the column layout. Each cell has a pulsing bar at the right width. The accent bar area is a thin vertical skeleton strip.

### Empty States

- **No PRs (Active)**: Icon + "No open pull requests" — clean and clear.
- **No PRs (Recent)**: Icon + "No recently closed pull requests"
- **Auth error (401)**: Icon + "GitHub CLI not authenticated" + hint to run `gh auth login`
- **Filtered to zero**: "No PRs match this filter" with a clear-filter link.

---

## 5. Expanded Row — Detail View

Clicking a row reveals a detail panel inline below it (`<td colSpan={4}>`). This panel shows information that **doesn't fit in the table row** — review comment threads, file changes, and actions.

### What It Shows

The expanded view does NOT repeat information already visible in the table row (state, CI summary, review decision). It shows the *next level of detail*:

1. **Review comment threads** — The main reason to expand. Shows each reviewer's comments grouped by file, with inline code suggestions rendered as diffs. Replies shown nested. This is the existing `PrPreviewCard` review section.

2. **Changed files list** — Compact file list with per-file `+N -M` stats. Helps the user understand the scope of changes without leaving the dashboard.

3. **Action buttons** — Contextual actions based on PR state:
   - Draft PRs: "Mark ready for review" button (with confirmation)
   - Open non-draft PRs: Auto-merge toggle switch
   - All PRs: "Open in GitHub" link button

### Data Fetching

The expanded view calls `GET /api/pr-preview?url=...` to fetch full detail (reviews with comment threads, files, checks). This is the existing detailed fetch — expensive but only triggered on expand. A loading skeleton shows while fetching.

The `initialData` prop is NOT used from the table's summary data because the summary intentionally lacks comment threads and file details. The expanded view always fetches fresh detail.

### Visual Integration

The expanded panel uses `background: var(--bg)` (recessed relative to the table's `--surface`) to create a clear visual inset. No card border — the recessed background and the row above/below provide containment. Padding: 16px. Animated entrance: 200ms opacity + max-height ease-out.

---

## 6. Data Architecture

### Single GraphQL Query (Phase 1 — instant table)

**Endpoint**: `GET /api/prs?tab=active` or `GET /api/prs?tab=recent&days=7`

One GraphQL call returns both the PR list AND enough status detail to populate the full table without a Phase 2 batch fetch. This eliminates the two-phase loading pattern and gives instant, complete rows.

**Enhanced GraphQL query:**

```graphql
query($q: String!) {
  search(query: $q, type: ISSUE, first: 100) {
    nodes {
      ... on PullRequest {
        url
        number
        title
        state
        isDraft
        author { login }
        createdAt
        updatedAt
        closedAt
        mergedAt
        mergedBy { login }
        additions
        deletions
        changedFiles
        repository { nameWithOwner }
        autoMergeRequest { enabledAt }
        reviewDecision
        reviewRequests(first: 10) {
          nodes {
            requestedReviewer {
              ... on User { login }
              ... on Team { name }
            }
          }
        }
        commits(last: 1) {
          nodes {
            commit {
              statusCheckRollup {
                state
              }
            }
          }
        }
      }
    }
  }
}
```

New fields vs. current implementation:
- `reviewDecision` — single enum summarizing review state (APPROVED, CHANGES_REQUESTED, REVIEW_REQUIRED, null). No need to fetch individual reviews for the table summary.
- `reviewRequests` — list of pending reviewers. Shows who the PR is waiting on.

Both are lightweight fields that don't add significant query cost.

**Response shape:**

```json
{
  "prs": [
    {
      "url": "https://github.com/org/repo/pull/42",
      "repo": "org/repo",
      "number": 42,
      "title": "Fix widget alignment",
      "state": "open",
      "draft": false,
      "author": "yuqiu",
      "created_at": "2026-03-10T...",
      "updated_at": "2026-03-13T...",
      "closed_at": null,
      "merged_at": null,
      "additions": 45,
      "deletions": 12,
      "changed_files": 3,
      "review_decision": "approved",
      "review_requests": ["reviewer1", "reviewer2"],
      "auto_merge": true,
      "ci_state": "success",
      "attention_level": 2,
      "linked_task": { "id": "...", "task_key": "TSK-1", "title": "Fix layout" },
      "linked_worker": { "id": "...", "name": "ember-cli-checkout" }
    }
  ]
}
```

Key additions vs. current:
- `review_decision`: "approved" | "changes_requested" | "review_required" | null
- `review_requests`: list of pending reviewer logins
- `auto_merge`: boolean (derived from autoMergeRequest presence)
- `ci_state`: "success" | "failure" | "pending" | null (derived from statusCheckRollup)
- `attention_level`: 1-4, computed server-side (see Attention Model)
- `additions`, `deletions`, `changed_files`: already fetched, now included in response

The `details` map from the current response is removed. All table-level data is in the `prs` list directly. The expanded view fetches its own detail via the existing `GET /api/pr-preview` endpoint.

### Task/Worker Cross-Reference

Same as current implementation: scan all tasks for links matching PR URLs, resolve task keys and assigned workers. No changes needed.

### Caching

- **Backend**: In-memory cache keyed by `"active"` or `"recent:{days}"`. TTL: 10 minutes. Bypass on `?refresh=true`.
- **Frontend**: `useRef` cache keyed by `cacheKey`. TTL: 10 minutes. Bypass on manual refresh.
- **Expanded view**: Uses the existing `pr_preview` cache (TTL: 2 min open, 10 min closed).

---

## 7. Frontend Types

```typescript
interface PrSearchItem {
  url: string
  repo: string
  number: number
  title: string
  state: 'open' | 'closed'
  draft: boolean
  author: string
  created_at: string
  updated_at: string
  closed_at: string | null
  merged_at: string | null
  additions: number
  deletions: number
  changed_files: number
  review_decision: 'approved' | 'changes_requested' | 'review_required' | null
  review_requests: string[]
  auto_merge: boolean
  ci_state: 'success' | 'failure' | 'pending' | null
  attention_level: 1 | 2 | 3 | 4
  linked_task: { id: string; task_key: string | null; title: string } | null
  linked_worker: { id: string; name: string } | null
}

interface PrSearchResponse {
  prs: PrSearchItem[]
}
```

The `PrCISummary` and `PrBatchResponse` types are no longer needed — the batch endpoint is eliminated.

---

## 8. Active Tab vs. Recent Tab

### Active Tab (default)

Shows all open PRs (including drafts) authored by `@me`.

- Filter pills: Needs action | Ready to ship | In review | Draft (with counts and colored dots)
- Default sort: attention level ascending, then updated desc
- All columns active

### Recent Tab

Shows merged/closed PRs within a configurable time window.

- Filter pills: All | Merged | Closed (with counts)
- Default sort: closed/merged date descending
- Status column shows Merged/Closed state + "by @username"
- No attention model (these PRs are done)
- Days dropdown: 7 (default) | 14 | 30

---

## 9. Implementation Plan

### Backend Changes

1. **`orchestrator/api/routes/prs.py`**: Update `_GRAPHQL_QUERY` to add `reviewDecision`, `reviewRequests`. Update `_parse_graphql_prs` to extract the new fields and compute `attention_level`. Include `additions`, `deletions`, `changed_files`, `auto_merge`, `ci_state` in the PR response objects. Remove the `details` map from the response.

2. **`orchestrator/api/routes/pr_preview.py`**: No changes — the expanded view still uses the existing single-PR detail fetch.

### Frontend Changes

1. **`frontend/src/api/types.ts`**: Update `PrSearchItem` to include new fields. Update `PrSearchResponse` to remove `details`. Remove `PrCISummary`.

2. **`frontend/src/pages/PRsPage.tsx`**: Rewrite the page component:
   - Replace sub-filter pills with attention-level pills (Active tab)
   - Rewrite table columns: PR (with accent bar + size), Status (review + CI chips), Task/Worker (two-line), Updated
   - Add attention-level-based accent bars to rows
   - Change default sort to attention level
   - Update `computeBadge` to use `attention_level === 1`
   - Remove `details` state and batch fetch logic
   - Pass only `url` to expanded `PrPreviewCard` (no initialData from summary)

3. **`frontend/src/pages/PRsPage.css`**: Rewrite styles for new row design, accent bars, status chips, task/worker column.

4. **`frontend/src/components/tasks/PrPreviewCard.tsx`**: No changes needed — it handles its own fetching and display.

### Files Changed

| File | Change |
|------|--------|
| `orchestrator/api/routes/prs.py` | Update GraphQL query, response shape, add attention_level |
| `frontend/src/api/types.ts` | Update PrSearchItem, PrSearchResponse |
| `frontend/src/pages/PRsPage.tsx` | Rewrite page component |
| `frontend/src/pages/PRsPage.css` | Rewrite styles |
| `tests/unit/test_prs.py` | Update tests for new response shape |

### Files NOT Changed

| File | Reason |
|------|--------|
| `orchestrator/api/routes/pr_preview.py` | Expanded view still uses existing endpoint |
| `frontend/src/components/tasks/PrPreviewCard.tsx` | Works as-is for expanded view |
| `frontend/src/components/tasks/prUtils.ts` | Still used by TaskLinksCard |

---

## 10. Edge Cases

- **`gh` not authenticated**: Return 401, frontend shows auth empty state with `gh auth login` hint.
- **No PRs found**: Clean empty state per tab.
- **Rate limit (429)**: Return cached results if available, or surface error.
- **Same PR linked to multiple tasks**: Pick the most recently updated task.
- **User navigates away mid-fetch**: AbortController cancels the request.
- **Tab switch while fetch is in-flight**: Abort old fetch, start fresh.
- **PR with no CI checks**: `ci_state` is null, CI chip not shown, doesn't affect attention level (won't be "ready to ship" since CI isn't confirmed passing).
- **PR with no reviewers requested**: `review_decision` is null, `review_requests` is empty. Shows "No reviewers" chip. Attention level defaults to 3 (in review).
- **GraphQL returns >100 PRs**: Unlikely for a single author's open PRs. Cap at 100 for now.
