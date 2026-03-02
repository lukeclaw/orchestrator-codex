---
name: check_worker
description: Review all workers, produce a status summary table with suggested actions, and let the user approve which to execute.
---

# Check Workers

Scan workers, show a status table with grouped actions, get user approval, execute.

## Usage
- `/check_worker` — All workers
- `/check_worker <worker-id>` — Single worker

---

## Procedure

### 1. Gather data

```bash
orch-workers list
orch-tasks list --exclude-status done
```

If zero non-idle workers, report "All workers idle — nothing to check" and stop.

### 2. Capture terminal for each non-idle worker

```bash
tmux capture-pane -p -t orchestrator:<worker-name> -S -50
```

Classify prompt state before deciding actions:
- **Claude prompt** — `>` input indicator visible → safe to send "continue"
- **Interactive prompt** — y/n, password, menu → note what it's asking
- **Running command** — output scrolling → no action needed
- **Unclear** → mark "Needs human"

### 3. Check for stated wait reasons

Before triaging, check the worker's task/subtask notes for a stated wait reason:
```bash
orch-tasks show <task-id>   # Check notes field for "Waiting: ..." messages
```
If the worker has stated a valid reason for waiting (e.g., outside working hours, waiting on a dependency, blocked on access), **respect it** — skip that worker or note the reason in the summary table. Don't nudge a worker to do something it has already explained it can't do yet.

### 4. Triage

Two paths based on terminal output:

**Fast Path** (no completion signals — terminal-only, no external calls):

| Condition | Action |
|-----------|--------|
| Waiting <2m | — (avoid double-nudge) |
| Waiting 2m-2h, at Claude prompt | Send "continue" |
| Waiting, at interactive prompt | Describe prompt; suggest input or "Needs human" |
| Context exhaustion (0%) | Send "continue" (triggers auto-compact) |
| PR review wait >2h | Nudge: "Check PR status, address comments if any, merge if approved" |
| PR review wait <2h | — (reviews take time) |
| Needs info you can look up | Look it up and relay |
| Blocked by auth / needs human decision | — (leave for user) |

**Slow Path** (worker claims completion — verify externally):

```bash
orch-tasks list --assigned <worker-id> --format json | jq '.[0]'
orch-prs --repo <owner/repo> <pr-numbers>    # batch per repo
```

Map `orch-prs` action field to suggested action:

| PR action | Suggested Action |
|-----------|-----------------|
| merged | Mark done + stop |
| ready_to_merge | Tell worker to merge |
| ci_failing / changes_requested / merge_conflicts | Tell worker to fix |
| review_pending, <2h | — (wait for review) |
| review_pending, >2h | Nudge: "Check PR status, address comments if any, merge if approved" |
| draft | — (still working) |
| closed | Investigate |

### 5. Present summary

Show a numbered table of non-idle workers. Collapse idle workers into one line. Group proposed actions by type letter.

```
| # | Worker | Task | Situation | Suggested Action |
|---|--------|------|-----------|------------------|
| 1 | api-worker | PERP-7: Fix dashboard | Idle at prompt (15m) | Send "continue" |
| 2 | rdev-worker | PERP-3: Rename API | PR #254 review (3h) | Nudge PR check |
| 3 | deploy-worker | PERP-9: Memory fix | PR #270 merged | Mark done + stop |

+8 idle workers available: test-worker, infra-worker, ...

A) Send "continue" (1): #1
B) Nudge PR check (1): #2
C) Mark done + stop (1): #3

Approve? (all / A,B / skip C / none / 1,3)
```

User can approve by group letter, skip a group, or cherry-pick numbers.

### 6. Execute and recap

After approval, execute actions. Verify each with `orch-workers show <id> | jq '.status'`. If still "waiting" after sending a message, retry with `tmux send-keys -t orchestrator:<worker-name> Enter`.

Print recap grouped by action type. Include follow-up suggestions (e.g., "Check rdev-worker again in 2h").

### Executing "Mark done + stop"

Must follow this exact sequence:
```bash
# 1. Notify (include task description, PR link, verification details)
orch-notifications create \
  --message "<summary>" --task-id "<id>" \
  --type "task_completion_review" --link "<pr-url>"

# 2. Mark done + stop
orch-tasks update <task-id> --status done
orch-workers stop <worker-id>
```

---

## Key Rules

- **Terminal-first** — read terminal output BEFORE external API calls
- **Verify prompt state** — only send "continue" when at a Claude `>` prompt, not interactive prompts
- **Never stop a worker waiting for PR review** — must stay alive for the review cycle
- **PR created is not done** — worker stays alive until PR is MERGED
- **Always notify before marking done** — completion notification with verification details
- **Act on facts only** — if unsure, put "Needs human" rather than guessing
- **Batch PR checks** — `orch-prs --repo <owner/repo> <pr1> <pr2> ...`
- **For special keys** (arrow keys for selection menus): use `tmux send-keys Up/Down`
