---
name: create
description: Analyze input (text, image, or idea), determine best placement across projects and tasks, create the work item after approval, then optionally assign a worker.
---

# Create Work Item

Analyze user input, match it against existing projects and tasks to find the best placement, present a plan for approval, create the work item, then optionally assign a worker.

## Usage
- `/create <text or idea>` — Analyze input and propose where to place it
- `/create <file-path>` — Read an image or file, then analyze and propose placement
- `/create` — Prompt the user to describe what they want to create

---

## Procedure

### Phase 1: Analyze and Place

#### Step 1: Gather state

```bash
orch-ctx list
orch-projects list --status active
orch-tasks list --exclude-status done
```

If there are zero active projects, note that a new project will be required.

#### Step 2: Analyze input

Parse the user's text, image, or idea. Extract:
- **Core deliverable** — What is the concrete outcome? (e.g., "PR merged: ...", "Deployed: ...", "Document published: ...")
- **Scope** — Is this a whole project, a single task, or a sub-piece of an existing task?
- **Reference material** — Any URLs, code snippets, screenshots, or specifications included

If the input is a file path (image, PDF, etc.), read the file and incorporate its content into the analysis.

#### Step 3: Match against existing work

Compare the extracted deliverable against all active projects and tasks:

| Condition | Placement |
|-----------|-----------|
| Doesn't fit any existing project | **New project** + first task |
| Fits an existing project but no task covers it | **New task** under that project |
| Is a sub-deliverable of an existing task | **Subtask** under that task |
| Overlaps with an existing task | **Flag overlap** — show both and ask user to choose |

When in doubt, prefer creating a new task over a subtask. Subtasks are only for clearly decomposable pieces of an already-defined task.

#### Step 4: Present recommendation

Show a table with the proposed work item:

```
## Proposed Work Item

| Field       | Value                                              |
|-------------|---------------------------------------------------|
| Type        | New task (under existing project)                  |
| Project     | Auth Migration (UTI)                               |
| Parent      | — (or parent task key if subtask)                  |
| Title       | PR merged: Add OAuth callback endpoint             |
| Priority    | high                                               |
| Description | Implement the /auth/callback endpoint that ...     |

Reference material will be stored as context.

Approve? (yes / edit / no)
```

Rules for the recommendation:
- **Title must be deliverable-focused** — "PR merged: ...", "Deployed: ...", "Shipped: ...", not implementation steps
- **Priority**: high if blocking other work, medium by default, low if nice-to-have
- **Description**: concise but sufficient for a worker to understand the deliverable without the original input

If the placement is "overlap detected", show both the existing task and the proposed task and ask the user which to keep or whether to merge.

#### Step 5: User approval

Wait for user response:
- **"yes"** — Proceed to execute
- **"edit"** — Ask what to change, update the recommendation, re-present
- **"no"** — Abort

#### Step 6: Execute creation

Based on the approved plan, run the appropriate commands:

**New project + task:**
```bash
orch-projects create --name "<project-name>" --description "<project-description>" --task-prefix "<PREFIX>"
# Use the returned project ID:
orch-tasks create --project-id <new-project-id> --title "<task-title>" --description "<task-description>" --priority <priority>
```

**New task under existing project:**
```bash
orch-tasks create --project-id <project-id> --title "<task-title>" --description "<task-description>" --priority <priority>
```

**Subtask under existing task:**
```bash
orch-tasks create --project-id <project-id> --title "<task-title>" --description "<task-description>" --priority <priority> --parent-id <parent-task-id>
```

If there is reference material (URLs, pasted content, images), store it as context:
```bash
orch-ctx create --title "<descriptive-title>" --content "<reference-content>" --scope project --project-id <project-id> --category reference
```

For multi-line descriptions or large reference content, use stdin:
```bash
echo '<content>' | orch-ctx create --title "<title>" --content-stdin --scope project --project-id <project-id> --category reference
```

Print what was created:
```
Created: PROJ-7 "PR merged: Add OAuth callback endpoint" (high priority)
  Project: Auth Migration
  Context: Stored reference material as context item abc123
```

---

### Phase 2: Worker Assignment

#### Step 7: Check workers

```bash
orch-workers list --status idle
orch-workers rdevs
```

#### Step 8: Assess options

Evaluate in this order of preference:
1. **Idle worker exists** → Propose assigning the task to it
2. **No idle worker, but available rdev** → Propose creating a new worker on an available rdev
3. **No idle worker, no available rdev, but localhost** → Propose creating a local worker
4. **No availability** → Report no workers available, user can assign later

#### Step 9: Present assignment plan

```
## Worker Assignment

| Field   | Value                                   |
|---------|-----------------------------------------|
| Action  | Assign to existing idle worker          |
| Worker  | api-worker (idle for 20m)               |
| Task    | PROJ-7: PR merged: Add OAuth callback   |

Assign? (yes / no / skip)
```

- **"yes"** — Execute assignment
- **"no"** — Don't assign, done
- **"skip"** — Same as "no" — work item was already created, skip assignment

#### Step 10: Execute assignment

**Assign to existing worker:**
```bash
orch-tasks assign <task-id> <worker-id>
```

**Create new worker + assign:**
```bash
orch-workers create --name <worker-name> --host <host> --task-id <task-id>
```

The `--task-id` flag on worker creation handles assignment automatically.

#### Step 11: Recap

```
Done.
  Created: PROJ-7 "PR merged: Add OAuth callback endpoint" (high, Auth Migration)
  Assigned: api-worker → PROJ-7
  Context: Stored 1 reference item
```

If no worker was assigned:
```
Done.
  Created: PROJ-7 "PR merged: Add OAuth callback endpoint" (high, Auth Migration)
  Worker: Not assigned (no idle workers available)
  Context: Stored 1 reference item
```

---

## Key Rules

- **Two separate approval gates** — Never combine work item creation and worker assignment into a single approval
- **Deliverable-focused titles** — "PR merged: ...", "Deployed: ...", not implementation steps like "Update the config and refactor..."
- **Overlap detection** — Flag rather than silently create duplicates; show the existing task and let the user decide
- **Image/file support** — When input is a file path, read it and reference in description + store as context
- **Idle workers first** — Always prefer reusing idle workers over creating new ones
- **Minimal projects** — Don't create a new project if the work fits an existing one
- **Store reference material** — Any pasted content, URLs, or images that informed the task should be stored as context for the worker
- **Let the user decide scope** — If placement is ambiguous between project/task/subtask, present the options rather than guessing
