---
name: check-worker
description: Check waiting workers and handle low-risk actions. Use when workers are stuck in "waiting" status.
---

# Check Waiting Workers

Handle workers in "waiting" status with low-risk actions to move tasks forward.

## Usage
- `/check-worker` — Check first waiting worker, propose action, wait for confirmation
- `/check-worker auto` — Automatically process ALL waiting workers

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

**Auto mode (`auto` arg):** Process ALL waiting workers sequentially.

### Step 3: Get task description and deliverables
```bash
orch-tasks list --assigned <worker-id> --format json | jq '.[0]'
```

Check the task description for **explicit deliverables** (e.g., "deliver a design doc", "create a POC", "fix the bug").
- If specific deliverable is defined → use that as completion criteria
- If no specific deliverable → default completion = **PR merged**

### Step 4: Capture terminal state
```bash
tmux capture-pane -p -t orchestrator:<worker-name> -S -50
```

### Step 5: Analyze situation and determine action

**Case 1: Waiting for nudge** — Worker finished a step, sitting at prompt
- Check `status_age`: If **<2m**, skip to avoid double-nudging
- If **>2m**: `orch-send <worker-id> "continue"`

**Case 2: Context exhaustion (0%)** — Claude shows context limit warning
- Action: `orch-send <worker-id> "continue"` (triggers auto-compact)
- Do NOT stop or recreate the worker

**Case 3: Blocked on PR reviews** — Worker waiting for PR approval/merge
- Check `status_age` to see how long they've been waiting
- If **>2h**: Nudge to check PR status
  - `orch-send <worker-id> "Check PR status. If there are review comments, address them. If approved, merge."`
- If **<2h**: Skip — PR reviews take time
- **Recommended follow-up:** "Nudge again in 2h if still waiting"

**Case 3b: Worker just checked PR, still waiting for reviewer**
- Terminal shows worker already checked PR and is waiting
- Do NOT nudge again immediately — reviewer needs time
- **Recommended follow-up:** "Check again in 2-4h"

**Case 4: Missing info** — Worker needs information you can look up
- Use your tools (jarvis, confluence, jira, gh CLI) to find the info
- Relay via: `orch-send <worker-id> "<the information they need>"`
- If you cannot find the info: Skip, leave for human

**Case 5: Blocked by auth** — Worker needs credentials or permissions
- Action: Skip, leave for human to handle

**Case 6: Need decision** — Worker asking which approach to take
- Only act if you have >90% confidence in the right choice
- If confident: `orch-send <worker-id> "Use approach X because..."`
- If not confident: Skip, leave for human

### Step 6: Propose or execute action

**Default mode:** Present your analysis and proposed action to the user:
```
## Worker: <name> (<id>)
**Situation:** <brief description of what you see>
**Proposed action:** <the command you would run>

Proceed? (yes/no/skip)
```
Wait for user confirmation before executing. If user says "skip", move on without action.

**Auto mode:** Execute the action immediately, then verify and continue to next worker.

### Step 7: Verify action worked (after execution)
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
- **Default action is "continue"** — never stop or delete workers unless task is done
- **Check task deliverables first** — if task specifies a deliverable (doc, POC, etc.), use that; otherwise default to PR merged
- **PR created ≠ done** — unless task says otherwise, worker must stay alive until PR is merged
- **Act on facts only** — if unsure about a decision, do NOT take action
- **You have more tools than workers** — use captain MCP tools (LIX, jarvis, confluence, jira) to relay info workers can't access
- **For special keys** (up/down arrow to select options): use `tmux send-keys -t orchestrator:<name> Up` or `Down`

## Output
Provide a brief summary:
- Workers checked and actions taken (or proposed)
- Workers skipped and why (left for human)
- **Recommended follow-ups** with timing (e.g., "Worker X: nudge again in 2h if still waiting for PR review")
