# Implementation Plan: Claude Orchestrator

**Version:** 2.0
**Author:** Yudong Qiu
**Date:** February 7, 2026
**Status:** Ready for Implementation

---

## Table of Contents

1. [Overview](#1-overview)
2. [Technical Stack](#2-technical-stack)
3. [Project Structure](#3-project-structure)
4. [Phase 1: Foundation](#4-phase-1-foundation)
5. [Phase 2: Terminal & Sessions](#5-phase-2-terminal--sessions)
6. [Phase 3: API & Dashboard](#6-phase-3-api--dashboard)
7. [Phase 4: LLM Brain & Intelligence](#7-phase-4-llm-brain--intelligence)
8. [Phase 5: Advanced Features](#8-phase-5-advanced-features)
9. [Testing Strategy](#9-testing-strategy)
10. [Development Workflow](#10-development-workflow)

---

## 1. Overview

### 1.1 Implementation Goals

Build a fully functional Claude Orchestrator that:
- Manages multiple Claude Code sessions via tmux
- Provides a web dashboard for monitoring, decisions, and project management
- Routes commands and decisions between user and sessions
- Uses an LLM brain for intelligent reasoning about session state
- Learns from user feedback over time
- Contains zero hard-coded domain context — all context lives in the DB

### 1.2 Guiding Principles

| Principle | Description |
|-----------|-------------|
| **Zero Hard-Coded Context** | All domain knowledge in DB. The orchestrator is a general-purpose engine. |
| **Simplicity First** | Start with minimal viable features per phase, iterate |
| **Local Only** | Everything runs on user's Mac, no cloud dependencies beyond LLM API |
| **No rdev Changes** | Remote environments only need SSH access |
| **Fail Gracefully** | Handle errors without crashing; persist state on every mutation |
| **Explicit Actions** | Always confirm before executing commands (unless Autonomous Mode) |
| **Test as You Build** | Each phase includes its own tests. Playwright for dashboard. |

### 1.3 Environment

| Dependency | Available Version |
|------------|-------------------|
| Python | 3.13.7 |
| tmux | 3.6a |
| uv | installed (`/usr/local/bin/uv`) |
| SQLite | 3.51.0 |
| Node.js | 25.1.0 (for Playwright E2E tests) |

---

## 2. Technical Stack

### 2.1 Core Technologies

| Component | Technology | Rationale |
|-----------|------------|-----------|
| Language | Python 3.13 | Latest stable, good async support |
| Package Manager | uv | Fast, reliable, lockfile support |
| Terminal Control | tmux 3.6a | Universal, scriptable, multi-client attachable |
| State Storage | SQLite (WAL mode) | Zero config, portable, crash-safe |
| Web Framework | FastAPI | Native async, WebSocket via Starlette, Pydantic validation, auto OpenAPI docs |
| WebSocket | Starlette native | No extra dependency (comes with FastAPI), simpler than Socket.IO |
| LLM Client | Anthropic SDK | Direct API access, structured tool use |
| CLI Framework | Click + Rich | Beautiful terminal UI, command completion |
| Config | YAML | Human-readable bootstrap config |
| Vector Store | ChromaDB | Lightweight, embedded, good for RAG (Phase 5) |
| E2E Testing | Playwright | Screenshots + HTML capture for iterative UI development |

### 2.2 Python Dependencies

```toml
[project]
name = "claude-orchestrator"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    # CLI & Display
    "click>=8.0",
    "rich>=13.0",

    # Web Server & API
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.34.0",

    # LLM & HTTP
    "anthropic>=0.42.0",
    "httpx>=0.27.0",

    # Config & Data
    "pyyaml>=6.0",
    "python-dateutil",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24.0",
    "httpx>=0.27.0",           # FastAPI test client
    "ruff>=0.8.0",
    "mypy>=1.13.0",
]
e2e = [
    "playwright>=1.49.0",
]
knowledge = [
    "chromadb>=0.6.0",
]

[project.scripts]
orchestrator = "orchestrator.main:cli"
```

### 2.3 Bootstrap Config (config.yaml)

```yaml
# Bootstrap configuration — only infrastructure settings, NOT domain context.
# All domain context (projects, tasks, sessions, templates) lives in the DB.

server:
  host: "127.0.0.1"
  port: 8093

database:
  path: "data/orchestrator.db"    # Relative to project root

tmux:
  session_name: "orchestrator"    # tmux session name

anthropic:
  # API key is read from macOS Keychain, NOT stored here.
  model: "claude-sonnet-4-20250514"
  max_tokens: 4096

monitoring:
  poll_interval_seconds: 5        # Passive monitor default interval
  heartbeat_timeout_seconds: 120  # Mark session stale after this
  reconciliation_interval_seconds: 300  # Full state reconciliation

logging:
  level: "INFO"
  file: "data/orchestrator.log"
```

---

## 3. Project Structure

Aligned with PRD Section 8.7. See PRD for full tree and dependency rules.

```
orchestrator/
├── pyproject.toml
├── config.yaml
├── orchestrator/
│   ├── __init__.py
│   ├── __main__.py                # python -m orchestrator
│   ├── main.py                    # App init, DI wiring, CLI entry
│   ├── core/                      # Orchestration engine
│   │   ├── orchestrator.py        # Main event loop, coordination
│   │   ├── lifecycle.py           # Startup, shutdown, recovery
│   │   └── events.py              # Internal event bus (pub/sub)
│   ├── auth/                      # Keychain + token
│   ├── terminal/                  # tmux, SSH, skill installer, output parser
│   ├── comm/                      # MCP server, hooks, heartbeat, reconciler
│   ├── state/                     # DB, models, repositories/, migrations/
│   ├── scheduler/                 # Task matching, dependencies, conflicts
│   ├── llm/                       # Brain, context selector, actions, templates
│   ├── recovery/                  # Detector, snapshot, rebrief
│   ├── cost/                      # Tracker, budget, reports
│   ├── api/                       # FastAPI app, routes/, WebSocket, middleware
│   ├── web/                       # Dashboard (templates/, static/)
│   └── knowledge/                 # ChromaDB, learning (Phase 5)
├── tests/
│   ├── conftest.py
│   ├── unit/
│   ├── integration/
│   └── e2e/                       # Playwright tests with screenshot capture
├── scripts/
│   ├── seed_db.py                 # Populate DB with default config + templates
│   └── test_auth.py               # Auth testing (exists)
└── docs/
    ├── PRD.md
    └── IMPLEMENTATION.md
```

---

## 4. Phase 1: Foundation

**Goal:** Scaffolding, database, config, models, seed data, basic CLI shell. Everything needed for other phases to build on.

**Deliverable:** `python -m orchestrator` launches a CLI shell. Database is created with schema. Seed data is loaded. User can run `/help`, `/config`.

### 4.1 Tasks

#### 4.1.1 Project Scaffolding
- Create `pyproject.toml` with all dependencies
- Create package structure (`orchestrator/` with all `__init__.py` files)
- Create `__main__.py` entry point
- Create `config.yaml` bootstrap config
- Set up `uv` virtual environment

#### 4.1.2 Database & Migrations
- `state/db.py` — SQLite connection with WAL mode, query helpers
- `state/migrations/runner.py` — Migration runner (detect version, apply pending `.sql` files)
- `state/migrations/versions/001_initial.sql` — Full schema from PRD Section 8.6 (all tables)
- Test: migration runner creates DB from scratch, idempotent re-runs

#### 4.1.3 Data Models
- `state/models.py` — Python dataclasses for all entities:
  - `Session`, `Project`, `Task`, `TaskDependency`, `Decision`, `PullRequest`
  - `Activity`, `WorkerCapability`, `TaskRequirement`, `CostEvent`
  - `Config`, `PromptTemplate`, `SkillTemplate`, `SessionSnapshot`

#### 4.1.4 Repositories (Data Access Layer)
- `state/repositories/config.py` — get/set config by key, list by category
- `state/repositories/templates.py` — CRUD for prompt_templates + skill_templates
- `state/repositories/sessions.py` — CRUD for sessions + worker_capabilities
- `state/repositories/projects.py` — CRUD for projects + project_workers
- `state/repositories/tasks.py` — CRUD for tasks + dependencies + requirements
- `state/repositories/decisions.py` — Create, respond, dismiss, list pending
- `state/repositories/pull_requests.py` — CRUD + PR dependencies
- `state/repositories/activities.py` — Append-only event log, query by project/session/time
- Each repository: plain functions taking a `sqlite3.Connection`, returning dataclasses

#### 4.1.5 Seed Script
- `scripts/seed_db.py` — Populate DB with:
  - Default config values (approval policies, context weights, monitoring intervals, budget ceiling)
  - Default prompt templates (system_prompt, status_query, task_planning, rebrief)
  - Default skill template (orchestrator integration skill from PRD 7.9.2)
  - Schema version record

#### 4.1.6 Auth Module
- `auth/keychain.py` — Read/write Anthropic API key from macOS Keychain
- `auth/token.py` — Token validation, caching, refresh detection

#### 4.1.7 CLI Shell
- `main.py` — App initialization: load config, open DB, run migrations, wire dependencies
- Basic CLI with Click + Rich:
  - `/help` — show available commands
  - `/config` — show current configuration
  - `/status` — show "no sessions" placeholder
  - Shell loop with Rich prompt and formatted output

### 4.2 Tests (Phase 1)
- `tests/unit/test_models.py` — Dataclass creation, serialization
- `tests/unit/test_config_repo.py` — Config get/set/list
- `tests/integration/test_migrations.py` — Fresh DB creation, re-run idempotency
- `tests/integration/test_repositories.py` — CRUD for all repositories against real SQLite
- `tests/integration/test_seed.py` — Seed script populates expected data

### 4.3 Exit Criteria
- [ ] `python -m orchestrator` launches CLI shell
- [ ] Database created with full schema on first run
- [ ] Seed data loaded (config, templates)
- [ ] All Phase 1 tests pass
- [ ] `/help`, `/config`, `/status` commands work

---

## 5. Phase 2: Terminal & Sessions

**Goal:** tmux session management, SSH, passive monitor, skill installation. The orchestrator can create sessions, connect to rdevs, monitor terminal output, and install the /orchestrator skill.

**Deliverable:** User can `/add` a session, see it in `/list`, view output with `/output`, and send messages with `/send`. Passive monitoring detects session states.

### 5.1 Tasks

#### 5.1.1 tmux Manager
- `terminal/manager.py` — tmux operations:
  - `create_session()` — `tmux new-session -d -s orchestrator` (if not exists)
  - `create_window(name)` — `tmux new-window -t orchestrator -n <name>`
  - `list_windows()` — parse `tmux list-windows`
  - `kill_window(name)` — remove a window
  - `capture_output(window, lines=50)` — `tmux capture-pane -p -t <target>`
  - `send_keys(window, text)` — `tmux send-keys -t <target> "<text>" Enter`

#### 5.1.2 SSH Connection
- `terminal/ssh.py` — SSH wrapper:
  - `connect(host, window)` — send `ssh <host>` to tmux window
  - `health_check(window)` — detect if SSH is alive (prompt detection)
  - `setup_tunnel(host, local_port, remote_port)` — reverse tunnel for API access

#### 5.1.3 Session Lifecycle
- `terminal/session.py` — Full session lifecycle:
  - `create_session(name, host, mp_path)` → create window, SSH, cd to path
  - `start_claude_code(name)` → send `claude` command to tmux window
  - `remove_session(name)` → update DB, optionally kill window
  - Wire to `state/repositories/sessions.py` for persistence

#### 5.1.4 Output Parser (Tier 1: Regex)
- `terminal/output_parser.py` — Pattern detection on captured terminal output:
  - Detect Claude Code prompt (idle state)
  - Detect "All tests pass" / test failures
  - Detect "PR #NNN created" / PR URLs
  - Detect error stack traces
  - Detect build success/failure
  - Returns structured `OutputEvent` objects

#### 5.1.5 Passive Monitor
- `terminal/monitor.py` — Background async task:
  - Poll each session's terminal output at configurable interval
  - Run through output_parser
  - Detect state changes (idle → working, working → error, etc.)
  - Update session status in DB
  - Publish events to internal event bus
  - Adaptive polling: faster for active sessions, slower for idle

#### 5.1.6 Skill Installer
- `terminal/skill_installer.py` — Install /orchestrator skill:
  - `check_skill_exists(session)` → check for `.claude/commands/orchestrator.md`
  - `install_skill(session)` → type instruction into Claude Code via send_keys
  - `check_skill_version(session)` → parse version marker from skill file
  - `update_skill(session)` → type update instruction
  - Load skill template from DB, render with session variables

#### 5.1.7 CLI Session Commands
- Add to CLI:
  - `/add <name> <host> [path]` — create session, SSH, start Claude Code, install skill
  - `/remove <name>` — remove session
  - `/list` — show all sessions with status table (Rich)
  - `/status [name]` — detailed session status
  - `/output <name> [lines]` — show recent terminal output
  - `/send <name> <message>` — type message into session
  - `/attach <name>` — print tmux attach command for user

### 5.2 Tests (Phase 2)
- `tests/unit/test_output_parser.py` — Pattern detection against sample terminal outputs
- `tests/integration/test_terminal_manager.py` — Real tmux operations (create/list/capture/send)
- `tests/integration/test_skill_installer.py` — Skill check/install against mock Claude Code session

### 5.3 Exit Criteria
- [ ] `/add test-session local /tmp/test` creates a tmux window
- [ ] `/list` shows sessions with color-coded status
- [ ] `/output test-session` shows terminal content
- [ ] `/send test-session "hello"` types into the session
- [ ] Passive monitor detects state changes
- [ ] Skill installer creates `.claude/commands/orchestrator.md` in session
- [ ] All Phase 2 tests pass

---

## 6. Phase 3: API & Dashboard

**Goal:** FastAPI server with REST endpoints and WebSocket, web dashboard with session cards, decision queue, project management. Playwright E2E tests.

**Deliverable:** User opens `http://localhost:8093` and sees a live dashboard with session cards, decision queue, and chat. WebSocket pushes real-time updates.

### 6.1 Tasks

#### 6.1.1 FastAPI App
- `api/app.py` — App factory:
  - FastAPI app with CORS middleware
  - Mount static files from `web/static/`
  - Mount templates from `web/templates/`
  - Include route modules
  - Lifespan handler: start passive monitor, open DB

#### 6.1.2 API Routes
- `api/routes/sessions.py` — Session CRUD + send/takeover/release
- `api/routes/projects.py` — Project CRUD + task management
- `api/routes/tasks.py` — Task CRUD + status updates + assignment
- `api/routes/decisions.py` — Decision queue: list, respond, dismiss
- `api/routes/reporting.py` — `/api/report`, `/api/decision`, `/api/guidance`, `/api/hook`
- `api/routes/chat.py` — Chat endpoint (placeholder, wired to LLM in Phase 4)
- `api/routes/costs.py` — Cost summary endpoints
- `api/routes/prs.py` — PR listing + dependency graph
- `api/routes/health.py` — System health + comm channel status
- `api/middleware.py` — Request logging, optional auth token

#### 6.1.3 WebSocket
- `api/websocket.py` — Native Starlette WebSocket:
  - `/ws` endpoint
  - Broadcast session status updates on state changes
  - Broadcast new decisions, activities, PR updates
  - Subscribe to internal event bus, push to connected clients

#### 6.1.4 Dashboard Frontend
- `web/templates/index.html` — SPA shell (single HTML file, vanilla JS)
- `web/static/app.js` — Dashboard JavaScript:
  - Session grid with color-coded cards (green/yellow/red/blue)
  - Decision queue panel with approve/dismiss buttons
  - Project list with progress bars
  - Activity timeline (recent events)
  - Chat interface (messages → `/api/chat`)
  - WebSocket connection for real-time updates
  - Terminal takeover via xterm.js
- `web/static/styles.css` — Clean, functional styling
- `web/static/xterm.min.js` — Terminal emulator for takeover mode

#### 6.1.5 Direct tmux Access Feature
- Add prominent "Attach Terminal" button per session in dashboard
- Display `tmux attach -t orchestrator` command in the UI header
- Document in `/help` output: "Run `tmux attach -t orchestrator` from any terminal to browse sessions directly"

#### 6.1.6 CLI Integration
- Update CLI to optionally start the web server alongside the shell
- `/dashboard` command — start/stop the web server
- Status bar in CLI shows dashboard URL when running

### 6.2 Tests (Phase 3)
- `tests/integration/test_api_routes.py` — All API endpoints with httpx test client
- `tests/integration/test_websocket.py` — WebSocket connect, receive updates
- `tests/e2e/test_dashboard.py` — Playwright tests:
  - Load dashboard, verify session cards render
  - Add a session via API, verify card appears via WebSocket
  - Create a decision, verify it appears in queue
  - Click approve on a decision, verify it resolves
  - **Screenshot capture**: every test step saves a screenshot to `tests/e2e/screenshots/`
  - **HTML dump**: capture `page.content()` for Claude Code to inspect
  - **Console log capture**: browser JS errors saved to file

### 6.3 Playwright Test Setup
```python
# tests/e2e/conftest.py
import pytest
from playwright.sync_api import Page

@pytest.fixture(autouse=True)
def screenshot_on_failure(page: Page, request):
    """Capture screenshot after every test for Claude Code inspection."""
    yield
    screenshot_path = f"tests/e2e/screenshots/{request.node.name}.png"
    page.screenshot(path=screenshot_path, full_page=True)

@pytest.fixture
def dashboard(page: Page):
    """Navigate to dashboard and wait for load."""
    page.goto("http://localhost:8093")
    page.wait_for_selector("[data-testid='session-grid']", timeout=5000)
    return page
```

### 6.4 Exit Criteria
- [ ] `http://localhost:8093` loads dashboard
- [ ] Session cards render with real-time status
- [ ] Decision queue shows pending decisions with approve/dismiss
- [ ] WebSocket pushes updates without page reload
- [ ] Chat interface sends messages (placeholder response in Phase 3)
- [ ] Playwright E2E tests pass with screenshots
- [ ] All Phase 3 tests pass

---

## 7. Phase 4: LLM Brain & Intelligence

**Goal:** Wire up the LLM brain for intelligent chat, context selection, action execution, task scheduling, and session recovery.

**Deliverable:** User chats with the orchestrator in natural language. The brain reasons about session state, suggests actions, and executes approved commands. Sessions auto-recover from context loss.

### 7.1 Tasks

#### 7.1.1 LLM Client
- `llm/client.py` — Anthropic API wrapper:
  - `call(system_prompt, messages, tools?)` → structured response
  - Token counting, cost tracking (log to `cost_events` table)
  - Retry with backoff on rate limits
  - Load API key from auth module (Keychain)

#### 7.1.2 Context Selector
- `llm/context_selector.py` — Smart context selection (PRD Section 8.5.5):
  - `select_context(query, token_budget)` → assembled context string
  - Category A (always include): system state summary, active sessions, pending decisions
  - Category B (score and select): session details, task details, decision history
  - Category C (compact summaries): completed tasks, old decisions, inactive sessions
  - Scoring: `relevance * 0.35 + recency * 0.25 + status * 0.20 + urgency * 0.10 + connection * 0.10`
  - Weights loaded from DB config (not hard-coded)

#### 7.1.3 Brain
- `llm/brain.py` — Core reasoning engine:
  - `process_query(user_message)` → action list + summary
  - `process_state_change(event)` → optional action list
  - Assemble context via context_selector
  - Load system prompt from DB (`prompt_templates` table)
  - Parse LLM response into structured actions
  - Tiered intelligence: check if output_parser can handle it first

#### 7.1.4 Action Executor
- `llm/actions.py` — Execute parsed actions:
  - `send_message(session, message)` → tmux send_keys
  - `assign_task(session, task_id)` → update DB + notify session
  - `create_task(project_id, title, description)` → insert to DB
  - `update_task(task_id, status)` → update DB
  - `respond_decision(decision_id, response)` → update DB + send to session
  - `alert_user(message, urgency)` → push to dashboard WebSocket
  - `rebrief_session(session)` → compose context + send to session
  - Approval check: consult config for which actions need approval

#### 7.1.5 Template Renderer
- `llm/templates.py` — Load templates from DB, substitute `${variables}`

#### 7.1.6 Task Scheduler
- `scheduler/matcher.py` — Match tasks to workers by capability
- `scheduler/scheduler.py` — Priority queue: pick next task for idle workers
- `scheduler/dependencies.py` — Dependency graph: resolve order, detect cycles, cascade blockers

#### 7.1.7 Session Recovery
- `recovery/detector.py` — Detect /compact, restart, crash via passive monitor signals
- `recovery/snapshot.py` — Periodically save session context snapshots to DB
- `recovery/rebrief.py` — Compose re-brief message from snapshot, send via tmux

#### 7.1.8 Wire Chat Endpoint
- Update `api/routes/chat.py` to use brain for real responses

### 7.2 Tests (Phase 4)
- `tests/unit/test_context_selector.py` — Scoring, budget fitting, category assignment
- `tests/unit/test_actions.py` — Action parsing and validation
- `tests/unit/test_matcher.py` — Worker capability matching
- `tests/unit/test_dependencies.py` — Dependency graph resolution, cycle detection
- `tests/integration/test_brain.py` — Brain with mock LLM client

### 7.3 Exit Criteria
- [ ] Chat in dashboard produces intelligent responses about session state
- [ ] Actions are proposed and require approval before execution
- [ ] Task scheduler assigns tasks to idle workers by capability
- [ ] Session recovery detects /compact and re-briefs
- [ ] Context selector fits within token budget for 10+ sessions
- [ ] All Phase 4 tests pass

---

## 8. Phase 5: Advanced Features

**Goal:** Cost tracking dashboard, cross-session communication, PR dependency graph, audit/replay, conflict detection, vector store learning.

**Deliverable:** Full-featured orchestrator with cost visibility, cross-session coordination, and learning from decision history.

### 8.1 Tasks

#### 8.1.1 Cost Tracking
- `cost/tracker.py` — Log cost events on every LLM call
- `cost/budget.py` — Budget enforcement: check ceiling, emit alerts via WebSocket
- `cost/reports.py` — Cost reports by session, project, time period
- Dashboard: cost panel showing spend breakdown

#### 8.1.2 Cross-Session Communication
- Mediated messaging: orchestrator relays information between sessions
- Dependency notification: when Session A completes a task, notify Session B if dependent
- Dashboard: show cross-session message flow

#### 8.1.3 Conflict Detection
- `scheduler/conflicts.py` — Detect overlapping file paths between workers
- Alert when two sessions modify same files
- Dashboard: conflict warning panel

#### 8.1.4 PR Dependency Management
- Model PR merge ordering in DB
- Dashboard: PR dependency graph visualization
- Alert when upstream PR has failing CI

#### 8.1.5 Audit & Replay
- Immutable event log (already built in Phase 1 via activities table)
- Replay UI: step through project history chronologically
- Export as markdown/JSON

#### 8.1.6 Knowledge & Learning (ChromaDB)
- `knowledge/vectors.py` — Store decision embeddings
- `knowledge/learning.py` — Extract patterns from decision history
- Wire to context_selector for RAG-enhanced context

#### 8.1.7 Autonomous Mode
- Config-driven autonomy levels per action type
- Dashboard toggle: Advisory Mode ↔ Autonomous Mode
- Audit log for all autonomous actions

### 8.2 Exit Criteria
- [ ] Cost dashboard shows spend by session/project
- [ ] Budget alerts fire when approaching ceiling
- [ ] Cross-session messaging works
- [ ] Conflict detection warns on overlapping file changes
- [ ] PR dependency graph renders in dashboard
- [ ] Replay view works for completed projects
- [ ] All Phase 5 tests pass

---

## 9. Testing Strategy

### 9.1 Test Pyramid

| Level | Count Target | What | How |
|-------|-------------|------|-----|
| Unit | 60% | Models, parsers, selectors, matchers, actions | pytest, no DB/network |
| Integration | 30% | Repositories, API routes, WebSocket, tmux ops | pytest + real SQLite + mock tmux |
| E2E | 10% | Full dashboard flows, session lifecycle | Playwright + running server |

### 9.2 Playwright Workflow for Dashboard Development

Claude Code develops the dashboard iteratively using this loop:

```
1. Edit HTML/JS/CSS
2. Run: uvicorn orchestrator.api.app:create_app --reload
3. Run: npx playwright test tests/e2e/
4. Read: tests/e2e/screenshots/*.png     ← Claude Code sees the UI
5. Read: tests/e2e/output/console.log    ← Claude Code sees JS errors
6. Read: tests/e2e/output/page.html      ← Claude Code sees DOM structure
7. Fix issues, go to step 1
```

Every Playwright test:
- Saves a **full-page screenshot** after each test step
- Captures **browser console logs** to a file
- Dumps **page HTML** for structural inspection
- Generates a **JSON test report** with pass/fail details

This gives Claude Code full visibility into the UI without needing a live browser.

### 9.3 Test Fixtures

```python
# tests/conftest.py

@pytest.fixture
def db():
    """In-memory SQLite DB with schema applied."""
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn)
    seed_defaults(conn)
    yield conn
    conn.close()

@pytest.fixture
def mock_tmux(tmp_path):
    """Mock tmux that records send_keys calls."""
    # Records all commands sent, returns canned output for capture_pane
    ...

@pytest.fixture
def api_client(db):
    """FastAPI test client with real DB."""
    app = create_app(db=db)
    with TestClient(app) as client:
        yield client
```

---

## 10. Development Workflow

### 10.1 Per-Phase Workflow

Each phase follows this cycle:

1. **Create module files** with interfaces (empty functions, docstrings, type hints)
2. **Write tests** for the module (test-first where feasible)
3. **Implement** the module
4. **Run tests** — fix until green
5. **Integration check** — verify the module works with previously built modules
6. **Commit** with descriptive message

### 10.2 Running the Orchestrator During Development

```bash
# Terminal 1: Run the orchestrator (API + monitor)
cd /Users/yuqiu/projects/my_assistant/orchestrator
uv run uvicorn orchestrator.api.app:create_app --factory --reload --port 8093

# Terminal 2: Run tests
cd /Users/yuqiu/projects/my_assistant/orchestrator
uv run pytest tests/ -v

# Terminal 3: Run E2E tests (Phase 3+)
cd /Users/yuqiu/projects/my_assistant/orchestrator
uv run playwright install chromium  # once
uv run pytest tests/e2e/ -v

# Any terminal: Attach to tmux sessions
tmux attach -t orchestrator
```

### 10.3 Key Commands

```bash
# Setup (once)
cd /Users/yuqiu/projects/my_assistant/orchestrator
uv sync
uv run python scripts/seed_db.py

# Run
uv run python -m orchestrator          # CLI mode
uv run uvicorn orchestrator.api.app:create_app --factory --reload  # API mode

# Test
uv run pytest tests/unit/ -v           # Fast unit tests
uv run pytest tests/integration/ -v    # Integration tests
uv run pytest tests/e2e/ -v            # E2E (requires running server)
uv run ruff check orchestrator/        # Lint
uv run mypy orchestrator/              # Type check
```

---

*Document Version History*

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-07 | Yudong Qiu | Initial draft (Flask + Socket.IO, 4 phases) |
| 2.0 | 2026-02-07 | Yudong Qiu | Complete rewrite aligned with PRD v1.4. FastAPI + native WebSocket. 5 phases. Skill-based approach (no CLAUDE.md). Playwright E2E workflow. Zero hard-coded context. Updated project structure, dependencies, test strategy, and development workflow. |
