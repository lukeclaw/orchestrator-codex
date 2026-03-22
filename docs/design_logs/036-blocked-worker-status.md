# Blocked Worker Status

Split the overloaded `waiting` status into two distinct states so the brain can prioritize workers that need help.

## Problem

Worker status `waiting` means "Claude stopped and is at the prompt" — regardless of why. A worker parked while a PR is under review looks identical to a worker stuck on an authentication error. Both show as yellow `waiting` in the dashboard.

The brain has to read every waiting worker's terminal output to figure out if it needs help. This wastes heartbeat cycles and makes prioritization impossible at a glance — both for the brain and the human operator.

## The Split

| Status | Meaning | Brain priority | Color | Example situations |
|--------|---------|----------------|-------|--------------------|
| `waiting` | **Parked intentionally** — worker is waiting on something external and there's nothing to do | Low — check occasionally | Teal/cyan (calm — "everything is fine, just parked") | PR under review, task done pending brain confirmation, waiting for CI, scheduled for later |
| `blocked` | **Needs help** — worker cannot proceed without intervention | **High** — investigate immediately | Yellow/amber (warning — the attention signal) | Stuck on error, needs auth/credentials, can't find a solution, asking a question, missing access |

## Who Sets the Status?

### The Hook Sets `waiting` (unchanged)

The existing `update-status.sh` hook fires on Claude Code lifecycle events (`Stop`, `Notification`) and sets `waiting`. It can't distinguish *why* Claude stopped. This continues to work as the default "stopped" state.

### The Worker Escalates to `blocked`

When a worker determines it can't proceed, it explicitly reports blocked status:

```bash
# Worker calls this when it's stuck
orch-worker update --status blocked
orch-task update --notes "Waiting: need auth credentials for staging DB"
```

The worker prompt already has a convention for stating wait reasons in task notes (`"Waiting: ..."`). The change is that the worker also sets its *session status* to `blocked` when the reason is something that needs help, vs leaving it as `waiting` when it's just parked.

### The Brain Can Also Set `blocked`

During heartbeat, if the brain detects a `waiting` worker that's actually stuck (visible error, retry loop, idle too long with no progress), the brain can escalate:

```bash
orch-workers update <id> --status blocked
```

This means a worker doesn't have to be self-aware about being stuck — the brain catches it too.

## State Machine

Current transitions for `waiting`:
```
working → waiting → {working, idle, paused, disconnected}
disconnected → waiting
```

New status `blocked` with parallel transitions:
```
working → blocked       (worker detects it's stuck)
waiting → blocked       (brain detects worker is stuck)
blocked → working       (brain sends help, worker resumes)
blocked → idle          (user/brain gives up, stops worker)
blocked → paused        (user pauses)
blocked → disconnected  (connection lost)
```

Key difference: `blocked` cannot transition directly to `waiting`. Once blocked, the worker either gets unblocked (`→ working`) or is stopped (`→ idle`). This prevents the status from flip-flopping.

## Visual Design

- **Color**: Yellow/amber (`--status-blocked`). The warning color previously used by `waiting` now belongs to `blocked` — the status that actually needs attention. The old `waiting` moves to teal/cyan (calm, "parked and fine"). Red stays for errors/disconnected.
- **Badge**: Orange pill with "blocked" text, same styling pattern as other status badges.
- **Worker card**: Orange accent bar on the left edge.
- **Dashboard stats bar**: Blocked count shown alongside other statuses, highlighted as needing attention (same treatment as `waiting`).
- **Sidebar**: Blocked workers included in the attention badge count.

## Brain Heartbeat Integration

This is the biggest payoff. The heartbeat scan can now prioritize by status:

```
1. FIRST: handle blocked workers → run /unblock investigation
2. THEN: handle waiting workers → check if PR merged, nudge if idle too long
3. LAST: handle working workers → skip unless error visible
```

No more reading terminal output for every `waiting` worker just to figure out if it needs help. The status itself tells the brain what to do.

## Trends & Worker-Hours

Blocked time does **not** count as active work-hours. The worker is stuck, not productive. This requires no code change — `blocked` is not `working`, so `query_worker_hours()` in `status_events.py` automatically ends work-hour intervals when status transitions to `blocked`.

Future opportunity: a "blocked hours" metric could show how much time workers spend stuck, helping identify systemic issues (auth problems, flaky CI, missing documentation).

## Reconnect Behavior

On reconnect, preserve the pre-disconnect status. If a worker was `blocked` before disconnecting, restore to `blocked` (not `waiting`). The blocker likely still exists. Update `_recovery_status()` in `reconnect.py` to check the previous status.

## Worker Prompt Changes

Add guidance to the worker prompt for when to use `blocked` vs just staying `waiting`:

```markdown
**When you're blocked**: If you cannot proceed without external help (missing credentials,
need access, can't find a solution, unclear requirements), set your status to blocked:
  orch-worker update --status blocked
  orch-task update --notes "Waiting: <reason you're blocked>"

**When you're just waiting**: If you're waiting on something that will resolve on its own
(PR review, CI running, scheduled for later), stay in waiting status — no action needed.
```

## Impact Analysis

### Files to modify

**Backend (4 files):**
- `orchestrator/session/state_machine.py` — add `BLOCKED` to enum + transitions
- `orchestrator/session/reconnect.py` — `_recovery_status()` preserves blocked on reconnect
- `orchestrator/api/routes/projects.py` — add `blocked` to worker_stats aggregation
- `orchestrator/main.py` — add `blocked` to Rich terminal color mapping

**Agent files (3 files):**
- `agents/worker/prompt.md` — when to set blocked vs waiting
- `agents/worker/bin/orch-worker` — add `blocked` to help text
- `agents/brain/skills/heartbeat.md` — prioritize blocked workers in scan order

**Frontend (8 files):**
- `frontend/src/api/types.ts` — add `'blocked'` to status union + ProjectStats
- `frontend/src/styles/variables.css` — add `--status-blocked` color
- `frontend/src/styles/global.css` — add `.status-dot.blocked`, `.status-badge.blocked`
- `frontend/src/utils/statusColors.ts` — add `blocked` to color map
- `frontend/src/pages/WorkersPage.tsx` — add to STATUS_ORDER
- `frontend/src/components/layout/StatsBar.tsx` — count + display blocked workers
- `frontend/src/components/dashboard/RecentActivity.tsx` — group blocked separately
- `frontend/src/components/sidebar/Sidebar.tsx` — include in attention badge

### Files that need NO changes
- `orchestrator/api/routes/sessions.py` — status is text, no validation enforcement needed
- `orchestrator/api/routes/brain.py` — filter already includes non-idle/non-disconnected
- `orchestrator/state/repositories/status_events.py` — blocked auto-excluded from work-hours

---

## Risks & Mitigations

### Race Condition: Hook Overwrites `blocked` with `waiting`

**The critical risk.** The sequence:

1. Worker calls `orch-worker update --status blocked` → status set to `blocked`
2. Claude Code fires `Stop` event → `update-status.sh` runs → sets `waiting`
3. Status silently reverts to `waiting`, brain never sees `blocked`

This happens because the hook fires on Claude lifecycle events that can occur *after* the worker sets its own status. The existing guard only protects `idle` from being overwritten.

**Fix**: Extend the hook guard to also protect `blocked`:

```bash
# Guard: don't overwrite "idle" or "blocked" with "waiting"
if [ "$STATUS" = "waiting" ]; then
    CURRENT=$(curl -s "$API_BASE/api/sessions/$SESSION_ID" | jq -r '.status // empty')
    if [ "$CURRENT" = "idle" ] || [ "$CURRENT" = "blocked" ]; then
        exit 0
    fi
fi
```

This means `update-status.sh` **does need a change** (moved from "no changes" list above).

### State Machine Not Enforced at API Level

The PATCH `/sessions/{id}` endpoint writes status directly without calling `validate_transition()`. The state machine is advisory. This is a pre-existing design choice for hook flexibility — hooks need to set status without knowing the full transition graph.

**Implication**: The hook guard is the real enforcement, not the state machine. The state machine documents intent; the hook guard enforces the `blocked` protection.

### Worker Forgets to Set `blocked`

The worker is an LLM and may not always remember to call `orch-worker update --status blocked`. It might just say "I'm stuck" in terminal and stay as `waiting`.

**Mitigation**: The brain's heartbeat is the safety net. When a `waiting` worker shows error patterns or has been idle too long, the brain escalates to `blocked`. Worker self-reporting is the fast path; brain detection is the fallback.

### Multiple Status Sources Racing

The worker, the hook, and the brain can all set status concurrently. SQLite WAL handles concurrent writes, but last-write-wins applies.

**Mitigation**: The hook guard (check current status before overwriting) prevents the most common race. The brain sets `blocked` during heartbeat scans which happen on a timer (not concurrently with hook events). The worker sets `blocked` and then stops executing (so no subsequent hook fires until brain sends help).
