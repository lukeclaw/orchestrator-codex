---
name: check_worker
description: Check waiting workers and handle low-risk actions. Use when workers are stuck in "waiting" status.
---

# Check Waiting Workers

Handle workers in "waiting" status with low-risk actions to move tasks forward.

## Usage
- `/check_worker` — Check first waiting worker, propose action, wait for confirmation
- `/check_worker <worker-id>` — Check a specific worker by ID
- `/check_worker auto` — Automatically process ALL waiting workers

---

## Procedure

### Step 1: Get waiting workers
```bash
orch-workers list --status waiting
```

If no waiting workers, report "No workers in waiting status" and stop.

Each worker has:
- `status_age` — Human-readable duration like "5m ago", "2h ago", "1d ago"
- `last_status_changed_at` — ISO timestamp (if you need exact time)

Use `status_age` to prioritize:
- **Recently waiting (<5m):** May still be processing, consider skipping
- **Waiting 5-30m:** Good candidate for nudge
- **Waiting >30m:** Likely stuck, prioritize checking these first

### Step 2: Select worker(s) to process

**Default mode (no args):** Pick only the FIRST waiting worker from the list.

**Specific worker mode (`<worker-id>` arg):** Check the specified worker directly:
```bash
orch-workers show <worker-id>
```
Skip Step 1 if a specific worker ID is provided — go straight to checking that worker.

**Auto mode (`auto` arg):** Process ALL waiting workers sequentially.

### Step 3: Capture terminal state FIRST (before any external commands)
```bash
tmux capture-pane -p -t orchestrator:<worker-name> -S -50
```

**⚡ IMPORTANT:** Read the terminal output to understand what the worker is doing. This is the fastest way to determine the situation — no external API calls needed.

### Step 4: Quick triage — Is worker claiming completion?

Scan the terminal output for **completion signals**:
- "PR merged", "PR has been merged", "Successfully merged"
- "Task complete", "Task done", "All done"
- "Deliverable ready", "Doc published"
- Worker explicitly says it finished

**If NO completion signals → Go to [Fast Path](#fast-path-worker-still-working)** (most common)

**If completion signals found → Go to [Slow Path](#slow-path-verify-completion)** (requires verification)

---

## Fast Path: Worker Still Working

This is the **quick path** — no external API calls needed. Just decide if we should nudge.

### Check `status_age` and terminal context:

**Case 1: Recently waiting (<2m)**
- Action: **Skip** — avoid double-nudging
- Worker may still be processing

**Case 2: Waiting 2m-2h, at prompt**
- Worker finished a step, sitting at prompt
- Action: `orch-send <worker-id> "continue"`

**Case 3: Context exhaustion (0%)**
- Claude shows context limit warning
- Action: `orch-send <worker-id> "continue"` (triggers auto-compact)
- Do NOT stop or recreate the worker

**Case 4: Waiting for PR reviews (>2h)**
- Terminal shows worker is waiting for PR approval/merge
- **⚠️ DO NOT STOP THE WORKER** — must stay alive for review cycle
- If **>2h**: Nudge to check PR status
  - `orch-send <worker-id> "Check PR status. If there are review comments, address them. If approved, merge."`
- If **<2h**: Skip — PR reviews take time
- **Recommended follow-up:** "Nudge again in 2h if still waiting"

**Case 4b: Worker just checked PR, still waiting**
- Terminal shows worker already checked PR recently
- **⚠️ DO NOT STOP** — even if "idle", must stay alive
- Action: **Skip** (no action needed)
- **Recommended follow-up:** "Check again in 2-4h"

**Case 5: Missing info**
- Worker needs information you can look up
- Use your tools (jarvis, confluence, jira) to find the info
- Relay via: `orch-send <worker-id> "<the information>"`
- If you cannot find the info: Skip, leave for human

**Case 6: Blocked by auth**
- Action: Skip, leave for human to handle

**Case 7: Need decision**
- Worker asking which approach to take
- Only act if you have >90% confidence
- If confident: `orch-send <worker-id> "Use approach X because..."`
- If not confident: Skip, leave for human

**After fast path → Go to [Shared Steps](#shared-steps-after-fast-or-slow-path)**

---

## Slow Path: Verify Completion

**Only enter this path if worker claims task is done or PR is merged.**

This path is slower because it requires external verification, but it's necessary to ensure quality.

### Step A: Get task details
```bash
orch-tasks list --assigned <worker-id> --format json | jq '.[0]'
```

Identify the task ID and deliverables:
- Design doc? → Must be shared/published
- POC? → Must be working and demo-able
- Bug fix? → Must be verified fixed
- Code change? → Default = **PR merged**

### Step B: Verify PR status (for coding tasks)

If the task involves code changes, check the PR:
```bash
gh pr view <pr-number> --repo <repo> --json state,mergeable,reviews,statusCheckRollup,comments
```

**PR is NOT complete if ANY of these are true:**
- `state` is not "MERGED" → PR still needs to be merged
- `mergeable` is "CONFLICTING" → Has merge conflicts to resolve
- `reviews` has "CHANGES_REQUESTED" → Reviewer requested changes
- `statusCheckRollup` has failing checks → CI is failing
- `comments` has unresolved threads → Comments need to be addressed

If any of the above are true:
```
orch-send <worker-id> "PR is not ready to merge yet. Please check: [specific issue]. Address it and try again."
```

### Step C: Verify other deliverables

For non-PR deliverables:
- **Design doc:** Verify the doc exists and is shared (check the link)
- **JIRA ticket:** Verify it's updated with resolution
- **Investigation:** Verify findings are documented

### Step D: Mandatory notification before marking done

**You MUST send a notification to the user before marking any task as done.**

Use the `orch-notifications` CLI:
```bash
orch-notifications create \
  --message "<completion-summary-with-verification>" \
  --task-id "<task-id>" \
  --type "task_completion_review" \
  --link "<pr-url-if-applicable>"
```

The notification message should include:
1. **Task:** Brief description of what was done
2. **PR link:** (if coding task) Direct link to the PR
3. **Verification:** Brief explanation of how you verified completion
   - "PR merged, CI passed, no open comments"
   - "Design doc shared at <link>, reviewed by team"
   - "Bug fix verified: <test that confirms fix>"

**Example notification message:**
```
📋 Task Ready for Completion

Task: Rename customizationApi to chameleonPremiumApi
Worker: api-worker

PR: https://github.com/org/repo/pull/456

Verification:
✓ PR state: MERGED
✓ CI checks: All passed
✓ Reviews: Approved by @reviewer
✓ Comments: All resolved

Reply 'yes' to mark as done and stop worker.
```

### Step E: Wait for user acknowledgment

**DO NOT proceed until the user acknowledges the notification.**

After sending the notification:
1. Tell the user: "I've sent a notification for your review. Please check the dashboard and approve before I mark the task as done."
2. Wait for user response (they will say "yes", "approved", "go ahead", etc.)
3. Only then execute:
   ```bash
   orch-tasks update <task-id> --status done
   orch-workers stop <worker-id>
   ```

---

## Shared Steps (after Fast or Slow Path)

### Step 5: Propose or execute action

**Default mode:** Present your analysis and proposed action to the user:
```
## Worker: <name> (<id>)
**Situation:** <brief description of what you see>
**Proposed action:** <the command you would run>

Proceed? (yes/no/skip)
```
Wait for user confirmation before executing. If user says "skip", move on without action.

**Auto mode:** Execute the action immediately, then verify and continue to next worker.

### Step 6: Verify action worked (after execution)
Wait 3 seconds, then check worker status:
```bash
orch-workers show <worker-id> | jq '.status'
```

If status is not "working", the Enter key may not have registered. Resend:
```bash
tmux send-keys -t orchestrator:<worker-name> Enter
```

---

## Key Rules
- **⚡ Terminal-first approach** — read terminal output BEFORE running external commands (gh, jarvis, etc.). Most checks don't need verification.
- **Fast path = no external calls** — if worker isn't claiming completion, just check `status_age` and decide to nudge or skip
- **Default action is "continue"** — never stop or delete workers unless task is done
- **Never guess GitHub URLs** — copy PR URLs exactly from worker output or `gh pr view --json url`. Never construct URLs from memory.
- **⚠️ NEVER stop a worker waiting for PR review** — worker must stay alive to address reviewer comments when they arrive. "Waiting for reviewer" is NOT a reason to stop. Only stop when PR is MERGED.
- **Check task deliverables first** — if task specifies a deliverable (doc, POC, etc.), use that; otherwise default to PR merged
- **PR created ≠ done** — worker must stay alive until PR is MERGED with all checks passing
- **PR waiting for review ≠ done** — worker stays alive, will address comments when reviews come in
- **PR with open comments ≠ done** — reviewer comments must be addressed
- **PR with failing CI ≠ done** — CI must pass
- **PR with conflicts ≠ done** — conflicts must be resolved
- **Always notify before marking done** — user must acknowledge completion
- **Act on facts only** — if unsure about a decision, do NOT take action
- **You have more tools than workers** — use captain MCP tools (LIX, jarvis, confluence, jira) to relay info workers can't access
- **For special keys** (up/down arrow to select options): use `tmux send-keys -t orchestrator:<name> Up` or `Down`

## Output
Provide a brief summary:
- Workers checked and actions taken (or proposed)
- Workers skipped and why (left for human)
- **Notifications sent** for task completion reviews
- **Recommended follow-ups** with timing (e.g., "Worker X: nudge again in 2h if still waiting for PR review")
