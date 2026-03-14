# Skills Management Feature — Implementation Plan

## Context

The orchestrator has a skill system (markdown files deployed to `.claude/commands/`) but no UI visibility or management. Skills are hardcoded in `agents/brain/skills/` and `agents/worker/skills/`, and users must edit files on disk to customize agent behavior. This feature adds a Skills page to the dashboard, showing all built-in skills and allowing users to create/edit/delete custom skills stored in the database. Custom skills are auto-injected into agent prompts so agents know when to use them.

---

## Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Custom skill storage | SQLite database | User preference. Enables structured queries, UI-driven CRUD, and backup via existing DB backup system |
| Built-in skill source | Files in `agents/` (read-only) | Keep immutable core behaviors in source control |
| Name conflicts | Custom names cannot duplicate built-in names | Simplifies v1 — no override/merge logic needed |
| Prompt injection | Auto-inject custom skill names+descriptions | Agents need prompt guidance to know WHEN to use custom skills |
| Deploy timing | Session start only | Consistent with existing behavior; changes apply to new sessions |

---

## Step 1: Database Migration

**File**: `orchestrator/state/migrations/versions/028_add_skills.sql`

```sql
CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    target TEXT NOT NULL CHECK(target IN ('brain', 'worker')),
    description TEXT,
    content TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(name, target)
);
```

- `name`: slash-command slug (e.g., `deploy-checklist` → `/deploy-checklist`)
- `target`: `brain` or `worker`
- `content`: full markdown body (no YAML frontmatter — name/description stored as columns)
- Unique constraint on (name, target) prevents duplicates

---

## Step 2: Data Model

**File**: `orchestrator/state/models.py` — Add after `Notification` class:

```python
@dataclass
class Skill:
    id: str
    name: str
    target: str = "worker"     # "brain" | "worker"
    description: str | None = None
    content: str = ""
    created_at: str = ""
    updated_at: str = ""
```

---

## Step 3: Repository Layer

**New file**: `orchestrator/state/repositories/skills.py`

Follow the exact pattern from `orchestrator/state/repositories/context.py`:
- `get_skill(conn, id) -> Skill | None`
- `list_skills(conn, target=None, search=None) -> list[Skill]`
- `create_skill(conn, name, target, content, description=None) -> Skill`
- `update_skill(conn, id, **kwargs) -> Skill`
- `delete_skill(conn, id) -> bool`

**Name validation** in `create_skill`:
- Validate: `re.match(r'^[a-z][a-z0-9-]*$', name)` and `len(name) <= 50`
- Reject names that match built-in skill filenames (read from `agents/{target}/skills/`)
- Raise `ValueError` on invalid names (route converts to 400)

---

## Step 4: API Route

**New file**: `orchestrator/api/routes/skills.py`

Follow the exact pattern from `orchestrator/api/routes/context.py`:

| Method | Endpoint | Purpose | Notes |
|--------|----------|---------|-------|
| `GET` | `/api/skills` | List all skills | Returns both built-in (from filesystem) and custom (from DB). Query params: `target`, `search` |
| `GET` | `/api/skills/{skill_id}` | Get single custom skill | Full content. For built-in, use a special endpoint |
| `GET` | `/api/skills/builtin/{target}/{name}` | Get built-in skill content | Reads from `agents/{target}/skills/{name}.md`, parses frontmatter |
| `POST` | `/api/skills` | Create custom skill | Validates name, creates DB row |
| `PATCH` | `/api/skills/{skill_id}` | Update custom skill | Only custom skills are editable |
| `DELETE` | `/api/skills/{skill_id}` | Delete custom skill | Only custom skills are deletable |

**List endpoint merges two sources:**
1. Read built-in skills from `agents/{brain,worker}/skills/*.md` — parse YAML frontmatter for name/description, stat file for updated_at
2. Read custom skills from DB via repository
3. Return combined list with a `type` field: `"built_in"` or `"custom"`

**Serialization format:**
```json
{
  "id": "uuid-or-builtin:brain:create",
  "name": "create",
  "target": "brain",
  "type": "built_in",
  "description": "Analyze input, determine best placement...",
  "content": null,
  "line_count": 205,
  "created_at": "2026-02-20T10:30:00",
  "updated_at": "2026-02-20T10:30:00"
}
```
- `content` is `null` in list view (fetched on detail), present in single-item view
- `line_count` computed from content length
- Built-in skills use synthetic IDs like `builtin:brain:create`

**Built-in skill frontmatter parsing** (inline helper, no new dependency):
```python
import yaml  # already a dependency (pyyaml>=6.0)

def parse_skill_file(path: str) -> dict:
    with open(path) as f:
        text = f.read()
    if text.startswith('---'):
        parts = text.split('---\n', 2)
        if len(parts) >= 3:
            meta = yaml.safe_load(parts[1]) or {}
            body = parts[2]
            return {"name": meta.get("name", ""), "description": meta.get("description", ""), "content": body}
    return {"name": Path(path).stem, "description": "", "content": text}
```

**Event broadcasting** — publish after create/update/delete:
```python
from orchestrator.core.events import Event, publish
publish(Event(type="skill.changed", data={"action": "created", "name": name, "target": target}))
```

**Register in `orchestrator/api/app.py`**: Add `skills` to the route imports and `app.include_router(skills.router, prefix="/api", tags=["skills"])`.

---

## Step 5: Skill Deployment Integration

Custom skills must be deployed alongside built-in skills when sessions start. **Four deployment sites** need updating:

### 5a. Brain start (`orchestrator/api/routes/brain.py`)

After the existing built-in skill copy loop (lines 101-112), add:
```python
# Deploy custom brain skills from DB
from orchestrator.state.repositories import skills as skills_repo
custom_skills = skills_repo.list_skills(db, target="brain")
for skill in custom_skills:
    skill_path = os.path.join(skills_dest, f"{skill.name}.md")
    with open(skill_path, "w") as f:
        f.write(f"---\nname: {skill.name}\ndescription: {skill.description or ''}\n---\n\n{skill.content}")
```

Also update the prompt injection — after getting the brain prompt, replace `{{CUSTOM_SKILLS}}`:
```python
prompt = get_brain_prompt()
custom_skills_section = _format_custom_skills_section(custom_skills)
prompt = prompt.replace("{{CUSTOM_SKILLS}}", custom_skills_section)
```

### 5b. Local worker creation (`orchestrator/api/routes/sessions.py`)

After the existing built-in skill copy loop (lines 335-348), add the same custom skill deployment pattern. Also inject into worker prompt.

### 5c. Remote worker setup (`orchestrator/terminal/session.py:setup_remote_worker`)

This runs in a background thread without direct DB access. The solution:
- The calling function in `sessions.py` passes the custom skills list as a parameter to `setup_remote_worker()`
- Add parameter: `custom_skills: list[dict] = None`
- After copying built-in skills to `local_skills_dir` (line 520-531), also write custom skill files there
- They'll be transferred to remote along with everything else via SSH

### 5d. Worker reconnect (`orchestrator/session/reconnect.py`)

The reconnect functions need DB access for custom skills. Add a helper:
```python
def _get_custom_skills(db_path: str, target: str) -> list:
    """Read custom skills from DB (safe for background threads)."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT name, description, content FROM skills WHERE target = ?", (target,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
```

Use in `_regenerate_local_configs()` after built-in skill copy.

### 5e. Shared helper for prompt injection

**New function in `orchestrator/agents/deploy.py`**:
```python
def format_custom_skills_for_prompt(skills: list[dict]) -> str:
    """Format custom skills as a markdown list for prompt injection."""
    if not skills:
        return ""
    lines = ["\n### Custom Skills\n"]
    for s in skills:
        desc = s.get("description") or "No description"
        lines.append(f"- **`/{s['name']}`** — {desc}")
    return "\n".join(lines)
```

### 5f. Prompt template changes

**`agents/brain/prompt.md`** — Add at the end of the file:
```markdown
{{CUSTOM_SKILLS}}
```

**`agents/worker/prompt.md`** — Add after the existing Skills section (after line 104):
```markdown
{{CUSTOM_SKILLS}}
```

The `get_brain_prompt()` and `get_worker_prompt()` functions in `deploy.py` will be updated to accept a `custom_skills_section` parameter and replace the placeholder.

---

## Step 6: Frontend — Types

**File**: `frontend/src/api/types.ts` — Add:

```typescript
export interface Skill {
  id: string
  name: string
  target: 'brain' | 'worker'
  type: 'built_in' | 'custom'
  description: string | null
  content: string | null   // null in list view, populated on detail fetch
  line_count: number
  created_at: string
  updated_at: string
}
```

---

## Step 7: Frontend — useSkills Hook

**New file**: `frontend/src/hooks/useSkills.ts`

Follow the `useContextItems` pattern:
- `items: Skill[]` state with loading flag
- `fetch(target?)` — GET `/api/skills?target=brain|worker`
- `getItem(id)` — GET `/api/skills/{id}` or `/api/skills/builtin/{target}/{name}`
- `create(body)` — POST `/api/skills`
- `update(id, body)` — PATCH `/api/skills/{id}`
- `remove(id)` — DELETE `/api/skills/{id}`
- Filter locally by target tab (brain/worker)

---

## Step 8: Frontend — SkillsPage

**New files**: `frontend/src/pages/SkillsPage.tsx` + `SkillsPage.css`

Layout:
```
┌──────────────────────────────────────────────────────────────────┐
│  Skills                   [Brain | Worker]       [+ New Skill]   │
│                                                                  │
│  ┌─────────────────────────┐  ┌─────────────────────────┐       │
│  │ /create        BUILT-IN │  │ /check_worker   BUILT-IN │       │
│  │ Analyze input, determine│  │ Review all workers,      │       │
│  │ best placement across...│  │ produce a status summary │       │
│  │ ────────────────────    │  │ ────────────────────     │       │
│  │ 205 lines · 3d ago     │  │ 207 lines · 3d ago      │       │
│  └─────────────────────────┘  └─────────────────────────┘       │
│                                                                  │
│  ┌─────────────────────────┐                                     │
│  │ /deploy-check    CUSTOM │                                     │
│  │ Pre-deployment verify...│                                     │
│  │ ────────────────────    │                                     │
│  │ 84 lines · 1h ago      │                                     │
│  └─────────────────────────┘                                     │
└──────────────────────────────────────────────────────────────────┘
```

- Tab switching follows WorkersPage pattern (URL-based: `/skills` for brain, `/skills/worker` for worker)
- Card grid: `auto-fill` with `min(300px)` columns (matches worker card grid)
- Empty state when no custom skills: "No custom {brain/worker} skills yet."
- Built-in skills sorted first, then custom skills sorted by updated_at desc

---

## Step 9: Frontend — SkillCard

**New files**: `frontend/src/components/skills/SkillCard.tsx` + `SkillCard.css`

- Header: `/name` in monospace + type badge (BUILT-IN purple, CUSTOM green)
- Body: description truncated to 3 lines
- Footer: line count + relative timestamp
- Click → opens SkillModal
- Hover → subtle border color change (same as worker cards)

---

## Step 10: Frontend — SkillModal

**New files**: `frontend/src/components/skills/SkillModal.tsx` + `SkillModal.css`

Follow `ContextModal` pattern with these modes:

**View mode** (built-in skills):
- Read-only display of name, target, description
- Rendered markdown content (using existing `Markdown` component)
- Footer: "Duplicate as Custom" button + Close
- "Duplicate as Custom" pre-fills create form with the content (new name required)

**Edit mode** (custom skills):
- Editable name field (slug validation: `^[a-z][a-z0-9-]*$`)
- Target radio: Brain / Worker
- Description textarea
- Content: monospace textarea with Edit/Preview toggle
- Footer: Delete (with ConfirmPopover) | Cancel | Save

**Create mode** (new skill):
- Same as edit mode but empty fields
- Target defaults to current tab selection
- Starter template pre-filled in content:
  ```
  # Skill Title

  Description of what this skill does.

  ## Procedure

  ### Step 1: ...
  ```

Modal size: `extraWide` (1000px max-width) — the content area needs room.

---

## Step 11: Frontend — Navigation & Routing

**`frontend/src/components/common/Icons.tsx`** — Add `IconSkills`:
```tsx
// Zap/lightning bolt icon (represents capabilities/power)
export function IconSkills(props: IconProps) {
  return (
    <Icon {...props}>
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
    </Icon>
  )
}
```

**`frontend/src/components/sidebar/Sidebar.tsx`** — Add between Context and Notifications:
```tsx
<SidebarItem to="/skills" icon={<IconSkills size={18} />} label="Skills" collapsed={collapsed} shortcut="S" />
```

**`frontend/src/App.tsx`** — Add routes:
```tsx
import SkillsPage from './pages/SkillsPage'
// ...
<Route path="/skills" element={<SkillsPage />} />
<Route path="/skills/worker" element={<SkillsPage />} />
```

---

## Step 12: Update UI Design Doc

**File**: `docs/005-ui-design.md` — Add Skills page section after Context Page section, documenting the page layout, card design, and modal patterns.

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| **Skill name as filename** — user input becomes a `.md` filename on deploy | Strict slug validation (`^[a-z][a-z0-9-]*$`, max 50 chars) at API layer. No path separators possible. |
| **Name collision with built-in** | API rejects names matching existing built-in skill filenames. Check at create time by scanning `agents/{target}/skills/`. |
| **Concurrent file writes during deploy** | Skill files are written to temp directories per-session. No shared state — each session gets its own copy. |
| **DB connection in background threads** | Use standalone `sqlite3.connect(db_path)` for reconnect/remote-deploy threads. Read-only queries are safe. |
| **Stale skills in running sessions** | Document in UI: "Changes apply to new sessions." Existing sessions keep their deployed skills. |
| **Packaged app paths** | `agents_dir()` resolves correctly in both dev and PyInstaller mode. Custom skills come from DB so no filesystem path issue. |
| **Migration on existing installs** | Migration 028 uses `CREATE TABLE IF NOT EXISTS` — safe for fresh and existing installs. No data transformation needed. |
| **YAML parsing edge cases** | Built-in frontmatter parsing uses `yaml.safe_load()` with fallback — if parsing fails, skill is still shown with filename as name. |
| **Large skill content** | No hard limit but textarea has reasonable defaults. Content is markdown text — unlikely to be problematically large. |

---

## Files Summary

### New files (10)
| File | Purpose |
|------|---------|
| `orchestrator/state/migrations/versions/028_add_skills.sql` | DB migration |
| `orchestrator/state/repositories/skills.py` | Repository CRUD |
| `orchestrator/api/routes/skills.py` | REST API endpoints |
| `frontend/src/hooks/useSkills.ts` | Data fetching hook |
| `frontend/src/pages/SkillsPage.tsx` | Skills page |
| `frontend/src/pages/SkillsPage.css` | Skills page styles |
| `frontend/src/components/skills/SkillCard.tsx` | Card component |
| `frontend/src/components/skills/SkillCard.css` | Card styles |
| `frontend/src/components/skills/SkillModal.tsx` | View/edit modal |
| `frontend/src/components/skills/SkillModal.css` | Modal styles |

### Modified files (14)
| File | Change |
|------|--------|
| `orchestrator/state/models.py` | Add `Skill` dataclass |
| `orchestrator/api/app.py` | Register skills router |
| `orchestrator/agents/deploy.py` | Add `format_custom_skills_for_prompt()` helper, update prompt getters |
| `orchestrator/api/routes/brain.py` | Deploy custom skills + inject into prompt |
| `orchestrator/api/routes/sessions.py` | Deploy custom skills + inject into prompt |
| `orchestrator/terminal/session.py` | Accept + deploy custom skills for remote workers |
| `orchestrator/session/reconnect.py` | Deploy custom skills on reconnect |
| `agents/brain/prompt.md` | Add `{{CUSTOM_SKILLS}}` placeholder |
| `agents/worker/prompt.md` | Add `{{CUSTOM_SKILLS}}` placeholder |
| `frontend/src/App.tsx` | Add skills routes |
| `frontend/src/api/types.ts` | Add `Skill` interface |
| `frontend/src/components/common/Icons.tsx` | Add `IconSkills` |
| `frontend/src/components/sidebar/Sidebar.tsx` | Add Skills nav item |
| `docs/005-ui-design.md` | Add Skills page documentation |

---

## Implementation Order

1. **Backend data layer** — Migration, model, repository (Steps 1-3)
2. **Backend API** — Route + registration (Step 4)
3. **Deployment integration** — All 4 deploy sites + prompt injection (Step 5)
4. **Frontend skeleton** — Types, hook, page, routing, sidebar (Steps 6-8, 11)
5. **Frontend components** — SkillCard + SkillModal (Steps 9-10)
6. **Polish** — Design doc update, edge case testing (Step 12)

---

## Verification

1. **API smoke test**: `curl http://localhost:8093/api/skills` returns merged built-in + custom list
2. **Create custom skill**: POST to `/api/skills`, verify it appears in list
3. **Brain deploy**: Start brain, check `/tmp/orchestrator/brain/.claude/commands/` contains both built-in and custom `.md` files
4. **Worker deploy**: Create a worker, verify `{work_dir}/.claude/commands/` has custom skills
5. **Prompt injection**: Read the deployed `prompt.md` and confirm custom skills section is present
6. **UI walkthrough**: Navigate to `/skills`, switch tabs, create/edit/delete custom skills, verify cards update
7. **Name validation**: Try creating a skill named `create` (built-in name) — should be rejected with 400
8. **Existing sessions unaffected**: Create a skill while a worker is running — worker should NOT get the new skill until restarted
