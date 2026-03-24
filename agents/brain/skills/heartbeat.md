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

### 2. Prioritize by status

Process workers in this order:
1. **`blocked` workers first** — these need immediate help
2. **`waiting` workers** — nudge if idle too long on PR review
3. **`working` workers** — skip unless error visible in terminal

For each worker, read terminal and task:
```bash
orch-workers preview <worker-name>
orch-tasks show <task-id>   # Check notes for "Waiting: ..." messages
```

**Classify PR-wait status**: Check if the task has PR links (`links` field with tag "PR"). If yes AND worker status is `waiting`, this is a **PR review wait** — use `last_status_changed_at` from worker data for wait duration. Do NOT treat as a generic idle worker.

**Review your past actions on this worker.** Check if you have pending `action:` notes:

```bash
orch-memory logs --search "action: <worker-name>"
```

If you find a pending action note, compare your suggestion against what the worker actually did (visible in task notes):

- Worker followed your suggestion and it worked → **Effective**. Delete the action note: `orch-memory delete-log <id>`
- Task notes show a *different* fix or root cause than what you suggested → **Correction**. Delete the action note and record the lesson:
  ```bash
  orch-memory log "correction: I suggested <X> for <situation>, but actual fix was <Y>. Lesson: <Z>" \
    --title "correction: <repo/context> — <short description>"
  ```
- Task re-opened after you marked it done, or worker restarted after you stopped it → **Correction**. Same as above.
- Task notes empty or no new activity yet → **Skip**. Leave the action note for next cycle. Glance at terminal as fallback.
- User's follow-up *extends* your suggestion (complementary, not contradictory) → **Effective**. Delete action note.

**Before taking any new action**, also search for past corrections on similar situations:

```bash
orch-memory logs --search "correction: <error keyword or situation>"
```

If a relevant correction exists, factor it into your decision.

### 3. Act based on state

**Act:**

| Condition | Action |
|-----------|--------|
| **Status: `blocked`** | **Priority** — investigate immediately (see "Investigating stuck workers" below). The worker explicitly asked for help. |
| **PR review wait, >2h** | `orch-send <id> "Use /pr-workflow to check PR status, address comments if any, merge if approved"` |
| **PR review wait, <2h** | Skip — reviews take time |
| At Claude `>` prompt, idle 5m+ (not PR-wait) | `orch-send <id> "continue"` -- but FIRST check if the last user message in the terminal is already "continue". If so, **skip** (don't spam). |
| Context exhaustion (0%) | `orch-send <id> "continue"` (triggers auto-compact) |
| Stuck with visible error (idle 10m+) | Investigate, and also set status to blocked: `orch-workers update <id> --status blocked` |
| At interactive prompt (y/n, menu) | Attempt to answer if obvious, otherwise notify |
| Worker claims task complete | Run verification checklist (see below). If all pass: mark done + stop. If concern: notify user. |
| PR open, missing evidence | Nudge worker to add evidence to PR description (see evidence nudge below) |
| Worker idle >2h, no visible progress | Notify: "Worker X may be stuck, needs human review" |
| Blocked on auth/access/human decision | Notify with details of what's needed |

**Resolving PR info**: Get the PR URL from task links (`orch-tasks show <task-id>` → `links` field with tag "PR") or from the worker's terminal output. Parse the URL: `github.com/ORG/REPO/pull/N` → `--repo ORG/REPO N`. Never guess the org name — multiproduct names are not GitHub org names.

**Verification checklist** (before marking done):

1. `orch-prs --repo <owner/repo> <numbers>` — PR must be merged
2. `gh pr checks <number> --repo <owner/repo>` — all required CI checks passed
3. `gh pr view <number> --repo <owner/repo> --json reviewDecision` — APPROVED
4. `gh pr view <number> --repo <owner/repo> --json files --jq '.files[].path'` — changes match task scope
5. `orch-tasks show <task-id>` — look for "## Verification" section in notes
6. If all pass → mark done + stop + notify. If any concern → notify user with specifics, don't auto-mark done.

If the PR is not merged, don't mark done — check the `orch-prs` action field:
- `ci_failing` / `changes_requested` / `merge_conflicts` → send worker: "Use /pr-workflow to fix PR issues"
- `ready_to_merge` → send worker: "Use /pr-workflow to merge"
- `review_pending` / `draft` → not actually complete, skip

**Tasks without PRs** (docs, config, investigation): verify the deliverable exists (file committed, answer in task notes). Check for "## Verification" section. If unverifiable → notify user instead of marking done.

For large/critical PRs, use `/review` (Claude Code built-in) with task context for a deeper review.

**Marking done + stopping** (always notify first):
```bash
# 1. Notify with verification summary
orch-notifications create --type "brain_heartbeat" \
  --message "Verified task <key>: <summary>. Marking done, stopping worker." \
  --task-id "<id>" --link "<pr-url>"

# 2. Mark done + stop
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

**Log significant actions** so you can review their outcomes on later heartbeats:

```bash
orch-memory log "<what you did and why — include worker name, task ID, key context>" \
  --title "action: <worker-name> — <short description>"
```

Skip logging routine actions (sending "continue", skipping). Log investigations, fixes sent, tasks marked done, workers stopped.

**Skip (no action needed):**

| Condition | Reason |
|-----------|--------|
| Actively running a command | Working -- don't interrupt |
| Idle <5m | Just finished, give it a moment |
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

### 5. Self-reflection

If you have accumulated `correction:` logs, periodically curate them into your wisdom document:

1. `orch-memory logs --search "correction:"` — check for correction patterns
2. If you see patterns (same mistake repeated, or enough corrections to distill a useful rule), update your wisdom document:
   ```bash
   orch-memory wisdom-update <<'EOF'
   (your current wisdom with the new correction pattern added/updated)
   EOF
   ```
3. Delete correction logs that have been curated into wisdom.
4. Clean up stale `action:` notes that are no longer resolvable (worker gone, task done long ago).
5. If you curated new patterns, notify the user:
   ```bash
   orch-notifications create --type "brain_heartbeat" \
     --message "Brain learned from recent corrections: <brief summary>"
   ```

Skip this step if there are no correction logs or nothing worth curating yet.

### 6. Brief output

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
- **Never guess org/repo** -- parse PR URLs from task links: `github.com/ORG/REPO/pull/N` → `--repo ORG/REPO N`. Multiproduct names ≠ GitHub org names
- **Special keys for interactive prompts** -- use `orch-workers type <worker-name> $'\x1b[A'` (Up) or `$'\x1b[B'` (Down) for selection menus
