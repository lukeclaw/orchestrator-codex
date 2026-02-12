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

### Step 2: Select worker(s) to process

**Default mode (no args):** Pick only the FIRST waiting worker from the list.

**Auto mode (`auto` arg):** Process ALL waiting workers sequentially.

### Step 3: For selected worker, capture terminal state
```bash
tmux capture-pane -p -t orchestrator:<worker-name> -S -50
```

### Step 4: Analyze situation and determine action

**Case 1: Waiting for nudge** — Worker finished a step, sitting at prompt
- Action: `orch-send <worker-id> "continue"`

**Case 2: Context exhaustion (0%)** — Claude shows context limit warning
- Action: `orch-send <worker-id> "continue"` (triggers auto-compact)
- Do NOT stop or recreate the worker

**Case 3: Blocked on PR reviews** — Worker waiting for PR approval
- Check if more than 4 hours have passed since last activity
- If yes: `orch-send <worker-id> "Check PR status again and proceed if possible"`
- If no: Skip, let it wait

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
- **Default action is "continue"** — never stop or delete workers unless task is done
- **Act on facts only** — if unsure about a decision, do NOT take action
- **You have more tools than workers** — use captain MCP tools (LIX, jarvis, confluence, jira) to relay info workers can't access
- **For special keys** (up/down arrow to select options): use `tmux send-keys -t orchestrator:<name> Up` or `Down`

## Output
Provide a brief summary:
- Workers checked and actions taken (or proposed)
- Workers skipped and why (left for human)
