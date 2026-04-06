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

**Filter first**: From the worker list, immediately skip:
- `idle` workers with no assigned task — these are available worker slots, leave untouched
- `paused` workers — intentionally paused by user/brain, leave untouched

Only process workers that have an assigned task and are in `blocked`, `waiting`, or `working` status.

### 2. Gather worker state in parallel

For workers that passed the filter, use **sub-agents** to gather terminal + task state in parallel. This avoids sequential previewing which burns context on large fleets.

Spawn one sub-agent per worker (or batch 3-4 per agent):
```
Read the terminal and task for this worker, then classify its state:
- Worker: <name> (id: <id>)
- Task: <task-id>

Run these commands:
  orch-workers preview <worker-name>
  orch-tasks show <task-id>

Then report:
1. Terminal state: at Claude > prompt / running command / interactive prompt / error visible / empty terminal / disconnected
2. Last activity: what the worker last did (from terminal)
3. Task notes summary: key status and wait reason
4. Task updated_at: <timestamp> and how old it is
5. Recommended action: nudge / skip / investigate / mark-done / notify-user
```

Collect all sub-agent reports, then act on them in priority order:
1. **`blocked` workers first** — these need immediate help
2. **`waiting` workers** — nudge if idle too long or stale
3. **`working` workers** — skip unless error visible in terminal

**Classify PR-wait status**: Check if the task has PR links (`links` field with tag "PR"). If yes AND worker status is `waiting`, this is a **PR review wait** — use `last_status_changed_at` from worker data for wait duration. Do NOT treat as a generic idle worker.

**Check task note staleness**: Compare the task's `updated_at` timestamp against the current time. If the task notes haven't been updated in >2h and the worker is idle at a prompt, the stated wait reason is stale — the worker should re-check regardless of what the notes say. External blockers (Owner Approval, review pending, CI) can resolve at any time, and a stale "waiting on X" may no longer be accurate.

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
| **PR review wait, <2h AND `updated_at` < 2h** | Skip — reviews take time |
| **Stale task notes (>2h since `updated_at`) + worker idle at prompt** | Nudge worker to re-check status, even if notes say "waiting on X". **Overrides** the PR-wait-<2h skip — external blockers resolve without notification. |
| At Claude `>` prompt, idle 5m+ (not PR-wait) | `orch-send <id> "continue"` -- but FIRST check if the last user message in the terminal is already "continue". If so, **skip** (don't spam). |
| Context exhaustion (0%) | `orch-send <id> "continue"` (triggers auto-compact) |
| Stuck with visible error (idle 10m+) | Investigate, and also set status to blocked: `orch-workers update <id> --status blocked` |
| At interactive prompt (y/n, menu) | Handle common prompts directly (see "Interactive prompts" below), otherwise notify |
| Empty terminal / disconnected | Worker session may be broken. Try `orch-workers reconnect <id>`. If that fails, notify user. |
| Worker claims task complete | Run verification checklist (see below). For PR-based tasks: if all pass, mark done + stop. For document deliverables: always notify user for confirmation. For recurring tasks: notify iteration complete, never mark done or stop worker. If concern: notify user. |
| PR open, missing evidence | Nudge worker to add evidence to PR description (see evidence nudge below) |
| Worker idle >2h, no visible progress | Set status to `waiting`, notify: "Worker X idle 2h+, needs attention". **Do NOT stop.** |
| Blocked on auth/access/human decision | Set status to `waiting`, notify with details of what's needed. **Do NOT stop** — the worker resumes once the human provides input. |
| Worker partially done, needs human input | Set status to `waiting`, notify with specifics (what input, what's done so far, what remains). **Do NOT stop** — partial progress is valuable and restarting from scratch wastes all prior work. |

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
- `closed` → PR was closed without merging. Check PR comments (`gh api repos/ORG/REPO/pulls/N/comments --jq '.[-1].body'`) to understand why. If the fix was superseded or root cause was external, mark the subtask done with a note explaining. If closed in error, send worker to reopen or create a new PR.

**Tasks without PRs** (docs, config, investigation): **always notify the user for confirmation** instead of auto-marking done. Document deliverables (design docs, reports, analysis, config changes) require human judgment to verify completeness and correctness. Notify with a summary of what was produced and ask the user to confirm the task is done.

**Recurring tasks** (monitoring, periodic reports, recurring syncs): Check the task description or notes for indicators that the task is recurring (e.g., "recurring", "periodic", "weekly", "daily", "ongoing", or implied by nature). For recurring tasks, **never mark done and never stop the worker** — the user will run the task again. Instead, notify the user that the current iteration is complete and leave the worker available.

For large/critical PRs, use `/review` (Claude Code built-in) with task context for a deeper review.

**When stopping is allowed** — stopping a worker destroys its context and all partial progress. **Only stop a worker when the task is verified complete.** This means:

- PR merged + checks pass + reviewed ✓ → stop
- Document/investigation confirmed done by user ✓ → stop
- Everything else → **do NOT stop**

**Never stop a worker that has an incomplete task**, regardless of reason:
- Blocked on human input? → Set to `waiting`, notify user. Worker stays alive.
- Stuck on auth/RBAC/access? → Set to `waiting`, notify user. Worker stays alive.
- Went off-track? → Notify user to course-correct. Worker stays alive.
- Idle for days? → Notify user. Worker stays alive.
- Needs laptop/local access? → Set to `waiting`, notify user. Worker stays alive.

A blocked worker that stays alive can resume in seconds once the blocker is resolved. A stopped worker must restart from scratch, losing all accumulated context and partial work. The cost of keeping a waiting worker alive is near zero; the cost of restarting from 0% is enormous.

**Marking done + stopping** (only for PR-based, non-recurring tasks — always notify first):

Before marking done, check:
- **Document deliverable?** → Do NOT mark done. Notify user: "Task <key> deliverable ready: <summary>. Please confirm if this is done."
- **Recurring task?** → Do NOT mark done or stop worker. Notify user: "Task <key> iteration complete: <summary>. Worker left available for next run."
- **PR-based, non-recurring?** → Safe to mark done + stop:

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

**Interactive prompts playbook:**

Common Claude Code startup/interactive prompts and how to handle them:

| Prompt | Action |
|--------|--------|
| "Yes, I trust this folder" (selected) / "No, exit" | `orch-workers type <name> Enter` — option 1 is already selected |
| Settings Error → "Exit and fix manually" (selected) / "Continue without these settings" | `orch-workers type <name> $'\x1b[B'` then `orch-workers type <name> Enter` — arrow down to option 2, then confirm |
| `[y/n]` confirmation | `orch-workers type <name> y` or `orch-workers type <name> n` based on context |
| Password/auth prompt | Notify user — never type credentials |
| Unknown interactive prompt | Notify user with screenshot of terminal |

**Skip (no action needed):**

| Condition | Reason |
|-----------|--------|
| `idle` or `paused` status | Not active — leave untouched |
| No assigned task | Available worker slot — nothing to monitor |
| Actively running a command | Working -- don't interrupt |
| Idle <5m | Just finished, give it a moment |
| Stated wait reason in task notes AND `updated_at` < 2h ago | Respecting worker's recent stated blocker |
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
- **Respect recent wait reasons** -- if a worker stated why it's waiting in task notes AND `updated_at` is <2h ago, don't nudge. Stale wait reasons (>2h) get a re-check — external blockers can resolve silently
- **Always mention `/pr-workflow`** -- when sending any PR-related message, include `/pr-workflow` so the worker invokes the skill
- **Act on facts only** -- if unsure, notify the user rather than guessing
- **Never guess org/repo** -- parse PR URLs from task links: `github.com/ORG/REPO/pull/N` → `--repo ORG/REPO N`. Multiproduct names ≠ GitHub org names
- **Interactive prompts** -- use the playbook above. `orch-workers type <name> Enter` for confirmations, `$'\x1b[B'` (Down) / `$'\x1b[A'` (Up) for menus. Never type passwords.
- **Staleness overrides PR-wait skip** -- a stale `updated_at` (>2h) means the worker should re-check, even if `last_status_changed_at` is under the PR-wait threshold
- **Document deliverables need human confirmation** -- never auto-mark done for tasks whose deliverable is a document, report, or non-PR artifact. Always notify user and wait for them to confirm completion
- **Recurring tasks stay alive** -- never mark done or stop workers on recurring tasks. Notify that the iteration is complete and leave the worker available for the next run
- **NEVER stop a worker with an incomplete task** -- blocked, waiting, needs input, went off-track, idle for days — none of these are stop conditions. Set to `waiting`, notify user, move on. Only stop when the task is **verified done**. A stopped worker loses all context and must restart from scratch
- **Idle/paused workers are not your concern** -- skip them entirely, don't preview their terminals
