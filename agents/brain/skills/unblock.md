---
name: unblock
description: Investigate why a worker is stuck and send targeted help.
---

# Unblock Worker

Shift from delegation mode to investigation mode. Research the problem, diagnose the root cause, and send targeted help.

## Usage

- `/unblock <worker-name>` -- Investigate and help a specific worker

---

## Procedure

### 1. Deep read

```bash
orch-workers preview <worker-name>
orch-tasks show <task-id>
```

Read the terminal output carefully. Look for error messages, stack traces, repeated failures, or questions the worker is asking.

### 2. Classify the blocker

| Category | Signal | Brain Action |
|----------|--------|-------------|
| Technical error | Build/test failure, type error, import error | Research the error, send a fix suggestion |
| Missing context | "Can't find...", "Where is...", "I don't know how to..." | Look it up (gh, orch-ctx), relay the answer |
| PR-related block | CI failing, changes requested, merge conflicts, review stale | Tell worker: "Use /pr-workflow to check PR status, address comments if any, merge if approved" |
| Decision paralysis | "Should I...", "Two approaches...", "Not sure whether to..." | Make a recommendation with rationale |
| External dependency | Waiting on review, access, API, another team | Check status, notify user if stale |
| Asking a question | "?" in output, waiting for input | Answer if confident, notify user if not |

### 3. Research

For technical errors and missing context, investigate before responding:

```bash
# Search your learning logs for similar past issues
orch-memory logs --search "<error keyword or pattern>"

# Search shared context
orch-ctx list --search "<error keyword or pattern>"

# Search the repo for relevant patterns
gh search code "<error message snippet>" --repo <org/repo> --limit 5
```

Also check:
- Task notes and subtask notes for relevant context
- Project-scoped context: `orch-ctx list --scope project --project-id <id>`

### 4. Act

**If confident in your diagnosis**, send targeted help:
```bash
orch-send <id> "<specific diagnosis + concrete suggestion with file paths or commands>"
```

Good help messages include:
- The specific error and why it's happening
- A concrete fix (file path, command, code change)
- Relevant context you found (past similar issue, repo convention)

**If uncertain**, notify the user instead of guessing:
```bash
orch-notifications create --type "brain_unblock" \
  --message "Worker <name> stuck on <problem>. I think it might be <hypothesis> but flagging for human review." \
  --task-id "<id>"
```

### 5. Record

If the root cause is a pattern worth remembering for future encounters:
```bash
orch-memory log "<root cause and what fixed it>" --title "<repo>: <short description>"
```

---

## Key Rules

- **Never send a worker down a wrong path.** If you're not confident in your diagnosis, notify the user: "I think it might be X, but flagging for human review."
- **Be specific, not vague.** "Try re-running the tests" is bad. "The ECONNREFUSED on port 5432 means the test DB container hasn't started -- run `docker compose up -d db` first, then re-run" is good.
- **Check memory first.** The same issue might have happened before. A 10-second memory check can save minutes of re-investigation.
- **External dependencies need user action.** If the blocker is access, permissions, or another team's review, notify the user -- don't try to solve it yourself.
- **Always mention `/pr-workflow`** when sending any PR-related message (create, fix, review, merge) so the worker invokes the skill.
