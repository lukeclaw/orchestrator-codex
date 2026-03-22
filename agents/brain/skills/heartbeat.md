---
name: heartbeat
description: Autonomous worker monitoring. Takes safe actions immediately, notifies for risky ones, investigates stuck workers. Never waits for approval.
---

# Heartbeat

Non-blocking autonomous monitoring. Designed for `/loop` but can also be run manually.

**Rule**: Never ask for approval. Never present an "Approve?" prompt. Take safe actions, notify for risky ones, move on.

---

## Procedure

### 1. Gather state

```bash
orch-workers list
orch-tasks list --exclude-status done
```

If zero non-idle workers with tasks, output "All clear." and stop.

### 2. For each non-idle worker with a task

Read terminal output:
```bash
orch-workers preview <worker-name>
```

Check for stated wait reasons:
```bash
orch-tasks show <task-id>   # Check notes for "Waiting: ..." messages
```

### 3. Act based on state

**Act:**

| Condition | Action |
|-----------|--------|
| At Claude `>` prompt, idle 5m+ | `orch-send <id> "continue"` -- but FIRST check if the last user message in the terminal is already "continue". If so, **skip** (don't spam). |
| Context exhaustion (0%) | `orch-send <id> "continue"` (triggers auto-compact) |
| PR review wait >3h | `orch-send <id> "Use /pr-workflow to check PR status, address comments if any, merge if approved"` |
| Stuck with visible error (idle 10m+) | Investigate inline (see below) |
| At interactive prompt (y/n, menu) | Attempt to answer if obvious, otherwise notify |
| Worker claims task complete | Run verification checklist (see below). If all pass: mark done + stop. If concern: notify user. |
| PR open, missing evidence | Nudge worker to add evidence to PR description (see evidence nudge below) |
| Worker idle >2h, no visible progress | Notify: "Worker X may be stuck, needs human review" |
| Blocked on auth/access/human decision | Notify with details of what's needed |

**Verification checklist** (before marking done):

1. `orch-prs --repo <owner/repo> <numbers>` — PR must be merged
2. `gh pr checks <number> --repo <owner/repo>` — all required CI checks passed
3. `gh pr view <number> --repo <owner/repo> --json reviewDecision` — APPROVED
4. `gh pr view <number> --repo <owner/repo> --json files --jq '.files[].path'` — changes match task scope
5. `orch-tasks show <task-id>` — look for "## Verification" section in notes
6. If all pass → mark done + stop + notify. If any concern → notify user with specifics, don't auto-mark done.

For large/critical PRs, use `/review` (Claude Code built-in) with task context for a deeper review.

**Marking done + stopping**:
```bash
orch-tasks update <task-id> --status done
orch-workers stop <worker-id>
```

**Evidence nudge** (catch missing evidence on open PRs):

When scanning workers with open PRs (`review_pending` or `ready_to_merge`), check: `gh pr view <number> --repo <owner/repo> --json body,files`
- **API changes** (files in `api/`, `routes/`, `models/`, proto) + no test results in PR body → nudge: "Add QEI/qprod test results to your PR description"
- **Frontend/UI changes** (files in `frontend/`, `components/`, `*.css`, `*.tsx`) + no screenshots in PR body → nudge: "Add screenshots or recordings showing the UI change"
- Only nudge once per PR — check if the worker was already nudged about this (look at recent messages in terminal)

### Notifications

**Always notify the user** about significant actions so they stay aware. Use notifications for:
- Actions taken: "Marked task X done, stopped worker Y (PR #270 merged)"
- Help sent: "Sent fix suggestion to worker X (ECONNREFUSED — missing DB env var)"
- Blockers found: "Worker X blocked on auth — needs human review"
- Stuck workers: "Worker X idle 2h+ with no progress, needs attention"

```bash
orch-notifications create --type "brain_heartbeat" \
  --message "<summary>" --task-id "<id>" --link "<relevant-url>"
```

Routine actions (sending "continue", skipping workers) do not need notifications.

**Skip (no action needed):**

| Condition | Reason |
|-----------|--------|
| Actively running a command | Working -- don't interrupt |
| Idle <5m | Just finished, give it a moment |
| PR review <3h | Reviews take time |
| Stated wait reason in task notes | Respecting worker's stated blocker |
| Last message was already "continue" | Avoid spam -- worker may need different help |

### 4. Investigating stuck workers

When a worker has been idle 10m+ with a visible error in its terminal:

1. **Read terminal output carefully.** Identify the error message or stack trace.

2. **Search operational memory for similar past issues:**
   ```bash
   orch-memory logs --search "<error keyword or pattern>"
   ```

3. **Classify and act:**

   - **Technical error** (build/test failure, type error, import error): Research via `gh search code "<error snippet>" --repo <org/repo> --limit 5`, check repo context, formulate a specific fix.
   - **Missing context** ("Can't find...", "Where is..."): Look it up and relay the answer.
   - **Decision paralysis** ("Should I...", two approaches): Make a recommendation with rationale.
   - **External dependency** (review, access): Check status, notify user if stale.
   - **Asking a question**: Answer if confident, notify user if not.

4. **If confident in diagnosis:** Send targeted help:
   ```bash
   orch-send <id> "<specific diagnosis + concrete suggestion>"
   ```

5. **If uncertain:** Notify the user instead of guessing:
   ```bash
   orch-notifications create --type "brain_unblock" \
     --message "Worker X stuck on <problem>. I think it might be Y but flagging for review." \
     --task-id "<id>"
   ```

6. **After resolving:** If this was a pattern worth remembering, store in operational memory:
   ```bash
   orch-memory log "<root cause and fix>" --title "<repo>: <short description>"
   ```

**Key rule**: Never send a worker down a wrong path. Uncertainty -> notify user.

### 5. Brief output

After processing all workers, log what you did -- one line per worker:

```
api-worker: sent "continue" (idle 12m at prompt)
rdev-worker: skipped (PR review 1h, waiting)
deploy-worker: marked done + stopped (PR #270 merged) — notified user
test-worker: sent fix suggestion (ECONNREFUSED — missing DB env var) — notified user
```

---

## Key Rules

- **Never block on user input** -- this skill must complete without waiting for approval
- **Terminal-first** -- read terminal output before making any external API calls
- **Verify prompt state** -- only send "continue" when at a Claude `>` prompt, not interactive prompts
- **Dedup "continue"** -- if the last message in the terminal is already "continue", skip
- **PR created is not done** -- worker stays alive until PR is MERGED
- **Self-report is not done** -- a worker claiming completion must be verified externally via `orch-prs`
- **Respect wait reasons** -- if a worker stated why it's waiting in task notes, don't nudge
- **Always mention `/pr-workflow`** -- when sending any PR-related message, include `/pr-workflow` so the worker invokes the skill
- **Act on facts only** -- if unsure, notify the user rather than guessing
