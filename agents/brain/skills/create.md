---
name: create
description: Analyze input (text, image, or idea), determine best placement across projects and tasks, create the work item after approval, then optionally assign a worker.
---

# Create Work Item

Analyze input, match against existing projects/tasks, propose placement, create after approval, then optionally assign a worker.

## Usage
- `/create <text or idea>` — Analyze and propose placement
- `/create <file-path>` — Read file/image, then analyze
- `/create` — Show welcome prompt, then wait for user input

### Empty Input Handling

When invoked with no arguments (`/create` or `/create ` with only whitespace), respond with a brief, friendly welcome message instead of immediately prompting for input. Example:

```
What would you like to create? Share your idea, feature request, bug report, or any task — I'll figure out where it fits and set things up.

You can describe it in plain text, or paste a file path / screenshot.
```

Then wait for the user to type their request. Do NOT run any commands or gather state until the user provides input.

---

## Phase 1: Create Work Item

### 1. Gather state

```bash
orch-ctx list
orch-projects list --status active
orch-tasks list --exclude-status done
```

### 2. Analyze input and match

Extract the core deliverable, scope, and any reference material. If input is a file path, read it first.

Match against existing work:

| Condition | Placement |
|-----------|-----------|
| Doesn't fit any project | New project + first task |
| Fits a project, no task covers it | New task under that project |
| Sub-deliverable of existing task | Subtask (only if clearly decomposable) |
| Overlaps existing task | Flag overlap — show both, let user choose |

When in doubt, prefer new task over subtask.

### 3. Present and approve

Show a summary table (type, project, title, priority, description) and wait for approval (yes / edit / no).

### 4. Execute

```bash
# New project (if needed)
orch-projects create --name "<name>" --description "<desc>" --task-prefix "<PREFIX>"

# Create task (add --parent-id for subtasks)
orch-tasks create --project-id <id> --title "<title>" --description "<desc>" --priority <priority>

# Store reference material as context
orch-ctx create --title "<title>" --content "<content>" --scope project --project-id <id> --category reference
# For large content, pipe via stdin:
echo '<content>' | orch-ctx create --title "<title>" --content-stdin --scope project --project-id <id> --category reference
```

---

## Phase 2: Worker Assignment

**Important:** Workers are assigned to **top-level tasks only**, never to sub-tasks. If you just created a sub-task, check whether the parent task already has a worker:
- **Parent has a worker** → send the worker a message pointing to the new sub-task: `orch-send <worker> "New sub-task created: <title>. Run orch-tasks show <parent-id> for details."`
- **Parent has no worker** → propose assigning a worker to the **parent task** (not the sub-task)

### 5. Check availability and propose

```bash
orch-workers list --status idle
orch-workers rdevs
```

Preference order (match by MP name — rdevs and workers are prefixed with their MP name, only assign to workers on the same MP as the task):
1. **Idle worker on matching MP** → assign to it
2. **Available rdev on matching MP** → create new worker on it
3. **No matching rdev available, <5 rdevs total** → suggest `rdev create <mp_name>`, offer local worker as alternative
4. **No matching rdev available, at 5 rdev limit** → propose a local worker
5. **No availability** → skip, user assigns later

Present assignment plan and wait for approval (yes / no).

### 6. Execute

```bash
# Assign to existing worker (always the top-level task, never a sub-task)
orch-tasks assign <task-id> <worker-id>

# Or create new worker + assign in one step
# Local worker (default):
orch-workers create --name <name> --task-id <task-id>
# Remote worker (rdev or SSH):
orch-workers create --name <name> --remote <host> --task-id <task-id>
```

Print recap of what was created and assigned.

### 7. Verify worker started

Wait ~10 seconds after assignment, then check that the worker is actually working:

```bash
orch-workers preview <worker-name>
```

Look at the terminal output:
- **Worker is running commands or reading the task** → all good, done.
- **Worker is sitting at a Claude prompt (`>`) doing nothing** → nudge it: `orch-send <worker-name> "You have a new task assigned. Run orch-task show to review it and get started."`
- **Worker shows an error or is unresponsive** → flag to the user that the worker may need attention.

Do not skip this step — assignment alone does not guarantee the worker received the message.

---

## rdev Quick Reference

- **List sessions:** `rdev list` — shows MP name, session name, and status
- **Host format:** `MP_NAME/SESSION_NAME` (e.g. `subs-mt/sleepy-franklin`) — the slash triggers rdev mode; use `localhost` for local workers
- **Brain's job:** just set the host — the API handles tunnel setup, SSH, Claude launch, and sending the initial message

---

## Key Rules

- **Two separate approval gates** — never combine creation and assignment into one prompt
- **Deliverable-focused titles** — "PR merged: ...", "Deployed: ...", not implementation steps
- **Overlap detection** — flag duplicates, let user decide; never silently create
- **Store reference material** — URLs, pasted content, images go into context for the worker
- **Idle workers first** — reuse idle workers over creating new ones
- **Never assign workers to sub-tasks** — assign to the parent task; message the worker about the new sub-task
- **Let user decide scope** — if ambiguous between project/task/subtask, present options
