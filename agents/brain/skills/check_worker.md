---
name: check_worker
description: Review all workers, produce a status summary table with suggested actions, and let the user approve which to execute.
---

# Check Workers

Scan all workers, produce a concise status table with suggested actions, and batch-execute after user confirmation.

## Usage
- `/check_worker` — Scan all workers, show summary table, wait for user to approve actions
- `/check_worker <worker-id>` — Check a specific worker (same table format, one row)
- `/check_worker auto` — Scan all and execute suggested actions without confirmation

---

## Procedure

### Step 1: Gather data

```bash
orch-workers list
orch-tasks list --exclude-status done
```

If there are zero non-idle workers, report "All workers idle — nothing to check" and stop.

**Specific worker mode:** If a worker ID was provided, run the commands above but only process that single worker. Still show the one-row table and wait for confirmation.

### Step 2: Capture terminal for each non-idle worker

For every worker whose status is NOT idle, capture the last 50 lines:
```bash
tmux capture-pane -p -t orchestrator:<worker-name> -S -50
```

Read each terminal output to understand the situation. This is the fastest way — no external API calls needed for most workers.

### Step 3: Triage each worker

For each worker, apply the [Triage Rules](#triage-rules) below to determine a one-line **situation** and **suggested action**. Workers claiming completion require the [Slow Path](#slow-path-completion-verification) (external verification via orch-prs).

Batch PR checks whenever possible:
```bash
orch-prs --repo <owner/repo> <pr1> <pr2> <pr3> ...
```

### Step 4: Present the summary table

Format all results as a numbered table. Example:

```
## Worker Status Report

| # | Worker | Task | Status | Age | Situation | Suggested Action |
|---|--------|------|--------|-----|-----------|-----------------|
| 1 | api-worker | PERP-7: Fix dashboard perf | waiting | 15m | Idle at prompt | Send "continue" |
| 2 | ui-worker | PERP-12: Update settings UI | working | 5m | Writing tests | — |
| 3 | rdev-worker | PERP-3: Rename API package | waiting | 3h | PR #254 awaiting review | Nudge to check PR |
| 4 | data-worker | PERP-9: Memory reduction | waiting | 10m | PR #261 merged, task complete | Mark done + stop |
| 5 | test-worker | — | idle | 1d | No task assigned | (available) |

Proposed actions: #1, #3, #4
Approve? (all / 1,3 / none / skip 4)
```

Rules for the table:
- Include ALL workers (idle, working, waiting, etc.) for a complete picture
- Workers with status "working" get a dash (—) as action — they are progressing
- Idle workers with no task get "(available)" — informational only
- Only workers with an actual suggested action get a row number in "Proposed actions"

### Step 5: Execute approved actions

Wait for user response, then execute:
- **"all"** or **"yes"** — Execute all proposed actions
- **"1,3"** (comma-separated numbers) — Execute only those numbered actions
- **"none"** — Do nothing
- **"skip 4"** — Execute all proposed EXCEPT #4

For each executed action, verify it worked. Wait 3 seconds then check:
```bash
orch-workers show <worker-id> | jq '.status'
```

If status is still "waiting" after sending a message, the Enter key may not have registered:
```bash
tmux send-keys -t orchestrator:<worker-name> Enter
```

### Step 6: Print recap

```
Done. 2 of 3 actions executed.
  #1 api-worker: sent "continue" → now working
  #3 rdev-worker: nudged to check PR → now working
  #4 data-worker: skipped by user
```

Include recommended follow-ups with timing, e.g., "Nudge rdev-worker again in 2h if still waiting for review."

---

## Triage Rules

### Quick check: Is worker claiming completion?

Scan terminal output for completion signals:
- "PR merged", "PR has been merged", "Successfully merged"
- "Task complete", "Task done", "All done", "Deliverable ready"
- Worker explicitly says it finished

No completion signals → **Fast Path**. Completion signals → **Slow Path**.

### Fast Path (no external calls needed)

Determine situation and action from status_age and terminal context:

| Condition | Situation | Suggested Action |
|-----------|-----------|-----------------|
| Waiting <2m | Just entered waiting | — (skip, avoid double-nudge) |
| Waiting 2m-2h, at prompt | Idle at prompt | Send "continue" |
| Context exhaustion (0%) | Context limit reached | Send "continue" (triggers auto-compact) |
| PR review wait >2h | PR awaiting review (Xh) | Nudge: "Check PR status, address comments if any, merge if approved" |
| PR review wait <2h | PR awaiting review (Xm) | — (reviews take time) |
| Just checked PR | PR checked recently, pending | — (check again in 2-4h) |
| Needs info you can look up | Needs info: ... | Look it up and relay |
| Needs info you cannot find | Needs human: ... | — (leave for user) |
| Blocked by auth | Blocked by auth/permissions | — (leave for user) |
| Needs decision, >90% confident | Asking: ... | Suggest answer with reasoning |
| Needs decision, <90% confident | Needs human decision: ... | — (leave for user) |

### Slow Path: Completion Verification

For workers claiming completion, verify externally before suggesting "Mark done".

**Step A:** Get task details and identify deliverables:
```bash
orch-tasks list --assigned <worker-id> --format json | jq '.[0]'
```

**Step B:** For coding tasks, check PR status:
```bash
orch-prs --repo <owner/repo> <pr-numbers>
```

Map PR action to table entry:

| PR action | Situation | Suggested Action |
|-----------|-----------|-----------------|
| merged | PR merged, all checks passed | Mark done + stop worker |
| ready_to_merge | PR approved, CI green | Tell worker to merge now |
| ci_failing | CI failing on PR | Tell worker to fix CI |
| changes_requested | Review changes requested | Tell worker to address reviews |
| merge_conflicts | PR has merge conflicts | Tell worker to rebase |
| review_pending | PR awaiting review | — (wait for review) |
| draft | PR still in draft | — (still working) |
| closed | PR was closed | Investigate (may need new PR) |

**Step C:** For non-PR deliverables, note what needs verification in the Situation column.

### Executing "Mark done + stop" Actions

When the user approves a "Mark done + stop" action, execute these steps in order:

1. Send a completion notification:
```bash
orch-notifications create \
  --message "<completion-summary-with-verification>" \
  --task-id "<task-id>" \
  --type "task_completion_review" \
  --link "<pr-url>"
```

The notification message must include the task description, PR link (if applicable), and verification details (PR state, CI status, review status, comments resolved).

2. Mark task done and stop worker:
```bash
orch-tasks update <task-id> --status done
orch-workers stop <worker-id>
```

---

## Auto Mode

When called with "auto":
1. Follow Steps 1-4 as normal (gather, capture, triage, print table)
2. Print the summary table for visibility
3. Execute ALL suggested actions immediately — no confirmation wait
4. Print the recap

---

## Key Rules

- **Terminal-first** — read terminal output BEFORE running external commands
- **Fast path = no external calls** — if worker is not claiming completion, just check status_age and decide
- **Default nudge is "continue"** — never stop or delete workers unless task is verified done
- **Never guess GitHub URLs** — copy exactly from worker output or use gh pr view
- **NEVER stop a worker waiting for PR review** — must stay alive for the review cycle
- **PR created is not done** — worker stays alive until PR is MERGED with all checks passing
- **Always notify before marking done** — send completion notification with verification details
- **Act on facts only** — if unsure, put "Needs human" in the table rather than guessing
- **Batch PR checks** — collect all PR numbers from terminal output and check per-repo in one call
- **For special keys** (up/down arrow to select options): suggest tmux send-keys Up or Down as the action
