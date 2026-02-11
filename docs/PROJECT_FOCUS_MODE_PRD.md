# Project Focus Mode — Mini PRD

**Version:** 1.0
**Author:** Yudong Qiu
**Date:** February 11, 2026
**Status:** Draft

---

## 1. Problem Statement

The current brain agent manages **all projects and workers simultaneously**. As the number of projects grows, this creates:

1. **Context overload** — Brain prompt includes all active tasks, workers, and context items
2. **Attention fragmentation** — Brain jumps between projects, losing focus
3. **No project-specific customization** — Same brain prompt for all project types
4. **UI clutter** — Dashboard shows everything at once

Users have asked for "multiple brain agents" to solve this, but that introduces significant complexity (resource locking, race conditions, fragmented state).

---

## 2. Proposed Solution: Project Focus Mode

A **single brain** with a **project focus selector**. When focused on a project:

- Brain sees **only that project's** tasks, workers, and context
- Brain's prompt can include **project-specific instructions**
- UI filters to show **focused project's** entities
- Brain can **switch focus** instantly without restart

This gives the UX benefits of "multiple brains" without the architectural risks.

---

## 3. How It Works

### 3.1 Focus State

The brain has a **current focus** stored in the API:

```
GET /api/brain/focus
→ { "project_id": "abc-123" | null, "project_name": "Auth Migration" | null }

POST /api/brain/focus
← { "project_id": "abc-123" }
```

When `project_id` is `null`, the brain operates in **global mode** (current behavior).

### 3.2 Filtered Context

When focused on project `P`:

| Entity | Filter Rule |
|--------|-------------|
| **Tasks** | Only tasks where `project_id = P` |
| **Workers** | Only workers assigned to tasks in project `P`, plus idle workers |
| **Context items** | Only items with `scope = project AND project_id = P`, plus `scope = global` |
| **Notifications** | Only notifications linked to tasks in project `P` |

### 3.3 Brain Prompt Injection

When focused, the brain's system prompt is **augmented**:

```
You are currently focused on project: **{project_name}**

Project description:
{project_description}

---
[Project-specific instructions from context items with category="instruction"]
---

When listing tasks, workers, or context, results are filtered to this project only.
To see all projects or switch focus, use `/switch-project` skill.
```

### 3.4 CLI Behavior Changes

When focused on project `P`:

| Command | Behavior |
|---------|----------|
| `orch-tasks list` | Returns only tasks for project `P` |
| `orch-workers list` | Returns workers assigned to `P` + idle workers |
| `orch-ctx list --scope project` | Returns context for project `P` only |
| `orch-tasks create ...` | Auto-sets `project_id = P` (no `--project-id` needed) |

**Escape hatch**: Add `--all` flag to bypass filtering:
```bash
orch-tasks list --all            # All tasks across all projects
orch-workers list --all          # All workers
```

### 3.5 UI Changes

#### Right Rail Tabs
The brain panel header gets a **project selector dropdown**:

```
┌─────────────────────────────────────┐
│ ◉ Brain │ [Auth Migration ▼] │ Stop │
├─────────────────────────────────────┤
│ (terminal)                          │
│                                     │
└─────────────────────────────────────┘
```

Dropdown options:
- **All Projects** (global mode)
- List of active projects
- "New Project..." link

#### Visual Indicators
- Focused project name shown in header
- Sidebar project list highlights focused project
- Task/worker cards show "out of focus" indicator if from other projects (in global mode)

### 3.6 Switching Focus

**Option A: Brain skill command**
```
/switch-project Auth Migration
/switch-project --all   # Switch to global mode
```

**Option B: UI dropdown** (triggers API call, sends notification to brain)

**Option C: Brain detects from dashboard URL** (existing focus tracking via `/api/brain/focus`)

When focus changes:
1. API updates focus state
2. WebSocket broadcasts `brain.focus_changed` event
3. UI updates dropdown
4. Next brain CLI command sees filtered results

---

## 4. Data Model Changes

### 4.1 Brain Focus Table (optional)

Could store in existing `config` table:
```sql
INSERT INTO config (key, value, category)
VALUES ('brain.focus_project_id', 'abc-123', 'brain');
```

Or add to brain session record:
```sql
ALTER TABLE sessions ADD COLUMN focus_project_id TEXT REFERENCES projects(id);
```

### 4.2 No Schema Changes Required for Filtering

Filtering is **query-time** — no data model changes needed. The API/CLI just adds WHERE clauses.

---

## 5. Implementation Plan

### Phase 1: Backend Focus State (1-2 hours)
- [x] `/api/brain/focus` already exists (currently tracks dashboard URL)
- [ ] Extend to store `project_id` instead of/alongside URL
- [ ] Add focus state to brain status response

### Phase 2: CLI Filtering (2-3 hours)
- [ ] Modify `orch-tasks` to read focus state from API
- [ ] Add `--all` flag to bypass
- [ ] Modify `orch-workers` similarly
- [ ] Modify `orch-ctx` to auto-scope to focused project

### Phase 3: Brain Prompt Injection (1 hour)
- [ ] On brain start, check focus state
- [ ] Inject project context into CLAUDE.md
- [ ] Create `/switch-project` skill

### Phase 4: UI Dropdown (2-3 hours)
- [ ] Add project selector to BrainPanel header
- [ ] Wire to `/api/brain/focus` POST
- [ ] Broadcast focus change via WebSocket
- [ ] Update dropdown on `brain.focus_changed` event

### Phase 5: Polish (1 hour)
- [ ] Add "out of focus" visual indicators
- [ ] Sidebar highlight for focused project
- [ ] Keyboard shortcut for focus switch

**Total estimate: 7-10 hours**

---

## 6. Example User Flow

1. User opens dashboard, sees projects: "Auth Migration", "Dark Mode", "API Refactor"
2. User clicks "Auth Migration" in brain panel dropdown
3. Brain receives: "You are now focused on project: Auth Migration"
4. User asks brain: "What's the status?"
5. Brain runs `orch-tasks list` → sees only Auth Migration tasks
6. Brain runs `orch-workers list` → sees workers assigned to Auth Migration + idle workers
7. Brain summarizes Auth Migration progress only
8. User clicks "All Projects" in dropdown
9. Brain receives: "You are now in global mode — all projects visible"
10. Brain runs `orch-tasks list` → sees all tasks across all projects

---

## 7. Comparison: Focus Mode vs Multi-Brain

| Aspect | Project Focus Mode | Multi-Brain |
|--------|-------------------|-------------|
| **Complexity** | Low | High |
| **Race conditions** | None | Requires locking |
| **Context isolation** | Per-project filtered | True isolation |
| **Resource usage** | 1 Claude session | N Claude sessions |
| **State consistency** | Guaranteed | Requires coordination |
| **Prompt customization** | Via context injection | Per-brain prompts |
| **UI complexity** | Dropdown | Tabs + terminals |
| **Implementation time** | ~8 hours | ~40+ hours |

---

## 8. Future: Graduating to Multi-Brain

If focus mode proves insufficient, the path to multi-brain is:

1. **Resource locking**: Add `locked_by_brain_id` to tasks/workers
2. **Brain instances**: Change brain from singleton to multi-instance
3. **Per-brain state**: Each brain has own focus, own tmux window
4. **UI tabs**: Replace dropdown with tabbed brain panels

Focus mode is designed to **not block** this evolution — the filtering logic and project-scoping will be reusable.

---

## 9. Open Questions

1. **Auto-focus on navigation?** — When user clicks a project in sidebar, auto-focus brain?
2. **Focus persistence?** — Remember focus across brain restarts?
3. **Multiple monitors?** — Could detach brain panel to separate window?

---

## 10. Success Criteria

- [ ] User can focus brain on a single project with one click
- [ ] Brain CLI commands return filtered results when focused
- [ ] Brain prompt includes project-specific context
- [ ] User can switch focus without restarting brain
- [ ] No race conditions or state inconsistencies
- [ ] Implementation ≤10 hours
