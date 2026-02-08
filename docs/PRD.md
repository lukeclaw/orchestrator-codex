# Claude Orchestrator — Product Requirements Document

**Version:** 2.0
**Author:** Yudong Qiu
**Date:** February 7, 2026

---

## 1. The Problem

I run many Claude Code sessions in parallel — each in its own terminal or rdev (remote dev) instance, each working on a single MP (repo). Together they can produce dozens of PRs per day. But this workflow has four acute pain points:

### 1.1 No Visibility

I have N terminals open. Each Claude Code session is doing something — working on a task, waiting for me, stuck on an error, or idle. There is no single place to see which worker is doing what. I have to manually switch between terminals to check.

### 1.2 Context Is Lost When Workers Restart

Every time I start a new worker — or one crashes, compacts context, or gets killed — I need to manually share the project context and tell it what to do again. The knowledge of "what this project is, what's been done, what's left" lives in my head, not in a system the worker can read from.

### 1.3 No Centralized Task Tracking

Each Claude Code session tracks its own sub-tasks locally (e.g., a `tracker.md` file). PRs it created, tasks it completed — all siloed in that one session. I have no centralized view of which tasks are done, which PRs were created/merged, and what's left across all workers.

### 1.4 I Am the Bottleneck

Claude Code stops and waits for me to take action. Many of these are trivial — "should I continue?" after doing 10 of 100 PRs, or permission prompts that just need a "yes." I am the chokepoint for N parallel workers. Every minute I don't respond is a minute a worker sits idle.

### 1.5 The Goal

**Shift my role from terminal babysitter to strategic decision-maker.**

I define projects with context. I break them into tasks. I assign tasks to workers. Workers report progress back to the system. Trivial approvals are handled automatically. I only intervene for real decisions.

At the end of the day: run many terminals and rdev sessions at the same time and get the most out of them.

---

## 2. Conceptual Model

```
PROJECT          = A high-level initiative with a goal (e.g., "Migrate auth to OAuth 2.0")
  TASK           = A unit of work assignable to one worker (e.g., "Add OAuth callback in voyager-web")
    WORKER       = A Claude Code session (terminal) that executes tasks
      PR         = Pull request created by a worker while executing a task
  DECISION       = A question requiring human input to proceed
  CONTEXT        = Persistent project knowledge that survives worker restarts
```

Hierarchy: `Project → Tasks → Workers → PRs`

A worker is assigned to one task at a time. When done, it picks up the next task from the project queue.

---

## 3. Core Capabilities

### 3.1 Dashboard — Single Pane of Glass

A web UI at `localhost:8080` showing:

- **All workers**: which is working, waiting, idle, or dead
- **All tasks**: kanban board (TODO / IN PROGRESS / DONE / BLOCKED)
- **All PRs**: created, in review, merged — linked to tasks
- **Decision queue**: pending items that need my input, sorted by urgency
- **Activity feed**: chronological log of what happened across all workers

### 3.2 Terminal Management

- Each worker maps to a tmux window
- Live terminal streaming via WebSocket (xterm.js in the browser)
- Send keystrokes, take over for manual intervention
- Workers can be local terminals or SSH into remote rdevs
- Persistent across orchestrator restarts (tmux survives)

### 3.3 Project & Task Management

- Create projects with name, description, context documents
- Break projects into tasks with dependencies
- Assign tasks to workers (manual or auto-assign idle workers)
- Workers report progress: task status, PRs created, blockers hit
- Centralized state in SQLite — workers read/write via API

### 3.4 Context Persistence

- Each project has stored context (goals, conventions, prior decisions)
- When a worker starts or recovers, it gets a "re-brief" with current task + context
- Context survives worker crashes, `/compact`, and restarts
- Zero context lives in my head — it's all in the system

### 3.5 Decision Queue & Auto-Approval

- Workers surface decisions via API when they need human input
- Dashboard shows pending decisions with context and urgency
- **Auto-approval rules**: trivial prompts (continue, permission) handled automatically
- I only see decisions that actually require judgment
- Goal: N workers running, I check in periodically instead of babysitting

### 3.6 Worker-to-Orchestrator Communication

Workers talk to the orchestrator via:

1. **REST API** (`curl` from the worker): report progress, request decisions, check for guidance
2. **Custom skill** (`/orchestrator` slash command installed in Claude Code): structured reporting
3. **Passive monitoring**: orchestrator polls tmux output to detect state changes
4. **Hooks**: Claude Code hooks fire on events (commits, PRs, errors)

These are layered — if one channel fails, others provide fallback.

### 3.7 Orchestration Engine

The backend runs a continuous orchestration loop:

```
Terminal Monitor (polls every 2-5s)
    → Output Parser (regex state detection: working/idle/waiting/error)
    → Event Bus (publishes session.state_changed, session.output, etc.)
    → Orchestrator (subscribes, dispatches actions):
        ├─ waiting → Auto-Approve Engine (check rules → send keystroke or create Decision)
        ├─ idle    → Scheduler (match idle worker to next ready task → send context)
        └─ recovery signal → Recovery Pipeline:
              ├─ Snapshot (capture current project/task/progress state)
              └─ Re-brief (compose + send context to worker via tmux)
```

**Terminal Monitor**: Background async loop that polls each session's tmux pane. Runs `capture-pane`, feeds output to the parser, publishes state-change events.

**Output Parser**: Regex-based detection of session state. Checks patterns in priority order: `waiting` (permission prompts, continue prompts) → `error` → `working` → `idle`. The `waiting` state is the key detection for Pain Point 1.4.

**Auto-Approve Engine**: Configurable rules stored in the config table (e.g., `auto_approve.tool_calls`, `auto_approve.continue_work`). When a session enters `waiting` state, checks recent output against enabled rules. If matched, sends the configured keystroke via tmux `send-keys`. If no rule matches, creates a Decision for human review.

**Recovery Pipeline**: Detects `/compact`, restart, or crash events. Creates a state snapshot (task, project, recent activities), then sends a re-brief message to the session with current context. The worker can resume without manual intervention.

**Scheduler**: When a worker becomes `idle`, checks for unassigned `ready` tasks using `get_next_assignments()`. Assigns the task, composes a context message (project + task + dependencies), and sends it to the worker.

---

## 4. Architecture

### 4.1 Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python, FastAPI, SQLite (WAL mode), tmux |
| Frontend | React, TypeScript, Vite, xterm.js |
| Communication | REST API, WebSocket, tmux send-keys |
| LLM | Anthropic API (for chat, planning, decision assistance) |

### 4.2 High-Level Diagram

```
┌──────────────────────────────────────────────────────┐
│  Browser (Dashboard)                                  │
│  React + xterm.js                                     │
└────────────────────────┬─────────────────────────────┘
                         │ HTTP / WebSocket
┌────────────────────────┴─────────────────────────────┐
│  Orchestrator Server (Python / FastAPI)                │
│                                                       │
│  ┌─────────┐ ┌──────────┐ ┌────────┐ ┌───────────┐  │
│  │ REST API│ │ Terminal  │ │ State  │ │ LLM Brain │  │
│  │         │ │ Manager   │ │ Store  │ │ (optional)│  │
│  │ sessions│ │ tmux ops  │ │ SQLite │ │ Anthropic │  │
│  │ tasks   │ │ SSH       │ │        │ │ API       │  │
│  │ projects│ │ capture   │ │        │ │           │  │
│  │ PRs     │ │ send-keys │ │        │ │           │  │
│  │decisions│ │ resize    │ │        │ │           │  │
│  └─────────┘ └──────────┘ └────────┘ └───────────┘  │
└──────────────────────────────────────────────────────┘
          │                        ▲
          │ tmux sessions          │ curl /api/report
          ▼                        │
┌──────────────────────────────────────────────────────┐
│  tmux session: orchestrator                           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐             │
│  │ Window 0 │ │ Window 1 │ │ Window 2 │  ...        │
│  │ worker-a │ │ worker-b │ │ worker-c │             │
│  │ claude   │ │ claude   │ │ claude   │             │
│  └──────────┘ └──────────┘ └──────────┘             │
└──────────────────────────────────────────────────────┘
```

### 4.3 API Endpoints

**Session management**
- `GET/POST /api/sessions` — list / create
- `GET/DELETE /api/sessions/:id` — get / remove
- `POST /api/sessions/:id/send` — send message to worker

**Project & task management**
- `GET/POST /api/projects` — list / create
- `GET/PATCH/DELETE /api/projects/:id` — get / update / remove
- `GET/POST /api/tasks` — list (with filters) / create
- `GET/PATCH/DELETE /api/tasks/:id` — get / update / remove

**PR tracking**
- `GET/POST /api/prs` — list / create
- `PATCH /api/prs/:id` — update status

**Decision queue**
- `GET /api/decisions` — list pending
- `POST /api/decisions` — create (worker requests decision)
- `POST /api/decisions/:id/respond` — respond
- `POST /api/decisions/:id/dismiss` — dismiss

**Worker reporting** (called by Claude Code sessions)
- `POST /api/report` — report event (progress, PR, error, completion)
- `GET /api/guidance` — check for pending instructions

**Activity log**
- `GET /api/activities` — list events with filters

**Chat**
- `POST /api/chat` — send message to orchestrator LLM brain
- `WS /ws` — real-time state updates
- `WS /ws/terminal/:id` — live terminal streaming

### 4.4 Data Model

```
sessions       (id, name, host, mp_path, status, tmux_window, last_activity)
projects       (id, name, description, status, context, created_at)
tasks          (id, project_id, title, description, status, assigned_session_id, dependencies)
prs            (id, session_id, task_id, repo, pr_number, url, status)
decisions      (id, session_id, task_id, question, context, urgency, status, response)
activities     (id, session_id, project_id, type, summary, details, created_at)
```

**Settings**
- `GET /api/settings` — list all config
- `PUT /api/settings` — update config values

### 4.5 What's Built (as of Feb 2026)

| Capability | Status | Notes |
|------------|--------|-------|
| Dashboard + session cards | Working | Live state from monitor (working/idle/waiting/error) |
| Terminal streaming | Working | xterm.js via WebSocket, send-keys, resize |
| Project/task CRUD | Working | Full REST API + UI |
| PR tracking | Working | API + UI, linked to sessions/tasks |
| Decision queue | Working | Create, respond, dismiss — UI + API |
| Activity feed | Working | Chronological log of all events |
| Terminal monitor | Working | Polls tmux, detects state via regex |
| Waiting detection | Working | Detects Claude Code permission/continue prompts |
| Auto-approve engine | Working | Configurable rules, sends keystrokes automatically |
| Recovery pipeline | Working | Snapshot + re-brief on compact/crash |
| Scheduler | Working | Auto-assigns idle workers to ready tasks |
| Settings UI | Working | General config + auto-approve toggles, persisted to DB |
| Chat (LLM brain) | Partial | API exists, no Anthropic integration yet |
| Skill installer | Built | `/orchestrator` slash command for Claude Code, not tested end-to-end |
| Reporting API | Built | `/api/report` endpoint, needs worker integration |

---

## 5. Key User Flows

### 5.1 Morning Startup

1. Start orchestrator: `orchestrator` (opens dashboard at localhost:8080)
2. Dashboard shows existing sessions, any orphaned from yesterday
3. Create new sessions or adopt existing tmux windows
4. Review overnight activity: PRs created, decisions pending

### 5.2 Assign Work

1. Create a project with description and context
2. Break into tasks (manually or LLM-assisted)
3. Assign tasks to workers
4. Workers receive task + context via send-keys or re-brief API
5. Workers execute and report progress back

### 5.3 Monitor Progress

1. Dashboard shows live status of all workers
2. Click any session to see terminal output, task progress, PRs
3. Decision queue shows items needing attention
4. Activity feed shows chronological events

### 5.4 Handle Decisions

1. Worker hits a decision point, reports via API
2. Decision appears in dashboard queue with context
3. I respond (or auto-approval rules handle trivial ones)
4. Response is routed back to the worker

### 5.5 Worker Recovery

1. Worker crashes or compacts context
2. Orchestrator detects via heartbeat / tmux monitoring
3. Re-creates tmux window if needed
4. Sends re-brief with current task, progress, and project context
5. Worker continues from where it left off

---

## 6. Non-Functional Requirements

| Category | Requirement |
|----------|-------------|
| Performance | Status query < 10s for 10+ sessions |
| Performance | Terminal capture < 2s per session |
| Reliability | State persists across orchestrator restarts |
| Reliability | Sessions survive orchestrator crashes (tmux is independent) |
| Security | All data local, no cloud dependencies beyond LLM API |
| Security | Never log tokens or credentials |
| Usability | First session added in < 2 minutes |
| Usability | Productive within 10 minutes |

---

## 7. Non-Goals (v1)

- Multi-user collaboration
- Automatic PR merging without human approval
- Jira / ticketing integration
- Mobile app
- Cross-session conflict resolution

---

## 8. Future Considerations

- **Autonomous mode**: configurable per-action auto-approval (e.g., auto-approve task assignment, always ask before sending messages)
- **NL project planning**: describe initiative in plain English, LLM decomposes into task graph
- **Cross-session communication**: orchestrator relays information between workers
- **Execution replay**: step through project history for debugging and learning
- **Cost tracking**: attribute API costs by project, task, and worker
- **Decision pattern learning**: analyze history to auto-approve repeated decision types

---

## Glossary

| Term | Definition |
|------|------------|
| **rdev** | Remote development environment (VM) |
| **MP** | Multiproduct — LinkedIn's term for a repository/service |
| **Worker** | A Claude Code session (terminal) that executes tasks |
| **Session** | Synonymous with Worker in the orchestrator context |
| **Decision** | A question from a worker requiring human input |
| **Re-brief** | Re-sending current task context to a session after context loss |
| **MCP** | Model Context Protocol — structured communication for Claude Code |
| **Skill** | Custom Claude Code slash command (e.g., `/orchestrator`) |
| **tmux** | Terminal multiplexer for managing multiple sessions |

---

*Version History*

| Version | Date | Changes |
|---------|------|---------|
| 1.0–1.5 | 2026-02-07 | Initial drafts with detailed specs |
| 2.0 | 2026-02-07 | Rewritten: pain points front and center, simplified from 3600 lines to focused requirements |
| 2.1 | 2026-02-07 | Added Section 3.7 (Orchestration Engine), Section 4.5 (What's Built), settings endpoint |
