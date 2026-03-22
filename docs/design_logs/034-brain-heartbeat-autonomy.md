# Brain Heartbeat & Autonomy

Give the brain periodic autonomous monitoring, a research mindset for unblocking workers, and long-term memory.

## Problem

The brain is **fully reactive** — it only acts when the user types something. Workers get stuck for hours with nobody noticing. The brain starts fresh every session with no memory of past patterns. The existing `/check_worker` skill requires manual triggering and waits for user approval before executing any action.

**Research basis**: Deep source code study of OpenClaw's heartbeat system (timer + priority wake queue + isolated sessions + transcript pruning) and Claude Code's `/loop` feature.

---

## Solution: Three Capabilities

### 1. Autonomous Monitoring (`/heartbeat` + `/loop`)

A new `/heartbeat` skill with a non-blocking operating model, scheduled via Claude Code's built-in `/loop` command:

| | `/check_worker` | `/heartbeat` |
|---|---|---|
| Trigger | User types it | `/loop` fires it automatically |
| Safe actions | Propose, wait for approval | **Execute immediately** |
| Notifications | Only on request | **Always — for user awareness** |
| Stuck workers | "Needs human" | **Investigate: research the error, attempt to help** |
| Output | Detailed table + approval prompt | Brief log of actions taken |

The brain sends `/loop {interval} /heartbeat` on startup when the `brain.heartbeat` setting is enabled. The setting is opt-in, off by default, with recommended 30-minute intervals.

**Heartbeat vs user input**: Claude Code's `/loop` fires between turns, so it waits if the user is chatting. If the heartbeat fires first, the user's message queues (10-30s). The 30m default and fast-path design minimize this friction.

### 2. Research Mindset (`/unblock`)

A standalone skill that shifts the brain from delegation mode to investigation mode. When a worker is stuck:

1. Deep read terminal output + task details
2. Classify blocker (technical error, missing context, decision paralysis, external dependency)
3. Research: search memory logs, search shared context, search repo
4. Send targeted help if confident, notify user if uncertain
5. Record learning log if the root cause is a pattern worth remembering

The `/heartbeat` skill calls this inline for stuck workers. Users can also invoke `/unblock <worker-name>` manually.

### 3. Long-Term Memory (`orch-memory`)

A dedicated CLI for the brain's private learning journal, separate from shared context (`orch-ctx`).

**Storage**: Reuses the existing `context_items` table with `scope=brain` and `category=memory|wisdom`. No new database table — the separation is at the CLI and UI layer.

**Two tiers:**

| Tier | Category | What it is | How it grows |
|------|----------|-----------|-------------|
| Learning logs | `memory` | Raw notes captured during work | Brain writes frequently via `orch-memory log "..."` |
| Wisdom | `wisdom` | Single curated document of high-quality insights | Brain distills from logs via `orch-memory wisdom-update`. Injected into system prompt via `{{BRAIN_MEMORY}}`. |

**CLI commands:**

```bash
orch-memory log "ECONNREFUSED = test DB slow to start"   # capture learning
orch-memory logs --search "ECONNREFUSED"                  # search past learnings
orch-memory wisdom                                        # view curated wisdom
orch-memory wisdom-update <<'EOF'                         # update wisdom from stdin
(curated learnings)
EOF
orch-memory delete-log <id>                               # delete one log
orch-memory clear-logs                                    # delete all logs
```

The `orch-memory` CLI is a thin wrapper over the existing `/api/context` endpoints with pre-set `scope=brain` and `category=memory|wisdom`. No new API routes needed.

**Pre-compaction hook**: A `PreCompact` hook prompts the brain to save learnings before context is wiped. The brain writes learning logs and optionally curates wisdom.

**Cross-scope learning**: Workers write project-scoped context. The brain reads it during investigation and promotes useful patterns into its own memory.

---

## Knowledge Architecture

Three systems with clear ownership:

| System | Owner | CLI | Purpose | User can... |
|--------|-------|-----|---------|------------|
| **Context** | User | `orch-ctx` | Shared knowledge (instructions, reference, conventions) | Create, edit, delete |
| **Memory** | Brain | `orch-memory` | Brain's private learnings + curated wisdom | **View only** |
| **Skills** | User | `orch-skills` | Reusable procedures (how to do things) | Create, edit, delete |

### Dashboard: Context Page Tabs

| Tab | Contents | Interaction |
|-----|----------|------------|
| **Context** | Global, project, brain-scoped context items (excludes memory/wisdom) | Full CRUD |
| **Brain Memory** | Wisdom document + learning logs | Read-only with search |

The Brain Memory tab is read-only because the brain owns its memory. The user can see what the brain has learned (transparency) but editing would undermine the learning process.

---

## Frontend: Settings

The auto-monitoring setting is under **Settings > Preferences > Brain** with a BETA badge and toggle switch. When enabled, an interval picker appears (combobox with presets + free text input). Warning note explains the behavior.

---

## Implementation

### Files Created

- `agents/brain/skills/heartbeat.md` — Non-blocking autonomous monitoring skill
- `agents/brain/skills/unblock.md` — Worker investigation skill
- `agents/brain/bin/orch-memory` — Brain memory CLI (wraps `/api/context`)
- `agents/brain/hooks/pre-compact.sh` — Pre-compaction learning flush
- `agents/brain/hooks/on-session-start.sh` — Re-deploy files + re-arm heartbeat after `/clear`
- `frontend/src/hooks/useBrainMemory.ts` — Read-only hook for brain memory tab

### Files Modified

- `agents/brain/prompt.md` — Identity section, research carve-out, `orch-memory` references, operational memory section
- `agents/brain/settings.json` — PreCompact + SessionStart hooks
- `orchestrator/agents/deploy.py` — `orch-memory` in BRAIN_SCRIPT_NAMES, `{{BRAIN_MEMORY}}` injection from `context_items`
- `orchestrator/api/routes/brain.py` — `/loop` scheduling on brain start, `POST /brain/redeploy` endpoint
- `orchestrator/config_defaults.py` — `brain.heartbeat: "off"` default
- `orchestrator/state/repositories/context.py` — Search now matches title + description only (not content body)
- `frontend/src/pages/ContextPage.tsx` — SlidingTabs with Context + Brain Memory tabs
- `frontend/src/pages/SettingsPage.tsx` — Brain section with heartbeat toggle + interval picker
- `frontend/src/hooks/useContextItems.ts` — `excludeScopeCategories` filter to hide memory/wisdom from Context tab

---

## Future Scope

| Idea | When to Reconsider |
|------|--------------------|
| Event-driven nudges | If 30m timer misses time-sensitive events |
| Dynamic intervals | If event nudges prove high-value |
| Semantic/vector search for memory | If learning logs grow to hundreds and keyword search is noisy |
| Self-modifying identity | If brain makes inconsistent judgment calls |
