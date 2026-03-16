---
name: create
description: "THE workflow for all new work — tasks, projects, subtasks, ideas, research, bugs. Use /create for EVERY piece of work, no exceptions. Analyzes input, determines placement across projects and tasks, creates the work item after approval, then assigns a worker. If you're about to run orch-tasks create or orch-projects create directly, STOP and use /create instead."
---

# Create Work Item

This is the single entry point for all new work. Every task, project, subtask, bug, research question, or idea flows through this skill — no ad-hoc `orch-tasks create` calls.

Why this matters: this workflow ensures overlap detection, proper placement, context bootstrapping for workers, and verified worker startup. Skipping it means workers start without context, duplicates go unnoticed, and assignments silently fail.

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

### Multi-Item Requests

When the user asks for multiple things at once ("I need X, Y, and Z"), treat each as a separate work item through this skill. You can gather state once (step 1) and present all items together for efficiency, but give the user per-item control — they should be able to approve, edit, or reject each item individually. For example: "Approve all three? Or type the number of any item to edit/skip."

---

## Phase 1: Create Work Item

### 1. Gather state

```bash
orch-ctx list
orch-projects list --status active
orch-tasks list --exclude-status done
```

### 2. Bootstrap context

Before analyzing, check if you already have useful context for the worker — prior decisions, relevant URLs, related task notes, or domain knowledge from stored context. Skim the `orch-ctx list` output from step 1 and read anything relevant:

```bash
orch-ctx read <id>  # Read items that relate to the user's request
```

Hold onto this — you'll attach it to the task in step 5 so the worker doesn't start from scratch.

### 3. Analyze input and match

Extract the core deliverable, scope, and any reference material. If input is a file path, read it first.

Match against existing work:

| Condition | Placement |
|-----------|-----------|
| Doesn't fit any project | New project + first task |
| Fits a project, no task covers it | New task under that project |
| Sub-deliverable of existing task | Subtask (only if clearly decomposable) |
| Overlaps existing task | Flag overlap — show both, let user choose |

When in doubt, prefer new task over subtask.

### 4. Present and approve

Show a summary table (type, project, title, priority, description) and wait for approval (yes / edit / no).

- **yes** → proceed to execute
- **edit** → ask what to change, revise, re-present
- **no** → acknowledge and stop; don't create anything

Write deliverable-focused titles — "PR merged: ...", "Deployed: ...", "Investigate: ..." — not implementation steps. If you find yourself writing "First do X, then Y" in the title, rewrite it as the end state.

### 5. Execute

```bash
# New project (if needed)
orch-projects create --name "<name>" --description "<desc>" --task-prefix "<PREFIX>"

# Create task (add --parent-id for subtasks)
orch-tasks create --project-id <id> --title "<title>" --description "<desc>" --priority <priority>
```

**Attach context for the worker** — if you found relevant context in step 2, or the user provided URLs/references/details, store them so the worker has them from the start:

```bash
# Store reference material as context
orch-ctx create --title "<title>" --content "<content>" --scope project --project-id <id> --category reference
# For large content, pipe via stdin:
echo '<content>' | orch-ctx create --title "<title>" --content-stdin --scope project --project-id <id> --category reference
# Or attach directly to the task notes:
orch-tasks update <id> --notes-stdin <<'EOF'
Relevant context:
- <key finding or URL>
- <prior decision or link>
EOF
```

---

## Phase 2: Worker Assignment

**Important:** Workers are assigned to **top-level tasks only**, never to sub-tasks. If you just created a sub-task, check whether the parent task already has a worker:
- **Parent has a worker** → send the worker a message pointing to the new sub-task: `orch-send <worker> "New sub-task created: <title>. Run orch-tasks show <parent-id> for details."`
- **Parent has no worker** → propose assigning a worker to the **parent task** (not the sub-task)

### 6. Check availability and propose

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

When assigning multiple tasks, allocate the best resources (rdevs) to the highest-priority tasks first.

Present assignment plan and wait for approval (yes / no).

### 7. Execute and verify

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

**Now verify the worker actually received the work.** This is not optional — whether you created a new worker, assigned an existing one, or messaged a worker about updated context, always verify they picked it up. Workers can fail to start (SSH issues, tunnel failures, Claude not launching), sit idle at a prompt without noticing the task, or miss messages entirely. If you skip this check, you'll think work is in progress when nothing is happening.

Wait **15 seconds** (30 seconds for rdev workers — they need tunnel setup time), then check:

```bash
orch-workers preview <worker-name>
```

| What you see | What to do |
|---|---|
| Worker is running commands or reading the task | All good — done. |
| Worker is at a Claude prompt (`>`) doing nothing | Nudge it: `orch-send <worker-name> "You have a new task assigned. Run orch-tasks show to review it and get started."` Then preview again after 10s to confirm it picked up. |
| Worker shows an error or is unresponsive | Flag to the user: "Worker <name> may need attention — saw: <brief error>". |
| Preview returns empty or connection error | The worker didn't start. Tell the user and suggest retrying or creating a new worker. |

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
- **Always verify** — preview the worker after every assignment or message, no exceptions
