# Product Requirements Document: Claude Orchestrator

**Version:** 1.5
**Author:** Yudong Qiu
**Date:** February 7, 2026
**Status:** Draft

---

## Table of Contents

0. [User Journey & Experience](#0-user-journey--experience) (Conceptual model, day-in-the-life walkthrough, UI wireframes, communication protocols, project management flows)
1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Goals & Success Metrics](#3-goals--success-metrics)
4. [User Personas](#4-user-personas)
5. [User Stories](#5-user-stories)
6. [Functional Requirements](#6-functional-requirements)
   - 6.1-6.9: Core modules (Auth, Terminal, State, LLM, Chat, API, Visualization, Project Mgmt, Dashboard)
   - 6.10: Communication Robustness (MCP, Hooks, Heartbeat, Reconciliation)
   - 6.11: Session Recovery & Context Preservation
   - 6.12: Worker Capability & Task Scheduling
   - 6.13: LLM Brain Design
   - 6.14: Cross-Session Communication
   - 6.15: Cost & Resource Management
   - 6.16: PR Dependency Management
   - 6.17: Replay & Audit
   - 6.18: Context Management (Zero Hard-Coded Context)
7. [Non-Functional Requirements](#7-non-functional-requirements)
   - 7.1-7.5: Performance, Reliability, Usability, Security, Maintainability
   - 7.6: Schema Migration Strategy
   - 7.7: Integration Strategy with Existing System
   - 7.8: Testing Strategy
   - 7.9: Skill Installation for Remote Sessions
8. [System Architecture](#8-system-architecture)
   - 8.0: Core Design Principles (Zero Hard-Coded Context, DB-Driven Everything, Smart Context Selection)
   - 8.1: High-Level Architecture
   - 8.2: Component Diagram
   - 8.3: API Endpoints
   - 8.4: Data Flow
   - 8.5: LLM Brain Design (Tiered Intelligence, Prompt Architecture, Action Schema, Smart Context Selection)
   - 8.6: State Schema
   - 8.7: Code Structure
9. [User Interface](#9-user-interface)
10. [Security & Privacy](#10-security--privacy)
11. [Risks & Mitigations](#11-risks--mitigations)
    - 11.1: Failure Scenarios & Recovery Playbooks
12. [Future Considerations](#12-future-considerations)
    - 12.4: Autonomy & Intelligence
    - 12.5: Observability & Review
    - 12.6: Cost Intelligence

---

## 0. User Journey & Experience

### 0.0 Conceptual Model

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  HIERARCHY OF CONCEPTS                                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│                        ┌─────────────────┐                                  │
│                        │    PROJECT      │                                  │
│                        │ "Voyager Login  │                                  │
│                        │   Refactor"     │                                  │
│                        └────────┬────────┘                                  │
│                                 │                                           │
│            ┌────────────────────┼────────────────────┐                      │
│            │                    │                    │                      │
│            ▼                    ▼                    ▼                      │
│     ┌─────────────┐      ┌─────────────┐      ┌─────────────┐              │
│     │   TASK      │      │   TASK      │      │   TASK      │              │
│     │ "OAuth      │      │ "Session    │      │ "Update     │              │
│     │  Callback"  │      │  Storage"   │      │  Tests"     │              │
│     └──────┬──────┘      └──────┬──────┘      └──────┬──────┘              │
│            │                    │                    │                      │
│            ▼                    ▼                    ▼                      │
│     ┌─────────────┐      ┌─────────────┐      ┌─────────────┐              │
│     │  WORKER     │      │  WORKER     │      │  WORKER     │              │
│     │ voyager-web │      │ identity-svc│      │ voyager-web │              │
│     │ (Session)   │      │ (Session)   │      │ (Session)   │              │
│     └──────┬──────┘      └──────┬──────┘      └──────┬──────┘              │
│            │                    │                    │                      │
│            ▼                    ▼                    ▼                      │
│     ┌─────────────┐      ┌─────────────┐      ┌─────────────┐              │
│     │    PRs      │      │    PRs      │      │    PRs      │              │
│     │ #123, #124  │      │ #456        │      │ #125        │              │
│     └─────────────┘      └─────────────┘      └─────────────┘              │
│                                                                             │
│  ═══════════════════════════════════════════════════════════════════════   │
│                                                                             │
│  PROJECT    = A high-level initiative with a goal and deadline             │
│  TASK       = A discrete unit of work that can be assigned to a worker     │
│  WORKER     = A Claude Code session (terminal) that executes tasks         │
│  PR         = Pull request created by a worker while executing a task      │
│  BLOCKER    = Something preventing a task from progressing                 │
│  DECISION   = A question requiring human input to proceed                  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 0.1 A Day with Claude Orchestrator

This section walks through how a Staff Engineer uses Claude Orchestrator to manage multiple AI coding sessions across remote and local development environments.

#### Scenario Setup

**User Profile:** Staff Engineer working on a large platform initiative  
**Sessions:**
- 5 remote rdevs: `voyager-web`, `payments-api`, `identity-service`, `notifications`, `analytics`
- 3 local terminals: `docs`, `scripts`, `scratch`

Each session runs Claude Code, and all 8 are orchestrated from a single dashboard.

---

### 0.2 Morning Startup Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 1: Start Orchestrator                                             │
│  ═══════════════════════════════════════════════════════════════════    │
│                                                                         │
│  $ orchestrator                                                         │
│                                                                         │
│  🎭 Claude Orchestrator v1.0                                            │
│  ─────────────────────────────────────────────────────────────────────  │
│  Checking for existing sessions...                                      │
│    ✓ Found 3 orphaned sessions from yesterday                          │
│    ✓ Adopting: voyager-web, payments-api, docs                         │
│                                                                         │
│  Dashboard starting at http://localhost:8080                            │
│  Press Ctrl+C to detach (sessions will continue running)               │
└─────────────────────────────────────────────────────────────────────────┘
```

#### Step 2: Dashboard Opens in Browser

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🎭 Claude Orchestrator                                    [+ New Session]   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─ SESSION GRID ─────────────────────────────────────────────────────────┐│
│  │                                                                        ││
│  │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐     ││
│  │  │ 🟢 voyager-web   │  │ 🟡 payments-api  │  │ 🔴 identity-svc  │     ││
│  │  │ ────────────────  │  │ ────────────────  │  │ ────────────────  │     ││
│  │  │ Remote (rdev)    │  │ Remote (rdev)    │  │ Remote (rdev)    │     ││
│  │  │                  │  │                  │  │                  │     ││
│  │  │ Task: Login UI   │  │ Task: DB Schema  │  │ Task: OAuth      │     ││
│  │  │ PRs: #123, #124  │  │ PRs: #456        │  │ PRs: -           │     ││
│  │  │                  │  │                  │  │                  │     ││
│  │  │ [View] [Takeover]│  │ [View] [Takeover]│  │ [View] [Takeover]│     ││
│  │  └──────────────────┘  └──────────────────┘  └──────────────────┘     ││
│  │                                                                        ││
│  │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐     ││
│  │  │ 🟢 notifications │  │ 🔵 analytics     │  │ ⚪ (empty)        │     ││
│  │  │ ────────────────  │  │ ────────────────  │  │ ────────────────  │     ││
│  │  │ Remote (rdev)    │  │ Remote (rdev)    │  │                  │     ││
│  │  │                  │  │                  │  │ [+ Add Session]  │     ││
│  │  │ Task: Push svc   │  │ Idle             │  │                  │     ││
│  │  │ PRs: #789-#791   │  │ PRs: #555 ✓      │  │                  │     ││
│  │  │                  │  │                  │  │                  │     ││
│  │  │ [View] [Takeover]│  │ [View] [Takeover]│  │                  │     ││
│  │  └──────────────────┘  └──────────────────┘  └──────────────────┘     ││
│  │                                                                        ││
│  │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐     ││
│  │  │ 🟢 docs          │  │ 🔵 scripts       │  │ 🔵 scratch       │     ││
│  │  │ ────────────────  │  │ ────────────────  │  │ ────────────────  │     ││
│  │  │ Local            │  │ Local            │  │ Local            │     ││
│  │  │                  │  │                  │  │                  │     ││
│  │  │ Task: API docs   │  │ Idle             │  │ Idle             │     ││
│  │  │ PRs: #999        │  │ PRs: -           │  │ PRs: -           │     ││
│  │  │                  │  │                  │  │                  │     ││
│  │  │ [View] [Takeover]│  │ [View] [Takeover]│  │ [View] [Takeover]│     ││
│  │  └──────────────────┘  └──────────────────┘  └──────────────────┘     ││
│  │                                                                        ││
│  └────────────────────────────────────────────────────────────────────────┘│
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌─ DECISIONS REQUIRING APPROVAL ───────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  ⚠️  payments-api: "Should I use PostgreSQL or MySQL for the new     │  │
│  │      user preferences service?"                                       │  │
│  │      [View Details] [Reply] [Dismiss]                                │  │
│  │                                                                       │  │
│  │  ⚠️  identity-service: "Test `test_token_refresh` is failing.        │  │
│  │      Should I skip it or investigate further?"                       │  │
│  │      [View Details] [Reply] [Dismiss]                                │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌─ ORCHESTRATOR CHAT ──────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  🤖 Good morning! I've adopted 3 sessions from yesterday.            │  │
│  │     Here's the overnight summary:                                    │  │
│  │     • voyager-web: Completed login refactor, created 2 PRs           │  │
│  │     • payments-api: Waiting for your DB decision                     │  │
│  │     • identity-service: Hit a test failure, paused                   │  │
│  │                                                                       │  │
│  │  🧑 Create new sessions for notifications and analytics              │  │
│  │                                                                       │  │
│  │  🤖 Creating 2 new remote sessions...                                │  │
│  │     ✓ notifications: Connected to rdev-notifications.linkedin.biz   │  │
│  │     ✓ analytics: Connected to rdev-analytics.linkedin.biz           │  │
│  │     Both sessions now have Claude Code running.                      │  │
│  │     What tasks should I assign to them?                              │  │
│  │                                                                       │  │
│  │  🧑 notifications: implement push notification service               │  │
│  │     analytics: wait for now, I'll assign later                       │  │
│  │                                                                       │  │
│  │  🤖 Got it.                                                          │  │
│  │     📤 Sending to notifications: "Implement push notification..."    │  │
│  │     ✓ analytics set to idle mode                                     │  │
│  │                                                                       │  │
│  ├───────────────────────────────────────────────────────────────────────┤  │
│  │  > Tell payments to use PostgreSQL and identity to investigate the   │  │
│  │    test failure, don't skip it                                       │  │
│  │                                                        [Send] [Voice]│  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 0.3 Session Card States

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  SESSION CARD STATES                                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  🟢 WORKING              🟡 WAITING               🔴 ERROR                  │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐      │
│  │ ████████████████ │    │ ████████████████ │    │ ████████████████ │      │
│  │ voyager-web      │    │ payments-api     │    │ identity-svc     │      │
│  │ ────────────────  │    │ ────────────────  │    │ ────────────────  │      │
│  │ Claude is active │    │ Needs decision   │    │ Error detected   │      │
│  │ typing...        │    │ ⚠️ Waiting 15min  │    │ 🔴 Test failure  │      │
│  │ ░░░░░░░░░░░░░░░░ │    │                  │    │                  │      │
│  │ [View] [Takeover]│    │ [View] [Respond] │    │ [View] [Takeover]│      │
│  └──────────────────┘    └──────────────────┘    └──────────────────┘      │
│                                                                             │
│  🔵 IDLE                 ⚫ DISCONNECTED          ⚪ NOT STARTED            │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐      │
│  │ ████████████████ │    │ ░░░░░░░░░░░░░░░░ │    │                  │      │
│  │ analytics        │    │ old-session      │    │ (empty slot)     │      │
│  │ ────────────────  │    │ ────────────────  │    │                  │      │
│  │ Task completed   │    │ SSH disconnected │    │ [+ Add Session]  │      │
│  │ Ready for next   │    │ [Reconnect]      │    │                  │      │
│  │                  │    │ [Remove]         │    │                  │      │
│  │ [View] [Assign]  │    │                  │    │                  │      │
│  └──────────────────┘    └──────────────────┘    └──────────────────┘      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 0.4 Clicking [View] - Session Detail Modal

When user clicks **[View]** on a session card:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  voyager-web                                              [X Close]         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─ SESSION INFO ───────────────────────────────────────────────────────┐  │
│  │  Type: Remote (rdev)                                                 │  │
│  │  Host: rdev-voyager.linkedin.biz                                     │  │
│  │  Path: /src/voyager-web                                              │  │
│  │  Status: 🟢 Working                                                  │  │
│  │  Started: 2 hours ago                                                │  │
│  │  Last Activity: 30 seconds ago                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ CURRENT TASK ───────────────────────────────────────────────────────┐  │
│  │  Implementing OAuth callback handler for login flow                  │  │
│  │  Progress: ████████░░ 80%                                            │  │
│  │  Subtasks:                                                           │  │
│  │    ✅ Create callback route                                          │  │
│  │    ✅ Implement token exchange                                       │  │
│  │    🔄 Add error handling (in progress)                               │  │
│  │    ⬜ Write tests                                                     │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ PULL REQUESTS ──────────────────────────────────────────────────────┐  │
│  │  #123 - Add OAuth callback endpoint        🟢 Approved  [Merge]      │  │
│  │  #124 - Implement token exchange logic     🟡 In Review [View]       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ TERMINAL OUTPUT (live) ─────────────────────────────────────────────┐  │
│  │  $ claude                                                            │  │
│  │  🤖 Claude: I've created the error handling for the OAuth callback.  │  │
│  │     The changes are in `src/auth/callback.ts`. I'm now running       │  │
│  │     the tests to verify...                                           │  │
│  │                                                                       │  │
│  │  Running: npm test -- --grep "oauth"                                 │  │
│  │  ✓ test_oauth_callback_success (245ms)                               │  │
│  │  ✓ test_oauth_callback_error (189ms)                                 │  │
│  │  ✓ test_oauth_token_refresh (312ms)                                  │  │
│  │                                                                       │  │
│  │  All tests passed! Creating PR for the error handling changes...     │  │
│  │  █                                                                    │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ ACTIONS ────────────────────────────────────────────────────────────┐  │
│  │  [🎮 Take Over Terminal]  [⏸️ Pause]  [📤 Send Message]  [🔄 Restart]  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 0.5 Clicking [Take Over] - Interactive Terminal

When user clicks **[Take Over]**, the modal becomes a full interactive terminal:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  voyager-web - INTERACTIVE MODE                           [X Exit Takeover] │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─ TERMINAL ───────────────────────────────────────────────────────────┐  │
│  │  $ claude                                                            │  │
│  │  🤖 Claude: I've created the error handling for the OAuth callback.  │  │
│  │     The changes are in `src/auth/callback.ts`. I'm now running       │  │
│  │     the tests to verify...                                           │  │
│  │                                                                       │  │
│  │  Running: npm test -- --grep "oauth"                                 │  │
│  │  ✓ test_oauth_callback_success (245ms)                               │  │
│  │  ✓ test_oauth_callback_error (189ms)                                 │  │
│  │  ✓ test_oauth_token_refresh (312ms)                                  │  │
│  │                                                                       │  │
│  │  All tests passed! Creating PR for the error handling changes...     │  │
│  │                                                                       │  │
│  │  🧑 You: Actually, let me check the error handling logic first       │  │
│  │                                                                       │  │
│  │  🤖 Claude: Sure! Here's the error handling I implemented:           │  │
│  │                                                                       │  │
│  │  ```typescript                                                       │  │
│  │  try {                                                               │  │
│  │    const token = await exchangeCodeForToken(code);                   │  │
│  │    // ...                                                            │  │
│  │  } catch (error) {                                                   │  │
│  │    if (error instanceof TokenExpiredError) {                         │  │
│  │  ```                                                                 │  │
│  │  █                                                                    │  │
│  │                                                                       │  │
│  ├───────────────────────────────────────────────────────────────────────┤  │
│  │  > Also add retry logic for network failures_                        │  │
│  │                                                            [Send ⏎]  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ⚠️  You are in takeover mode. Orchestrator is paused for this session.    │
│  [Return to Orchestrator] will resume automated management.                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 0.6 Adding a New Session

Clicking **[+ New Session]** or **[+ Add Session]** on an empty slot:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Add New Session                                               [X Cancel]   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Session Name: [ml-training                              ]                  │
│                                                                             │
│  ┌─ SESSION TYPE ───────────────────────────────────────────────────────┐  │
│  │  ○ Remote (SSH to rdev)                                              │  │
│  │  ● Local (terminal on this machine)                                  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ REMOTE SETTINGS (if remote) ────────────────────────────────────────┐  │
│  │  Host: [rdev-ml.linkedin.biz                          ]              │  │
│  │  SSH Options: [-o StrictHostKeyChecking=no            ]              │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ WORKING DIRECTORY ──────────────────────────────────────────────────┐  │
│  │  Path: [/src/ml-training                              ]              │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ INITIAL TASK (optional) ────────────────────────────────────────────┐  │
│  │  [Set up the ML training pipeline and optimize the hyperparameters  ]│  │
│  │  [                                                                   ]│  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ☑ Start Claude Code automatically                                         │
│  ☐ Register with orchestrator API (for active reporting)                   │
│                                                                             │
│                                          [Cancel]  [Create Session]         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 0.7 Orchestrator Lifecycle

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  ORCHESTRATOR LIFECYCLE                                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  STARTUP                                                                    │
│  ═══════                                                                    │
│                                                                             │
│  1. Check for existing tmux sessions from previous run                      │
│     └─► If found: Adopt sessions, restore state from database              │
│     └─► If not: Start fresh                                                 │
│                                                                             │
│  2. Start web dashboard server (localhost:8080)                             │
│                                                                             │
│  3. Initialize LLM client with token from Keychain                         │
│                                                                             │
│  4. Begin passive monitoring of all terminal outputs                        │
│                                                                             │
│  5. Start listening for active reports from Claude Code instances          │
│                                                                             │
│  ─────────────────────────────────────────────────────────────────────────  │
│                                                                             │
│  RUNNING                                                                    │
│  ═══════                                                                    │
│                                                                             │
│  • Web dashboard serves the UI                                              │
│  • Passive monitor: Polls terminal output every 5 seconds                   │
│  • Active listener: Receives API calls from Claude Code                     │
│  • LLM brain: Processes user queries, decides actions                       │
│  • Action executor: Sends commands to terminals                             │
│                                                                             │
│  ─────────────────────────────────────────────────────────────────────────  │
│                                                                             │
│  SHUTDOWN (Ctrl+C or dashboard close)                                       │
│  ════════                                                                   │
│                                                                             │
│  1. ⚠️  DO NOT kill tmux sessions (let them continue)                       │
│                                                                             │
│  2. Save current state to database                                          │
│     • Session list and metadata                                             │
│     • Pending decisions                                                     │
│     • Task progress                                                         │
│                                                                             │
│  3. Stop web dashboard                                                      │
│                                                                             │
│  4. Print message:                                                          │
│     "Orchestrator stopped. 8 sessions still running in tmux."              │
│     "Run 'orchestrator' to resume, or 'tmux attach -t orchestrator'"       │
│                                                                             │
│  ─────────────────────────────────────────────────────────────────────────  │
│                                                                             │
│  RESTART / RECONNECT                                                        │
│  ═══════════════════                                                        │
│                                                                             │
│  1. Find existing tmux sessions                                             │
│     $ tmux list-windows -t orchestrator                                     │
│                                                                             │
│  2. Match against saved state in database                                   │
│                                                                             │
│  3. For each matched session:                                               │
│     • Restore metadata (name, host, task)                                   │
│     • Check if Claude Code still running (passive monitor)                  │
│     • Re-register for active reporting                                      │
│                                                                             │
│  4. For orphaned sessions (in tmux but not in DB):                         │
│     • Prompt user: "Found unknown session 'window-3'. Adopt? [y/n]"        │
│                                                                             │
│  5. Resume normal operation                                                 │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 0.8 Claude Code ↔ Orchestrator Communication

The orchestrator uses **three complementary communication channels** to maximize reliability. No single channel is trusted alone — they reinforce each other.

| Channel | Direction | Mechanism | Reliability | Use Case |
|---------|-----------|-----------|-------------|----------|
| **Skill (Slash Command)** | Claude Code → Orchestrator | Custom `/orchestrator` skill installed once per session; Claude Code invokes it to report | High — skill persists across restarts; orchestrator types instructions to use it | Progress updates, PR creation, decision requests |
| **MCP Server** | Bidirectional | Orchestrator exposes MCP tools to Claude Code | High — structured tool interface, not free-text instructions | Preferred channel for structured events (replaces curl when available) |
| **Hooks** | Claude Code → Orchestrator | `.claude/hooks/` pre/post tool-call hooks | High — automatic, no LLM compliance needed | Auto-fire events on commit, PR creation, file changes |
| **Passive Monitoring** | Orchestrator → Terminal | `tmux capture-pane` + event-driven file watch | High — no cooperation from Claude Code needed | Fallback detection: errors, idle state, completion signals |
| **Commands** | Orchestrator → Claude Code | `tmux send-keys` | High — direct terminal input | Sending tasks, decisions, re-briefing, skill installation |

**Reconciliation principle:** The passive monitor always runs as a safety net. If active reporting or MCP events stop arriving, the passive monitor detects the gap and the orchestrator can re-brief the session or alert the user.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  COMMUNICATION PROTOCOL                                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─ ACTIVE REPORTING (Claude Code → Orchestrator) ──────────────────────┐  │
│  │                                                                       │  │
│  │  Claude Code in each session has an `/orchestrator` skill installed  │  │
│  │  that knows how to report to the orchestrator API. The skill is      │  │
│  │  created once during session setup by the orchestrator typing into   │  │
│  │  Claude Code like a real user (via tmux send-keys). The skill        │  │
│  │  persists in .claude/commands/ and survives restarts.                │  │
│  │                                                                       │  │
│  │  Skill-based reporting (Claude Code calls /orchestrator):            │  │
│  │                                                                       │  │
│  │  ### Report task progress                                            │  │
│  │  curl -X POST http://localhost:8080/api/report \                     │  │
│  │    -H "Content-Type: application/json" \                             │  │
│  │    -d '{"session": "$SESSION_NAME", "event": "task_progress",        │  │
│  │         "data": {"task": "...", "progress": 80, "subtasks": [...]}}'  │  │
│  │                                                                       │  │
│  │  ### Request a decision                                              │  │
│  │  curl -X POST http://localhost:8080/api/decision \                   │  │
│  │    -H "Content-Type: application/json" \                             │  │
│  │    -d '{"session": "$SESSION_NAME", "question": "...",               │  │
│  │         "options": ["A", "B"], "context": "..."}'                    │  │
│  │                                                                       │  │
│  │  ### Report PR created                                               │  │
│  │  curl -X POST http://localhost:8080/api/report \                     │  │
│  │    -d '{"session": "$SESSION_NAME", "event": "pr_created",           │  │
│  │         "data": {"url": "...", "title": "..."}}'                     │  │
│  │                                                                       │  │
│  │  ### Report error                                                    │  │
│  │  curl -X POST http://localhost:8080/api/report \                     │  │
│  │    -d '{"session": "$SESSION_NAME", "event": "error",                │  │
│  │         "data": {"message": "...", "stack": "..."}}'                 │  │
│  │                                                                       │  │
│  │  Always check for guidance before making important decisions:        │  │
│  │  curl http://localhost:8080/api/guidance?session=$SESSION_NAME       │  │
│  │  ```                                                                  │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ PASSIVE MONITORING (Orchestrator → Terminal) ───────────────────────┐  │
│  │                                                                       │  │
│  │  Orchestrator polls terminal output every 5 seconds using tmux:      │  │
│  │                                                                       │  │
│  │  $ tmux capture-pane -t orchestrator:voyager-web -p -S -50           │  │
│  │                                                                       │  │
│  │  Used to detect:                                                     │  │
│  │  • Claude Code stopped or waiting for input                          │  │
│  │  • Errors in terminal output                                         │  │
│  │  • Completion signals ("All tests passed", "PR created", etc.)       │  │
│  │  • Claude asking user questions (but not calling decision API)       │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ ORCHESTRATOR → CLAUDE CODE (Commands) ──────────────────────────────┐  │
│  │                                                                       │  │
│  │  Orchestrator sends commands via tmux send-keys:                     │  │
│  │                                                                       │  │
│  │  $ tmux send-keys -t orchestrator:voyager-web "implement the login   │  │
│  │    feature with OAuth support" Enter                                 │  │
│  │                                                                       │  │
│  │  Can also send Claude Code slash commands:                           │  │
│  │  $ tmux send-keys -t orchestrator:voyager-web "/status" Enter        │  │
│  │  $ tmux send-keys -t orchestrator:voyager-web "/compact" Enter       │  │
│  │                                                                       │  │
│  │  Or raw terminal commands:                                           │  │
│  │  $ tmux send-keys -t orchestrator:voyager-web "exit" Enter           │  │
│  │  $ tmux send-keys -t orchestrator:voyager-web "cd /src/api" Enter    │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 0.8.1 MCP Server Channel (Preferred)

The orchestrator exposes itself as an **MCP (Model Context Protocol) server** that Claude Code sessions connect to. This provides structured, typed tool calls instead of free-text curl instructions:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  MCP SERVER PROTOCOL (Orchestrator as MCP Server)                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  The orchestrator registers itself as an MCP server in each session's      │
│  Claude Code configuration (~/.claude/mcp_servers.json or per-project).    │
│                                                                             │
│  Available MCP Tools:                                                      │
│                                                                             │
│  ┌─ orchestrator_report_progress ──────────────────────────────────────┐  │
│  │  Parameters:                                                        │  │
│  │    task: string        - Current task description                   │  │
│  │    progress: number    - Percentage complete (0-100)                 │  │
│  │    subtasks: object[]  - List of {name, done} subtask objects        │  │
│  │    summary: string     - Brief status message                       │  │
│  │  Returns: {ack: true, guidance?: string}                            │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ orchestrator_report_pr ────────────────────────────────────────────┐  │
│  │  Parameters:                                                        │  │
│  │    url: string         - PR URL                                     │  │
│  │    title: string       - PR title                                   │  │
│  │    task_id: string?    - Associated task ID                         │  │
│  │  Returns: {ack: true, task_id: string}                              │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ orchestrator_request_decision ─────────────────────────────────────┐  │
│  │  Parameters:                                                        │  │
│  │    question: string    - Question for the user                      │  │
│  │    options: string[]   - Available choices                           │  │
│  │    context: string     - Background context                         │  │
│  │    urgency: enum       - low | normal | high | critical             │  │
│  │  Returns: {decision: string, notes: string}                         │  │
│  │  Note: This call BLOCKS until the user responds via dashboard       │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ orchestrator_get_guidance ─────────────────────────────────────────┐  │
│  │  Parameters: (none)                                                 │  │
│  │  Returns: {guidance?: string, task?: object, context?: string}      │  │
│  │  Claude Code should call this at the start of each major task       │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ orchestrator_report_error ─────────────────────────────────────────┐  │
│  │  Parameters:                                                        │  │
│  │    type: string        - Error category                             │  │
│  │    message: string     - Error description                          │  │
│  │    blocking: boolean   - Whether this blocks further progress       │  │
│  │  Returns: {ack: true, instruction?: string}                         │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  Advantages over curl-based active reporting:                              │
│  - Claude Code natively understands MCP tools (structured schema)          │
│  - No network routing issues (MCP uses stdio/SSE transport)                │
│  - Blocking calls (decisions) are handled natively                         │
│  - Tool results provide immediate feedback/guidance                        │
│  - No file injection needed — works alongside the skill approach           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 0.8.2 Hooks Channel (Automatic Events)

Claude Code supports **hooks** — shell commands that execute automatically in response to tool events. The orchestrator configures hooks on each session to fire events without relying on the LLM to remember instructions:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  HOOKS-BASED AUTOMATIC REPORTING                                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Configured in each session's .claude/settings.json:                       │
│                                                                             │
│  {                                                                          │
│    "hooks": {                                                               │
│      "post_tool_use": [                                                     │
│        {                                                                    │
│          "tool": "Bash",                                                    │
│          "pattern": "git commit|git push|gh pr create",                    │
│          "command": "curl -sX POST http://localhost:8080/api/hook \\        │
│            -H 'Content-Type: application/json' \\                           │
│            -d '{\"session\":\"$SESSION\",\"tool\":\"Bash\",                 │
│                 \"event\":\"post_tool_use\",                                │
│                 \"output\":\"$TOOL_OUTPUT\"}'"                              │
│        }                                                                    │
│      ],                                                                     │
│      "on_error": [                                                          │
│        {                                                                    │
│          "command": "curl -sX POST http://localhost:8080/api/hook \\        │
│            -d '{\"session\":\"$SESSION\",\"event\":\"error\",               │
│                 \"message\":\"$ERROR\"}'"                                   │
│        }                                                                    │
│      ]                                                                      │
│    }                                                                        │
│  }                                                                          │
│                                                                             │
│  Hooks automatically detect:                                                │
│  - Git commits and pushes                                                   │
│  - PR creation (via gh CLI)                                                 │
│  - Build/test failures                                                      │
│  - Claude Code errors                                                       │
│                                                                             │
│  Advantage: No LLM cooperation required — fires deterministically           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 0.8.3 Heartbeat & Reconciliation Protocol

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  HEARTBEAT & RECONCILIATION                                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Problem: Active reports (curl/MCP) can silently fail. A session may       │
│  stop reporting without the orchestrator knowing why.                       │
│                                                                             │
│  Solution: Multi-layer health monitoring with automatic recovery.           │
│                                                                             │
│  LAYER 1: Passive Heartbeat                                                │
│  ─────────────────────────                                                  │
│  The passive monitor tracks "last activity" per session.                   │
│  If no terminal output change for > 60 seconds on a "working" session:    │
│    → Mark session as "stale"                                               │
│    → Send /status to Claude Code to provoke a response                     │
│    → If still no response after 120s, mark as "unresponsive"              │
│                                                                             │
│  LAYER 2: State Reconciliation                                             │
│  ─────────────────────────────                                              │
│  Every 5 minutes, the orchestrator reconciles:                             │
│    1. Check tmux: Is the session window still alive?                       │
│    2. Check terminal: Is Claude Code still running? (detect prompt)        │
│    3. Check git: Any new commits/branches since last known state?          │
│    4. Check PRs: Any new PRs via gh/API since last known?                  │
│                                                                             │
│  If reconciliation finds events that weren't actively reported:            │
│    → Backfill the activity log                                              │
│    → Update task progress based on inferred state                          │
│    → Log a "missed event" for reliability tracking                         │
│                                                                             │
│  LAYER 3: Session Recovery                                                 │
│  ────────────────────────                                                   │
│  If a session is detected as crashed or restarted:                         │
│    1. Check if /orchestrator skill still exists; reinstall if missing      │
│    2. Re-register MCP server connection                                     │
│    3. Send "re-brief" message with current task context:                   │
│       "You are working on [task]. Progress so far: [summary].              │
│        Last known state: [state]. Please continue."                        │
│    4. Log recovery event in activity timeline                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 0.9 Decision Approval Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  DECISION APPROVAL FLOW                                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  SCENARIO: Claude Code in payments-api needs to choose a database           │
│                                                                             │
│  1. Claude Code calls decision API:                                         │
│     ┌─────────────────────────────────────────────────────────────────┐    │
│     │ POST /api/decision                                              │    │
│     │ {                                                               │    │
│     │   "session": "payments-api",                                    │    │
│     │   "question": "Should I use PostgreSQL or MySQL?",              │    │
│     │   "options": ["PostgreSQL", "MySQL"],                           │    │
│     │   "context": "Building new user preferences service. Need       │    │
│     │              JSON support and complex queries.",                │    │
│     │   "urgency": "normal"                                           │    │
│     │ }                                                               │    │
│     └─────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  2. Orchestrator receives, adds to decision queue                           │
│                                                                             │
│  3. Dashboard shows notification:                                           │
│     ┌─────────────────────────────────────────────────────────────────┐    │
│     │ ⚠️  payments-api needs your decision                             │    │
│     │ "Should I use PostgreSQL or MySQL?"                              │    │
│     │ [View Details]                                                   │    │
│     └─────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  4. User clicks [View Details]:                                             │
│     ┌─────────────────────────────────────────────────────────────────┐    │
│     │ Decision Required: payments-api                                  │    │
│     │ ────────────────────────────────────────────────────────────────│    │
│     │                                                                  │    │
│     │ Question: Should I use PostgreSQL or MySQL?                      │    │
│     │                                                                  │    │
│     │ Context:                                                         │    │
│     │ Building new user preferences service. Need JSON support         │    │
│     │ and complex queries. The service will handle ~10M users.         │    │
│     │                                                                  │    │
│     │ Options:                                                         │    │
│     │   ○ PostgreSQL                                                   │    │
│     │   ○ MySQL                                                        │    │
│     │   ○ Other: [                                    ]                │    │
│     │                                                                  │    │
│     │ Your response:                                                   │    │
│     │ [Use PostgreSQL for better JSON support and the JSONB           ]│    │
│     │ [data type. Also ensure you set up proper indexes.              ]│    │
│     │                                                                  │    │
│     │                           [Dismiss]  [Send Decision]             │    │
│     └─────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  5. User clicks [Send Decision]                                             │
│                                                                             │
│  6. Orchestrator sends to Claude Code:                                      │
│     $ tmux send-keys -t orchestrator:payments-api                          │
│       "Decision from user: Use PostgreSQL for better JSON support..."       │
│       Enter                                                                 │
│                                                                             │
│  7. Decision logged in history for learning                                 │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 0.10 Important Actions Requiring Approval

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  ACTIONS REQUIRING USER APPROVAL                                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  The orchestrator will ASK before performing these actions:                 │
│                                                                             │
│  ┌─ HIGH RISK (Always ask) ─────────────────────────────────────────────┐  │
│  │  • Merge a PR                                                        │  │
│  │  • Delete files or branches                                          │  │
│  │  • Deploy to production                                              │  │
│  │  • Modify database schema                                            │  │
│  │  • Kill/restart a session                                            │  │
│  │  • Send message to multiple sessions (broadcast)                     │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ MEDIUM RISK (Ask by default, can configure to auto-approve) ────────┐  │
│  │  • Create a PR                                                       │  │
│  │  • Run tests                                                         │  │
│  │  • Install dependencies                                              │  │
│  │  • Send task to a single session                                     │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ LOW RISK (Auto-approved, logged) ───────────────────────────────────┐  │
│  │  • Query session status                                              │  │
│  │  • Read terminal output                                              │  │
│  │  • Check PR status                                                   │  │
│  │  • Compact context                                                   │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  Example approval dialog in dashboard:                                      │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  🔐 Action Requires Approval                                        │   │
│  │  ─────────────────────────────────────────────────────────────────  │   │
│  │                                                                      │   │
│  │  Orchestrator wants to:                                             │   │
│  │                                                                      │   │
│  │  📤 Send message to voyager-web:                                    │   │
│  │     "Focus on implementing the OAuth error handling next.           │   │
│  │      Make sure to handle token expiration and refresh."             │   │
│  │                                                                      │   │
│  │  ☐ Don't ask again for similar actions                              │   │
│  │                                                                      │   │
│  │                              [Deny]  [Modify]  [Approve]            │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 0.11 Project Management Layer

### Story: Staff Engineer Starts a New Project

Yudong Qiu is starting a major initiative: **"Voyager Login Refactor"** - modernizing the authentication 
system across multiple services. This will require work in 4 repositories and generate 15-20 PRs.

#### Step 1: Create the Project

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🎭 Claude Orchestrator                              [Sessions] [Projects ●] │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─ NEW PROJECT ────────────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  Project Name: [Voyager Login Refactor                    ]          │  │
│  │                                                                       │  │
│  │  Description:                                                        │  │
│  │  [Modernize authentication across voyager-web, identity-service,    ]│  │
│  │  [payments-api, and notifications. Replace legacy cookie-based      ]│  │
│  │  [auth with OAuth 2.0 + JWT tokens.                                 ]│  │
│  │                                                                       │  │
│  │  Target Completion: [2026-02-21    ] (2 weeks)                       │  │
│  │                                                                       │  │
│  │  ┌─ INITIAL TASKS (parsed from description) ─────────────────────┐  │  │
│  │  │                                                                │  │  │
│  │  │  ☑ OAuth callback handler in voyager-web                      │  │  │
│  │  │  ☑ JWT token service in identity-service                      │  │  │
│  │  │  ☑ Update session storage in identity-service                 │  │  │
│  │  │  ☑ Migrate payments-api to new auth                           │  │  │
│  │  │  ☑ Update notifications service auth                          │  │  │
│  │  │  ☑ Integration tests across services                          │  │  │
│  │  │  ☐ [+ Add task                                            ]   │  │  │
│  │  │                                                                │  │  │
│  │  └────────────────────────────────────────────────────────────────┘  │  │
│  │                                                                       │  │
│  │  ┌─ ASSIGN WORKERS (sessions) ───────────────────────────────────┐  │  │
│  │  │                                                                │  │  │
│  │  │  Available Workers:                                           │  │  │
│  │  │  ☑ voyager-web      (Remote - rdev-voyager)                   │  │  │
│  │  │  ☑ identity-service (Remote - rdev-identity)                  │  │  │
│  │  │  ☑ payments-api     (Remote - rdev-payments)                  │  │  │
│  │  │  ☑ notifications    (Remote - rdev-notifications)             │  │  │
│  │  │  ☐ analytics        (Remote - rdev-analytics)                 │  │  │
│  │  │  ☐ docs             (Local)                                   │  │  │
│  │  │                                                                │  │  │
│  │  └────────────────────────────────────────────────────────────────┘  │  │
│  │                                                                       │  │
│  │                                    [Cancel]  [Create Project]         │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### Step 2: Project Dashboard - Overview

After creating the project, the user sees the project dashboard:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🎭 Claude Orchestrator                              [Sessions] [Projects ●] │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─ PROJECTS ───────────────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  ┌────────────────────────────────────────────────────────────────┐  │  │
│  │  │ 📁 Voyager Login Refactor                          [Active ●]  │  │  │
│  │  │ ──────────────────────────────────────────────────────────────  │  │  │
│  │  │                                                                 │  │  │
│  │  │  Progress: ████████░░░░░░░░░░░░ 40%   (4/10 tasks done)        │  │  │
│  │  │  Due: Feb 21 (12 days)                                         │  │  │
│  │  │                                                                 │  │  │
│  │  │  Workers: voyager-web, identity-service, payments-api, notifs  │  │  │
│  │  │  PRs: 8 open, 3 merged                                         │  │  │
│  │  │  Blockers: 1 ⚠️                                                 │  │  │
│  │  │                                                                 │  │  │
│  │  │                                              [View Project →]   │  │  │
│  │  └────────────────────────────────────────────────────────────────┘  │  │
│  │                                                                       │  │
│  │  ┌────────────────────────────────────────────────────────────────┐  │  │
│  │  │ 📁 Analytics Dashboard Redesign                    [Paused ○]  │  │  │
│  │  │ ──────────────────────────────────────────────────────────────  │  │  │
│  │  │                                                                 │  │  │
│  │  │  Progress: ████████████░░░░░░░░ 60%   (6/10 tasks done)        │  │  │
│  │  │  Due: Feb 28 (19 days)                                         │  │  │
│  │  │                                                                 │  │  │
│  │  │  Workers: analytics (paused)                                   │  │  │
│  │  │  PRs: 4 open, 6 merged                                         │  │  │
│  │  │  Blockers: 0                                                   │  │  │
│  │  │                                                                 │  │  │
│  │  │                                              [View Project →]   │  │  │
│  │  └────────────────────────────────────────────────────────────────┘  │  │
│  │                                                                       │  │
│  │                                                   [+ New Project]     │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ QUICK STATS ────────────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │   Active Projects: 1        Total Tasks: 20       Open PRs: 12       │  │
│  │   Active Workers:  4        Completed:   10       Merged:   9        │  │
│  │   Blockers:        1        In Progress: 6        Blocked:  2        │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 0.12 Project Detail View

Clicking **[View Project →]** shows the detailed project view:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🎭 Orchestrator > Projects > Voyager Login Refactor           [← Back]      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  📁 Voyager Login Refactor                                                  │
│  ══════════════════════════                                                 │
│  Modernize authentication across voyager-web, identity-service,             │
│  payments-api, and notifications. Replace legacy cookie-based auth          │
│  with OAuth 2.0 + JWT tokens.                                               │
│                                                                             │
│  ┌─ PROGRESS ───────────────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  Overall: ████████░░░░░░░░░░░░ 40%                                   │  │
│  │                                                                       │  │
│  │  By Service:                                                         │  │
│  │  voyager-web      ████████████████░░░░ 80%  (4/5 tasks)             │  │
│  │  identity-service ████████████░░░░░░░░ 60%  (3/5 tasks)             │  │
│  │  payments-api     ████░░░░░░░░░░░░░░░░ 20%  (1/5 tasks)             │  │
│  │  notifications    ░░░░░░░░░░░░░░░░░░░░  0%  (0/3 tasks)             │  │
│  │                                                                       │  │
│  │  Timeline:                                                           │  │
│  │  Feb 7 ─────●─────────────────────────────────────────── Feb 21     │  │
│  │        [Today]                                            [Due]      │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─ TASK BOARD ─────────────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │   TODO (4)          IN PROGRESS (3)      DONE (4)         BLOCKED(1) │  │
│  │   ──────────        ────────────────     ──────────       ────────── │  │
│  │                                                                       │  │
│  │  ┌───────────┐    ┌───────────────┐    ┌───────────┐    ┌──────────┐│  │
│  │  │Notif auth │    │🔄 JWT service │    │✅ OAuth   │    │⚠️ Session││  │
│  │  │update     │    │               │    │  callback │    │  storage ││  │
│  │  │           │    │identity-svc   │    │           │    │          ││  │
│  │  │           │    │PR #456 review │    │voyager-web│    │Waiting DB││  │
│  │  │[Assign →] │    └───────────────┘    │PR #123 ✓  │    │decision  ││  │
│  │  └───────────┘                         │PR #124 ✓  │    └──────────┘│  │
│  │                    ┌───────────────┐    └───────────┘                │  │
│  │  ┌───────────┐    │🔄 Payments    │                                 │  │
│  │  │Notif push │    │  migration    │    ┌───────────┐                │  │
│  │  │service    │    │               │    │✅ Token   │                │  │
│  │  │           │    │payments-api   │    │  exchange │                │  │
│  │  │           │    │PR #789 open   │    │           │                │  │
│  │  │[Assign →] │    └───────────────┘    │voyager-web│                │  │
│  │  └───────────┘                         │PR #125 ✓  │                │  │
│  │                    ┌───────────────┐    └───────────┘                │  │
│  │  ┌───────────┐    │🔄 Error       │                                 │  │
│  │  │Integration│    │  handling     │    ┌───────────┐                │  │
│  │  │tests      │    │               │    │✅ DB      │                │  │
│  │  │           │    │voyager-web    │    │  schema   │                │  │
│  │  │           │    │In progress... │    │           │                │  │
│  │  │[Assign →] │    └───────────────┘    │identity   │                │  │
│  │  └───────────┘                         │PR #450 ✓  │                │  │
│  │                                         └───────────┘                │  │
│  │  ┌───────────┐                                                       │  │
│  │  │Cleanup    │                                                       │  │
│  │  │legacy auth│                                                       │  │
│  │  │           │                                                       │  │
│  │  │[Assign →] │                                                       │  │
│  │  └───────────┘                                                       │  │
│  │                                                                       │  │
│  │                                                      [+ Add Task]    │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 0.13 Worker-Task Assignment View

The dashboard shows which worker is handling which task:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🎭 Orchestrator > Projects > Voyager Login Refactor > Workers  [← Back]     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─ WORKER ASSIGNMENTS ─────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │  │
│  │  │ 🟢 voyager-web                                     [View Term]  │ │  │
│  │  │ ───────────────────────────────────────────────────────────────  │ │  │
│  │  │                                                                  │ │  │
│  │  │  Current Task: Error handling for OAuth callback                │ │  │
│  │  │  Status: Working... (last activity 30s ago)                     │ │  │
│  │  │                                                                  │ │  │
│  │  │  ┌─ Tasks Assigned ─────────────────────────────────────────┐  │ │  │
│  │  │  │                                                          │  │ │  │
│  │  │  │  ✅ OAuth callback handler          PR #123 merged       │  │ │  │
│  │  │  │  ✅ Token exchange logic            PR #124 merged       │  │ │  │
│  │  │  │  ✅ Refresh token implementation    PR #125 merged       │  │ │  │
│  │  │  │  🔄 Error handling                  In progress...       │  │ │  │
│  │  │  │  ⬜ Cleanup legacy auth code        Not started          │  │ │  │
│  │  │  │                                                          │  │ │  │
│  │  │  └──────────────────────────────────────────────────────────┘  │ │  │
│  │  │                                                                  │ │  │
│  │  │  Stats: 4 tasks, 3 completed, 1 in progress                    │ │  │
│  │  │  PRs: 3 merged, 0 open                                          │ │  │
│  │  │                                                                  │ │  │
│  │  └─────────────────────────────────────────────────────────────────┘ │  │
│  │                                                                       │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │  │
│  │  │ 🟡 identity-service                                [View Term]  │ │  │
│  │  │ ───────────────────────────────────────────────────────────────  │ │  │
│  │  │                                                                  │ │  │
│  │  │  Current Task: JWT token service                                │ │  │
│  │  │  Status: Waiting for PR review (#456)                           │ │  │
│  │  │                                                                  │ │  │
│  │  │  ┌─ Tasks Assigned ─────────────────────────────────────────┐  │ │  │
│  │  │  │                                                          │  │ │  │
│  │  │  │  ✅ DB schema for tokens            PR #450 merged       │  │ │  │
│  │  │  │  🟡 JWT token service               PR #456 in review    │  │ │  │
│  │  │  │  ⛔ Session storage update          BLOCKED - decision   │  │ │  │
│  │  │  │                                                          │  │ │  │
│  │  │  └──────────────────────────────────────────────────────────┘  │ │  │
│  │  │                                                                  │ │  │
│  │  │  Stats: 3 tasks, 1 completed, 1 waiting, 1 blocked             │ │  │
│  │  │  PRs: 1 merged, 1 in review                                     │ │  │
│  │  │                                                                  │ │  │
│  │  └─────────────────────────────────────────────────────────────────┘ │  │
│  │                                                                       │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │  │
│  │  │ 🟢 payments-api                                    [View Term]  │ │  │
│  │  │ ───────────────────────────────────────────────────────────────  │ │  │
│  │  │                                                                  │ │  │
│  │  │  Current Task: Migrate to new auth system                       │ │  │
│  │  │  Status: Working... (writing migration code)                    │ │  │
│  │  │                                                                  │ │  │
│  │  │  ┌─ Tasks Assigned ─────────────────────────────────────────┐  │ │  │
│  │  │  │                                                          │  │ │  │
│  │  │  │  🔄 Migrate payments auth           PR #789 open         │  │ │  │
│  │  │  │  ⬜ Update payment webhooks         Not started          │  │ │  │
│  │  │  │                                                          │  │ │  │
│  │  │  └──────────────────────────────────────────────────────────┘  │ │  │
│  │  │                                                                  │ │  │
│  │  │  Stats: 2 tasks, 0 completed, 1 in progress                    │ │  │
│  │  │  PRs: 0 merged, 1 open                                          │ │  │
│  │  │                                                                  │ │  │
│  │  └─────────────────────────────────────────────────────────────────┘ │  │
│  │                                                                       │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │  │
│  │  │ 🔵 notifications                                   [View Term]  │ │  │
│  │  │ ───────────────────────────────────────────────────────────────  │ │  │
│  │  │                                                                  │ │  │
│  │  │  Current Task: (none assigned - idle)                           │ │  │
│  │  │  Status: Idle - ready for next task                             │ │  │
│  │  │                                                                  │ │  │
│  │  │  ┌─ Tasks Assigned ─────────────────────────────────────────┐  │ │  │
│  │  │  │                                                          │  │ │  │
│  │  │  │  (no tasks assigned yet)                                 │  │ │  │
│  │  │  │                                                          │  │ │  │
│  │  │  │  Unassigned tasks in this project:                       │  │ │  │
│  │  │  │  • Notification auth update      [Assign to this worker] │  │ │  │
│  │  │  │  • Notification push service     [Assign to this worker] │  │ │  │
│  │  │  │                                                          │  │ │  │
│  │  │  └──────────────────────────────────────────────────────────┘  │ │  │
│  │  │                                                                  │ │  │
│  │  │  Stats: 0 tasks assigned                                        │ │  │
│  │  │                                                                  │ │  │
│  │  └─────────────────────────────────────────────────────────────────┘ │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 0.14 Blockers & Decisions Panel

A dedicated view for understanding what's blocking progress:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🎭 Orchestrator > Projects > Voyager Login Refactor > Blockers [← Back]     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ⚠️  BLOCKERS & PENDING DECISIONS                                           │
│  ════════════════════════════════                                           │
│                                                                             │
│  ┌─ BLOCKING PROJECT PROGRESS ──────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │  │
│  │  │ ⛔ BLOCKER: Database Technology Decision                        │ │  │
│  │  │ ───────────────────────────────────────────────────────────────  │ │  │
│  │  │                                                                  │ │  │
│  │  │  Task: Session storage update                                   │ │  │
│  │  │  Worker: identity-service                                       │ │  │
│  │  │  Blocking since: 2 hours ago                                    │ │  │
│  │  │                                                                  │ │  │
│  │  │  Question: "Should I use PostgreSQL or Redis for session        │ │  │
│  │  │  storage? PostgreSQL offers durability but Redis is faster."    │ │  │
│  │  │                                                                  │ │  │
│  │  │  Context from worker:                                           │ │  │
│  │  │  • Current sessions table has 50M rows                          │ │  │
│  │  │  • Read:write ratio is 10:1                                     │ │  │
│  │  │  • Need TTL support for session expiration                      │ │  │
│  │  │                                                                  │ │  │
│  │  │  Options:                                                       │ │  │
│  │  │  ○ PostgreSQL (durability, SQL queries, existing infrastructure)│ │  │
│  │  │  ○ Redis (speed, built-in TTL, simpler scaling)                 │ │  │
│  │  │  ○ Both (Redis cache + PostgreSQL persistence)                  │ │  │
│  │  │                                                                  │ │  │
│  │  │  Your decision: [Use Redis with PostgreSQL backup for           ]│ │  │
│  │  │                 [critical sessions. TTL of 24h in Redis.        ]│ │  │
│  │  │                                                                  │ │  │
│  │  │  ☐ Apply to similar decisions in the future                     │ │  │
│  │  │                                                                  │ │  │
│  │  │                              [Skip]  [Need More Info]  [Decide] │ │  │
│  │  └─────────────────────────────────────────────────────────────────┘ │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ PENDING REVIEWS (not blocking yet) ─────────────────────────────────┐  │
│  │                                                                       │  │
│  │  🟡 PR #456 - JWT token service (identity-service)                   │  │
│  │     Waiting for review since: 4 hours ago                            │  │
│  │     Will block: Session storage update task                          │  │
│  │                                                     [View PR] [Ping] │  │
│  │                                                                       │  │
│  │  🟡 PR #789 - Migrate payments auth (payments-api)                   │  │
│  │     Waiting for review since: 1 hour ago                             │  │
│  │     Will block: Payment webhooks update task                         │  │
│  │                                                     [View PR] [Ping] │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ RESOLVED RECENTLY ──────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  ✅ "Use snake_case for JWT claims?" → Decided: Yes, for consistency │  │
│  │     Resolved: 3 hours ago by Yudong Qiu                                  │  │
│  │                                                                       │  │
│  │  ✅ "Include user roles in JWT?" → Decided: Yes, as array            │  │
│  │     Resolved: Yesterday                                              │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 0.15 Project Activity Timeline

A chronological view of what happened in the project:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🎭 Orchestrator > Projects > Voyager Login Refactor > Activity [← Back]     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  📜 PROJECT ACTIVITY TIMELINE                                               │
│  ═══════════════════════════                                                │
│                                                                             │
│  Filter: [All ▼]  Workers: [All ▼]  Date: [Today ▼]                        │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────────┐
│  │                                                                         │
│  │  TODAY - February 7, 2026                                              │
│  │  ────────────────────────                                              │
│  │                                                                         │
│  │  14:32  🟢 voyager-web                                                 │
│  │         Started task: Error handling for OAuth callback                │
│  │                                                                         │
│  │  14:15  📥 payments-api                                                │
│  │         PR #789 created: "Migrate payments-api to JWT auth"            │
│  │         → Auto-assigned reviewer: @alice                               │
│  │                                                                         │
│  │  13:45  ⚠️  identity-service                                           │
│  │         BLOCKED: Requesting decision on database technology            │
│  │         Question: "Should I use PostgreSQL or Redis?"                  │
│  │         → Awaiting your response                                       │
│  │                                                                         │
│  │  12:30  ✅ voyager-web                                                 │
│  │         Completed task: Refresh token implementation                   │
│  │         PR #125 merged                                                 │
│  │                                                                         │
│  │  11:00  📥 identity-service                                            │
│  │         PR #456 created: "Implement JWT token service"                 │
│  │         → Awaiting review (4 hours)                                    │
│  │                                                                         │
│  │  10:15  🧑 Yudong Qiu                                                      │
│  │         Decided: "Include user roles in JWT? → Yes, as array"         │
│  │         → Unblocked identity-service                                   │
│  │                                                                         │
│  │  09:00  🟢 payments-api                                                │
│  │         Started task: Migrate to new auth system                       │
│  │         Worker connected to project                                    │
│  │                                                                         │
│  │  ─────────────────────────────────────────────────────────────────────│
│  │                                                                         │
│  │  YESTERDAY - February 6, 2026                                          │
│  │  ──────────────────────────                                            │
│  │                                                                         │
│  │  17:30  ✅ voyager-web                                                 │
│  │         Completed task: Token exchange logic                           │
│  │         PR #124 merged                                                 │
│  │                                                                         │
│  │  15:00  ✅ identity-service                                            │
│  │         Completed task: DB schema for tokens                           │
│  │         PR #450 merged                                                 │
│  │                                                                         │
│  │  12:00  🆕 Project Created                                             │
│  │         "Voyager Login Refactor" with 10 initial tasks                 │
│  │         4 workers assigned: voyager-web, identity-service,             │
│  │         payments-api, notifications                                    │
│  │                                                                         │
│  └─────────────────────────────────────────────────────────────────────────┘
│                                                                             │
│                                              [Load More ↓]                  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 0.16 Orchestrator Chat with Project Context

The chat interface is project-aware:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🎭 Claude Orchestrator                              [Sessions] [Projects]   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─ CHAT (Project: Voyager Login Refactor) ─────────────────────────────┐  │
│  │                                                                       │  │
│  │  🧑 Yu: What's blocking this project right now?                      │  │
│  │                                                                       │  │
│  │  🤖 Orchestrator:                                                    │  │
│  │     Currently there's 1 blocker:                                     │  │
│  │                                                                       │  │
│  │     **Database Technology Decision** (identity-service)              │  │
│  │     The worker needs to decide between PostgreSQL and Redis for      │  │
│  │     session storage. This is blocking the "Session storage update"   │  │
│  │     task.                                                            │  │
│  │                                                                       │  │
│  │     Additionally, 2 PRs are waiting for review:                      │  │
│  │     • PR #456 (4 hours) - JWT token service                          │  │
│  │     • PR #789 (1 hour) - Payments migration                          │  │
│  │                                                                       │  │
│  │     Would you like me to:                                            │  │
│  │     1. Help you decide on the database question                      │  │
│  │     2. Ping reviewers for the pending PRs                            │  │
│  │     3. Assign the idle workers (notifications) to new tasks          │  │
│  │                                                                       │  │
│  │  🧑 Yu: Go with Redis + PostgreSQL backup. And yes, assign           │  │
│  │         notifications to start on the auth update.                   │  │
│  │                                                                       │  │
│  │  🤖 Orchestrator:                                                    │  │
│  │     Got it. I'll:                                                    │  │
│  │                                                                       │  │
│  │     ✅ Send decision to identity-service:                            │  │
│  │        "Use Redis with PostgreSQL backup for critical sessions."     │  │
│  │                                                                       │  │
│  │     ⏳ Assigning to notifications:                                   │  │
│  │        Task: "Notification auth update"                              │  │
│  │                                                                       │  │
│  │     🔐 This requires your approval:                                  │  │
│  │     [Approve Both] [Review Individually]                             │  │
│  │                                                                       │  │
│  │  🧑 Yu: Approved                                                     │  │
│  │                                                                       │  │
│  │  🤖 Orchestrator:                                                    │  │
│  │     Done! Here's what happened:                                      │  │
│  │                                                                       │  │
│  │     📤 identity-service: Received your decision, resuming work       │  │
│  │     📤 notifications: Started on "Notification auth update"          │  │
│  │                                                                       │  │
│  │     Project status:                                                  │  │
│  │     • Active workers: 4/4                                            │  │
│  │     • Blockers: 0 (was 1)                                            │  │
│  │     • Progress: 40% → estimated 45% by EOD                           │  │
│  │                                                                       │  │
│  ├───────────────────────────────────────────────────────────────────────┤  │
│  │  > Give me a summary for my standup                                  │  │
│  │                                                        [Send] [Voice]│  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 0.17 Standup Report Generation

The orchestrator can generate reports:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🎭 Orchestrator > Reports > Daily Standup                      [← Back]     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  📋 DAILY STANDUP REPORT                                                    │
│  ═══════════════════════                                                    │
│  Project: Voyager Login Refactor                                            │
│  Date: February 7, 2026                                                     │
│  Generated: 2:45 PM                                                         │
│                                                                             │
│  ┌─ YESTERDAY'S ACCOMPLISHMENTS ────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  ✅ Completed OAuth callback handler (voyager-web)                   │  │
│  │     PR #123 merged                                                   │  │
│  │                                                                       │  │
│  │  ✅ Completed token exchange logic (voyager-web)                     │  │
│  │     PR #124 merged                                                   │  │
│  │                                                                       │  │
│  │  ✅ Completed DB schema for JWT tokens (identity-service)            │  │
│  │     PR #450 merged                                                   │  │
│  │                                                                       │  │
│  │  📊 3 tasks completed, 3 PRs merged                                  │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ TODAY'S FOCUS ──────────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  🔄 Error handling for OAuth (voyager-web)                           │  │
│  │     In progress, ~80% complete                                       │  │
│  │                                                                       │  │
│  │  🔄 JWT token service (identity-service)                             │  │
│  │     PR #456 awaiting review                                          │  │
│  │                                                                       │  │
│  │  🔄 Payments migration (payments-api)                                │  │
│  │     PR #789 in progress                                              │  │
│  │                                                                       │  │
│  │  🆕 Notification auth update (notifications)                         │  │
│  │     Just started                                                     │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ BLOCKERS ───────────────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  ⚠️  Resolved: Database technology decision                          │  │
│  │     → Decided to use Redis + PostgreSQL backup                       │  │
│  │                                                                       │  │
│  │  🟡 PR reviews needed:                                               │  │
│  │     • PR #456 (4 hours waiting)                                      │  │
│  │     • PR #789 (1 hour waiting)                                       │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ PROJECT HEALTH ─────────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  Progress: 40%  │  On Track: ✅ Yes                                  │  │
│  │  Velocity: 3 tasks/day  │  Est. Completion: Feb 19 (2 days early)    │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│                    [Copy to Clipboard] [Export Markdown] [Send to Slack]   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Executive Summary

Claude Orchestrator is a local-first meta-agent that manages multiple concurrent Claude Code sessions across remote and local development environments from a single interface. It targets Staff+ Engineers who run parallel AI-assisted coding workflows across multiple repositories and need a unified view of status, decisions, and progress without constant context switching.

The core value proposition is shifting the engineer's role from **terminal babysitter** (manually switching between N Claude Code windows) to **strategic decision-maker** (reviewing aggregated status, unblocking workers, and steering project direction). The orchestrator provides a web dashboard and CLI for monitoring sessions, a decision queue for human-in-the-loop approvals, project/task management for tracking multi-repo initiatives, and a learning engine that improves guidance over time.

Success means an engineer can manage 10+ concurrent Claude Code sessions with < 10 second status queries, < 5 minute decision latency, and progressively less manual intervention as the system learns their preferences.

---

## 2. Problem Statement

### 2.1 Current Pain Points

| Pain Point | Description | Impact |
|------------|-------------|--------|
| **Context Switching** | Engineer must manually switch between N terminal windows/VSCode sessions | Mental overhead, lost context |
| **Information Fragmentation** | Each Claude Code session is a silo with no shared state | Duplicate work, inconsistent decisions |
| **Bottleneck Risk** | Engineer becomes the bottleneck when multiple sessions need input | Slowed development, idle AI agents |
| **No Central Status** | No way to get a unified view of all ongoing work | Difficulty tracking progress |
| **Repetitive Guidance** | Same feedback given repeatedly to different sessions | Wasted time, inconsistent guidance |

### 2.2 Current Workflow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ VSCode +    │     │ VSCode +    │     │ VSCode +    │
│ Claude Code │     │ Claude Code │     │ Claude Code │
│ (rdev-A)    │     │ (rdev-B)    │     │ (rdev-C)    │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   Engineer  │
                    │ (Bottleneck)│
                    └─────────────┘
```

### 2.3 Desired Workflow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Claude Code │     │ Claude Code │     │ Claude Code │
│ (rdev-A)    │     │ (rdev-B)    │     │ (rdev-C)    │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           │
                           ▼
                   ┌───────────────┐
                   │  Orchestrator │
                   │    Agent      │
                   └───────┬───────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   Engineer  │
                    │ (Strategic) │
                    └─────────────┘
```

---

## 3. Goals & Success Metrics

### 3.1 Primary Goals

| Goal | Description | Priority |
|------|-------------|----------|
| G1 | Reduce context switching overhead by 80% | P0 |
| G2 | Provide unified status view across all work streams | P0 |
| G3 | Enable human-in-the-loop at critical decision points | P0 |
| G4 | Learn from user feedback to improve guidance over time | P1 |
| G5 | Beautiful visualization of work progress | P2 |

### 3.2 Success Metrics

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| **Time to Status** | < 10 seconds to get status of all work | Timer from query to response |
| **Decision Latency** | < 5 minutes for critical decisions | Time from decision request to resolution |
| **Sessions Managed** | Support 10+ concurrent sessions | Load testing |
| **User Satisfaction** | NPS > 8 | User survey |
| **Learning Accuracy** | 90% accuracy on repeated decisions | Track decision patterns |

### 3.3 Non-Goals (v1)

- Automatic PR merging without human approval
- Integration with Jira/ticketing systems
- Mobile app interface
- Multi-user collaboration
- Automatic conflict resolution across repos

---

## 4. User Personas

### 4.1 Primary Persona: The Multi-Repo Engineer

**Name:** Alex Chen  
**Role:** Staff Software Engineer  
**Context:** Works on a large platform with multiple microservices

**Characteristics:**
- Manages 3-5 active development initiatives simultaneously
- Each initiative spans 2-4 repositories
- Creates 50-100 PRs per week with AI assistance
- Values efficiency and minimal context switching
- Comfortable with terminal-based workflows

**Goals:**
- Stay informed without micromanaging each session
- Make strategic decisions, not tactical ones
- Ensure code quality across all changes
- Maintain velocity across multiple work streams

**Frustrations:**
- "I lose track of what each Claude instance is doing"
- "I give the same feedback repeatedly"
- "Critical decisions wait because I'm focused elsewhere"

### 4.2 Secondary Persona: The Tech Lead

**Name:** Jordan Smith  
**Role:** Tech Lead  
**Context:** Oversees AI-assisted development across team

**Characteristics:**
- Needs visibility into AI-generated changes
- Reviews critical architectural decisions
- Sets patterns and guidelines for AI assistance

**Goals:**
- Ensure consistency across AI-generated code
- Catch architectural issues early
- Scale team's AI-assisted development

---

## 5. User Stories

### 5.1 Core User Stories

#### US-1: Unified Status Query
**As** an engineer managing multiple Claude Code sessions  
**I want** to ask a single question and get aggregated status from all sessions  
**So that** I can understand overall progress without checking each terminal

**Acceptance Criteria:**
- [ ] Single natural language query gets status from all sessions
- [ ] Response includes: active tasks, PRs created, blockers, pending decisions
- [ ] Response time < 10 seconds for up to 10 sessions
- [ ] Status is formatted in an easy-to-scan format

#### US-2: Session Management
**As** an engineer  
**I want** to add, remove, and list remote development sessions  
**So that** I can manage which work streams the orchestrator monitors

**Acceptance Criteria:**
- [ ] Add session with: name, SSH host, multiproduct/repo path
- [ ] Session automatically SSHs and starts Claude Code
- [ ] List all sessions with their current status
- [ ] Remove session cleanly (with option to leave Claude running)

#### US-3: Route Commands to Sessions
**As** an engineer  
**I want** to send instructions to specific Claude Code sessions through the orchestrator  
**So that** I don't need to switch to individual terminals

**Acceptance Criteria:**
- [ ] Natural language routing: "Tell the payments session to focus on the API endpoint"
- [ ] Direct routing: `/send payments-rdev prioritize the API endpoint`
- [ ] Confirmation before sending (configurable)
- [ ] Ability to broadcast to all sessions

#### US-4: Decision Queue
**As** an engineer  
**I want** critical decisions from all sessions surfaced to me in one place  
**So that** I can make decisions efficiently without sessions waiting

**Acceptance Criteria:**
- [ ] Sessions can report "need decision" via API
- [ ] Orchestrator maintains a decision queue
- [ ] Queue shows: session, question, context, urgency
- [ ] Decisions are routed back to the originating session

#### US-5: Terminal Output Review
**As** an engineer  
**I want** to review recent output from any session  
**So that** I can understand what's happening in detail when needed

**Acceptance Criteria:**
- [ ] Capture last N lines of terminal output per session
- [ ] Query specific session output: `/output payments-rdev`
- [ ] Search across all session outputs
- [ ] Attach to live session for manual intervention

#### US-6: Learning from Feedback
**As** an engineer  
**I want** the orchestrator to learn from my decisions and feedback  
**So that** it can make better recommendations over time

**Acceptance Criteria:**
- [ ] Track all decisions made (approve/reject/modify)
- [ ] Store context around each decision
- [ ] Use past decisions to suggest responses to similar situations
- [ ] Allow user to review and correct learned patterns

### 5.2 Extended User Stories (P1)

#### US-7: PR Dashboard
**As** an engineer  
**I want** a visual dashboard showing all PRs across sessions  
**So that** I can track review status and merge readiness

#### US-8: Checkpoint Configuration
**As** an engineer  
**I want** to configure what decisions require my approval  
**So that** I can tune the human-in-the-loop level

#### US-9: Session Templates
**As** an engineer  
**I want** to save and reuse session configurations  
**So that** I can quickly spin up common development setups

#### US-10: Conversation Export
**As** an engineer  
**I want** to export orchestrator conversations and decisions  
**So that** I can review and share learnings

### 5.3 Project Management User Stories (P0)

#### US-11: Create Project
**As** an engineer starting a new initiative  
**I want** to create a project that groups related tasks across multiple workers  
**So that** I can track overall progress toward a goal

**Acceptance Criteria:**
- [ ] Create project with: name, description, target date
- [ ] Auto-parse description to suggest initial tasks
- [ ] Assign workers (sessions) to the project
- [ ] Set project as active/paused/completed

#### US-12: View Project Progress
**As** an engineer managing a project  
**I want** to see overall progress across all tasks and workers  
**So that** I know if I'm on track for the deadline

**Acceptance Criteria:**
- [ ] Progress bar showing % complete
- [ ] Breakdown by service/repo (worker)
- [ ] Timeline view showing current position vs. deadline
- [ ] Velocity calculation and completion estimate

#### US-13: Task Board (Kanban)
**As** an engineer  
**I want** to see all tasks in a kanban-style board  
**So that** I can visualize work flow and bottlenecks

**Acceptance Criteria:**
- [ ] Columns: TODO, IN PROGRESS, DONE, BLOCKED
- [ ] Drag-and-drop to reassign or reorder (future)
- [ ] Click task to see details, PRs, worker
- [ ] Quick-assign unassigned tasks to idle workers

#### US-14: Worker Assignment View
**As** an engineer  
**I want** to see which worker is working on which task  
**So that** I understand resource allocation

**Acceptance Criteria:**
- [ ] List all workers assigned to project
- [ ] Show current task for each worker
- [ ] Show completed tasks and PRs per worker
- [ ] Identify idle workers ready for assignment
- [ ] Link to terminal view for each worker

#### US-15: Blockers Dashboard
**As** an engineer  
**I want** a dedicated view of all blockers  
**So that** I can quickly unblock progress

**Acceptance Criteria:**
- [ ] List all blocking decisions with context
- [ ] Show how long each blocker has been waiting
- [ ] Show which tasks are blocked downstream
- [ ] One-click to respond to decisions
- [ ] Highlight urgent blockers

#### US-16: Project Activity Timeline
**As** an engineer  
**I want** to see a chronological log of project activity  
**So that** I can understand what happened and when

**Acceptance Criteria:**
- [ ] Events: task started, task completed, PR created, PR merged, decision made
- [ ] Filter by worker, event type, date range
- [ ] Show who/what triggered each event
- [ ] Expandable details for each event

#### US-17: Generate Standup Report
**As** an engineer  
**I want** to generate a standup report from project data  
**So that** I can quickly prepare for team meetings

**Acceptance Criteria:**
- [ ] Auto-generate: yesterday's accomplishments, today's focus, blockers
- [ ] Include PR counts and status
- [ ] Show project health (on track / at risk)
- [ ] Export as markdown or copy to clipboard

#### US-18: Cross-Project Dashboard
**As** an engineer managing multiple projects  
**I want** to see all projects in one view  
**So that** I can prioritize across initiatives

**Acceptance Criteria:**
- [ ] List all projects with progress summary
- [ ] Quick stats: active workers, total tasks, open PRs
- [ ] Filter by status (active, paused, completed)
- [ ] Click to drill into project detail

---

## 6. Functional Requirements

### 6.1 Authentication Module

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-AUTH-1 | Reuse Claude Code's OAuth token from macOS Keychain | P0 |
| FR-AUTH-2 | Detect when token is invalid and prompt for re-auth | P0 |
| FR-AUTH-3 | Support triggering Claude Code's OAuth flow when no token exists | P1 |
| FR-AUTH-4 | Securely store any additional credentials | P1 |

### 6.2 Terminal Management Module

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-TERM-1 | Create and manage tmux sessions for remote connections | P0 |
| FR-TERM-2 | SSH into remote hosts and start Claude Code | P0 |
| FR-TERM-3 | Send keystrokes to specific tmux windows | P0 |
| FR-TERM-4 | Capture terminal output from tmux panes | P0 |
| FR-TERM-5 | Support attaching to tmux for manual intervention | P0 |
| FR-TERM-6 | Handle SSH connection failures gracefully | P1 |
| FR-TERM-7 | Support SSH key and password authentication | P1 |
| FR-TERM-8 | Maintain persistent sessions across orchestrator restarts | P2 |

### 6.3 State Management Module

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-STATE-1 | Store session metadata in SQLite | P0 |
| FR-STATE-2 | Track PRs created by each session | P0 |
| FR-STATE-3 | Maintain decision queue with pending items | P0 |
| FR-STATE-4 | Store decision history for learning | P1 |
| FR-STATE-5 | Track task/initiative hierarchy | P1 |
| FR-STATE-6 | Support querying state by various dimensions | P1 |

### 6.4 LLM Integration Module

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-LLM-1 | Make API calls to Anthropic using reused token | P0 |
| FR-LLM-2 | Build context from current state for each query | P0 |
| FR-LLM-3 | Parse structured actions from LLM responses | P0 |
| FR-LLM-4 | Support conversation history within session | P1 |
| FR-LLM-5 | Implement RAG with vector store for learning | P2 |
| FR-LLM-6 | Tiered intelligence: use pattern matching for routine detection, LLM only for ambiguous situations | P0 |
| FR-LLM-7 | Define invocation triggers: invoke LLM brain on state changes and user queries, not on every poll | P0 |
| FR-LLM-8 | Context assembly strategy: build focused context from session state, recent activity, and relevant history per query | P0 |
| FR-LLM-9 | Cost tracking per LLM invocation with configurable budget ceiling | P1 |

### 6.5 Chat Interface Module

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-CHAT-1 | Provide CLI-based chat interface | P0 |
| FR-CHAT-2 | Support slash commands for direct actions | P0 |
| FR-CHAT-3 | Display formatted responses with status tables | P0 |
| FR-CHAT-4 | Confirm before executing actions on sessions | P0 |
| FR-CHAT-5 | Support command history and completion | P1 |

### 6.6 Reporting API Module (Optional)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-API-1 | REST API for workers to report status | P1 |
| FR-API-2 | Endpoint for workers to request decisions | P1 |
| FR-API-3 | Endpoint for workers to check for guidance | P1 |
| FR-API-4 | Webhook support for PR events | P2 |

### 6.7 Visualization Module (Future)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-VIS-1 | Web dashboard for session overview | P2 |
| FR-VIS-2 | PR status board (kanban-style) | P2 |
| FR-VIS-3 | Activity timeline across all sessions | P2 |
| FR-VIS-4 | Decision queue visualization | P2 |

### 6.8 Project Management Module

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-PROJ-1 | Create projects with name, description, target date | P0 |
| FR-PROJ-2 | Assign workers (sessions) to projects | P0 |
| FR-PROJ-3 | Create and manage tasks within projects | P0 |
| FR-PROJ-4 | Assign tasks to workers | P0 |
| FR-PROJ-5 | Track task status: TODO, IN_PROGRESS, DONE, BLOCKED | P0 |
| FR-PROJ-6 | Link PRs to tasks automatically | P1 |
| FR-PROJ-7 | Calculate project progress from task completion | P0 |
| FR-PROJ-8 | Track blockers with context and waiting time | P0 |
| FR-PROJ-9 | Generate activity timeline for projects | P1 |
| FR-PROJ-10 | Generate standup reports | P1 |
| FR-PROJ-11 | Support multiple concurrent projects | P1 |
| FR-PROJ-12 | Estimate completion date from velocity | P2 |

### 6.9 Dashboard Views Module

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-DASH-1 | Project list view with progress summaries | P0 |
| FR-DASH-2 | Project detail view with all tasks | P0 |
| FR-DASH-3 | Task board (kanban) view | P0 |
| FR-DASH-4 | Worker assignment view | P0 |
| FR-DASH-5 | Blockers/decisions panel | P0 |
| FR-DASH-6 | Activity timeline view | P1 |
| FR-DASH-7 | Report generation view | P1 |
| FR-DASH-8 | Quick stats cards | P1 |

### 6.10 Communication Robustness Module

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-COMM-1 | Expose orchestrator as MCP server for structured Claude Code communication | P0 |
| FR-COMM-2 | Configure Claude Code hooks for automatic event reporting (commits, PRs, errors) | P0 |
| FR-COMM-3 | Implement heartbeat monitoring — detect stale/unresponsive sessions within 120s | P0 |
| FR-COMM-4 | State reconciliation every 5 minutes — cross-check tmux, git, and PR state against known state | P0 |
| FR-COMM-5 | Backfill missed events when reconciliation finds unreported activity | P1 |
| FR-COMM-6 | Support event-driven monitoring via `tmux pipe-pane` + file watchers as alternative to pure polling | P1 |
| FR-COMM-7 | Adaptive polling intervals: higher frequency for active sessions, lower for idle | P1 |
| FR-COMM-8 | Track communication reliability metrics per channel (MCP, hooks, passive, curl) | P2 |

### 6.11 Session Recovery & Context Preservation Module

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-RECV-1 | Detect when Claude Code has compacted context (`/compact`) or restarted | P0 |
| FR-RECV-2 | Verify /orchestrator skill exists (reinstall if missing) and re-register MCP server on session recovery | P0 |
| FR-RECV-3 | Send "re-brief" message to recovered sessions with current task, progress, and relevant context | P0 |
| FR-RECV-4 | Maintain a "session context snapshot" in the DB: last known task, progress, key decisions, file paths | P0 |
| FR-RECV-5 | Auto-recover crashed tmux windows: detect, re-create, SSH, restart Claude Code, re-brief | P1 |
| FR-RECV-6 | Log all recovery events in the activity timeline | P1 |
| FR-RECV-7 | Support manual "re-brief" command: `/rebrief <session>` to push current context to a session | P1 |

### 6.12 Worker Capability & Task Scheduling Module

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-SCHED-1 | Worker profiles: each session has capabilities (repo, language, tools, environment type) | P0 |
| FR-SCHED-2 | Match tasks to workers based on capability requirements (repo, skills needed) | P0 |
| FR-SCHED-3 | Respect task dependencies: hold dependent tasks until prerequisites complete | P0 |
| FR-SCHED-4 | Cascade blocker status to downstream dependent tasks | P0 |
| FR-SCHED-5 | Auto-assign next task to idle workers from the project's TODO queue | P1 |
| FR-SCHED-6 | Detect implicit dependencies: warn if two tasks modify overlapping file paths | P1 |
| FR-SCHED-7 | Priority-based scheduling: higher priority tasks assigned before lower ones | P1 |
| FR-SCHED-8 | Worker load balancing: avoid assigning too many concurrent tasks to one worker | P2 |

### 6.13 LLM Brain Design Module

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-BRAIN-1 | Define prompt templates for: status queries, command routing, decision assistance, task planning | P0 |
| FR-BRAIN-2 | Context assembly: for each LLM call, build a focused context including session states, recent activity, pending decisions, and relevant history | P0 |
| FR-BRAIN-3 | Action parsing: LLM responses produce structured actions (send_message, create_task, assign_task, etc.) with a defined schema | P0 |
| FR-BRAIN-4 | Tiered detection: terminal output is first processed by regex/pattern matchers; LLM is invoked only when patterns are ambiguous or user queries require reasoning | P0 |
| FR-BRAIN-5 | Autonomous vs Advisory mode: user-configurable autonomy level controlling which actions the brain can take without approval | P1 |
| FR-BRAIN-6 | Natural language project planning: decompose a user's project description into tasks with dependencies using LLM | P1 |
| FR-BRAIN-7 | Budget controls: max LLM tokens per hour/day, alert when approaching budget | P1 |

### 6.14 Cross-Session Communication Module

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-XCOMM-1 | Mediated cross-session messaging: orchestrator can relay information from one session to another | P1 |
| FR-XCOMM-2 | Dependency notification: when Session A completes an API/interface change, notify Session B that depends on it | P1 |
| FR-XCOMM-3 | Conflict detection: alert when two sessions modify overlapping code paths or create conflicting changes | P1 |
| FR-XCOMM-4 | Shared context: sessions working on the same project can access shared decisions and architectural context | P2 |

### 6.15 Cost & Resource Management Module

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-COST-1 | Track estimated API token cost per session (Claude Code usage) | P1 |
| FR-COST-2 | Track orchestrator's own LLM API costs separately | P1 |
| FR-COST-3 | Configurable budget ceiling: max cost per day/week, alert at thresholds | P1 |
| FR-COST-4 | Cost dashboard: visualize spend by session, project, and time period | P2 |
| FR-COST-5 | Rate limiting: cap orchestrator LLM calls per minute to control cost | P1 |

### 6.16 PR Dependency Management Module

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-PRDEP-1 | Model PR dependencies: PR B should only merge after PR A | P1 |
| FR-PRDEP-2 | Visualize PR dependency graph in the dashboard | P2 |
| FR-PRDEP-3 | Alert when a PR dependency is at risk (e.g., upstream PR has failing CI) | P2 |
| FR-PRDEP-4 | Suggest merge order based on dependency analysis | P2 |

### 6.17 Replay & Audit Module

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-REPLAY-1 | Log all orchestrator actions, decisions, and state transitions immutably | P1 |
| FR-REPLAY-2 | Replay capability: step through a project's execution history chronologically | P2 |
| FR-REPLAY-3 | Export project execution history as markdown or JSON for sharing/review | P1 |
| FR-REPLAY-4 | Annotate replay events with outcomes (was this decision correct in hindsight?) | P2 |

### 6.18 Context Management Module (Zero Hard-Coded Context)

**Design Principle:** The orchestrator must contain **zero hard-coded domain knowledge**. It does not know about specific repos, team conventions, tech stack preferences, or workflow patterns until told. All such context is stored in the database and loaded dynamically. The orchestrator is a general-purpose engine that becomes domain-aware through usage.

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-CTX-1 | All project names, repo paths, session configs, task descriptions, and workflow rules stored in and loaded from DB — never hard-coded in source | P0 |
| FR-CTX-2 | All LLM prompt templates stored in DB or config files, not embedded in code — enabling modification without code changes | P0 |
| FR-CTX-3 | Worker capability profiles (repo, language, tools) loaded from `worker_capabilities` table, not inferred from code | P0 |
| FR-CTX-4 | Decision patterns and learned preferences loaded from `learned_patterns` and `decision_history` tables | P0 |
| FR-CTX-5 | Skill template content (for /orchestrator slash command) stored in DB or config, with variable substitution at install time | P0 |
| FR-CTX-6 | Approval policies (which actions need approval at which risk level) stored in DB config, not hard-coded | P1 |
| FR-CTX-7 | Smart context selection: when total DB context exceeds LLM context window, apply relevance-weighted scoring to select and compact the most critical items (see Section 8.5.5) | P0 |
| FR-CTX-8 | Context selection scoring weights (recency, relevance, urgency, status) are configurable in DB | P1 |
| FR-CTX-9 | Support "context compaction" — summarize low-priority items into compact summaries rather than dropping them entirely | P0 |
| FR-CTX-10 | Empty-state behavior: orchestrator starts cleanly with an empty DB and guides the user through initial setup (add first session, create first project) | P1 |
| FR-CTX-11 | Context selection telemetry: log what was included vs excluded in each LLM call for debugging and tuning | P2 |

---

## 7. Non-Functional Requirements

### 7.1 Performance

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-PERF-1 | Status query response time | < 10 seconds for 10 sessions |
| NFR-PERF-2 | Terminal output capture | < 2 seconds per session |
| NFR-PERF-3 | LLM response time | < 30 seconds for complex queries |
| NFR-PERF-4 | Memory usage | < 500MB for 10 sessions |

### 7.2 Reliability

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-REL-1 | Orchestrator uptime | 99% during work hours |
| NFR-REL-2 | Session reconnection on failure | Automatic within 30 seconds |
| NFR-REL-3 | State persistence | Survive orchestrator restart |
| NFR-REL-4 | Graceful degradation | Continue if one session fails |

### 7.3 Usability

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-USE-1 | Time to add first session | < 2 minutes |
| NFR-USE-2 | Learning curve | Productive within 10 minutes |
| NFR-USE-3 | Documentation completeness | All features documented |
| NFR-USE-4 | Error message clarity | Actionable error messages |

### 7.4 Security

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-SEC-1 | Token storage | macOS Keychain only |
| NFR-SEC-2 | SSH key handling | Use system SSH agent |
| NFR-SEC-3 | No credential logging | Never log tokens or passwords |
| NFR-SEC-4 | API communication | HTTPS only |

### 7.5 Maintainability

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-MAIN-1 | Code test coverage | > 70% |
| NFR-MAIN-2 | Module coupling | Loose coupling, clear interfaces |
| NFR-MAIN-3 | Configuration | External config file |
| NFR-MAIN-4 | Logging | Structured logging with levels |

---

### 7.6 Schema Migration Strategy

The SQLite database schema will evolve as features are added. To avoid data loss and enable smooth upgrades:

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-MIG-1 | Use versioned migration files (Alembic or custom migration runner) | From day one |
| NFR-MIG-2 | Automatic migration on startup: detect schema version, apply pending migrations | Every startup |
| NFR-MIG-3 | Backup database before applying migrations | Automatic |
| NFR-MIG-4 | Support rollback of failed migrations | Best-effort |

### 7.7 Integration Strategy with Existing System

The `my_assistant` project already has a FastAPI backend with LangGraph orchestration and a React frontend. The orchestrator is designed as a **separate, standalone system** for the following reasons:

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| **Backend** | Separate Python process (FastAPI) | The orchestrator manages tmux sessions and SSH — fundamentally different from the LangGraph agent orchestration. Coupling them would complicate both. |
| **Database** | Separate SQLite file | The orchestrator's data model (sessions, terminals, tmux state) is disjoint from the main assistant's data model (runs, agents, tools). Separate DBs simplify both. |
| **Frontend** | Separate web dashboard (can share design system) | The orchestrator dashboard is operational tooling (monitoring, decisions). The main assistant frontend is task-oriented (chat, workflows). Different UX paradigms. |
| **Future bridge** | REST API interop | The main assistant could invoke the orchestrator's API to spawn sessions. The orchestrator could delegate complex reasoning to the main assistant's agent system. This is a future concern, not a v1 requirement. |

### 7.8 Testing Strategy

| Level | Scope | Approach |
|-------|-------|----------|
| **Unit Tests** | State management, decision queue, task scheduling, worker matching, pattern detection | Standard pytest with mocked dependencies. Target > 80% coverage on core logic. |
| **Integration Tests** | tmux session management, SSH wrapper, MCP server, API endpoints | Use mock tmux sessions (create real tmux windows with scripted "Claude Code" output). Test API endpoints with httpx test client. |
| **E2E Tests (Playwright)** | Full dashboard flows: session cards, decision queue, chat, WebSocket updates | Playwright tests with **screenshot capture**, **HTML dumps**, and **console log capture** per test step. Claude Code reads these artifacts to iteratively develop and fix the UI. |
| **E2E Tests (Flow)** | Full flow: create session → assign task → detect progress → surface decision → send response | Scripted scenario using real tmux + mock LLM responses. Runs in CI with a tmux fixture. |
| **Chaos Tests** | SSH drop mid-task, tmux window killed, DB corruption, orchestrator crash/restart | Inject failures during E2E tests. Verify recovery flows work correctly. |
| **Performance Tests** | 10+ concurrent sessions, polling overhead, LLM call latency | Load test with simulated sessions. Verify memory < 500MB and response time < 10s. |

**Playwright development workflow:** During dashboard development, every Playwright test generates artifacts that Claude Code can read directly: screenshots (`.png` via the Read tool), HTML dumps (`page.content()` to file), and browser console logs (`page.on('console')` to file). This enables iterative UI development where Claude Code "sees" its own output and fixes issues without needing a live browser.

### 7.9 Skill Installation for Remote Sessions

Instead of injecting a CLAUDE.md file (which would conflict with existing repo files on rdevs), the orchestrator installs a **custom slash command (skill)** in each Claude Code session. The orchestrator types into Claude Code like a real user via `tmux send-keys`, instructing it to create the skill. This approach:

- **Doesn't modify any repo files** — the skill lives in `.claude/commands/`, not in the MP workspace
- **Persists across restarts** — once created, the skill survives `/compact` and session restarts
- **Is idempotent** — the orchestrator checks if the skill already exists before installing
- **Is updatable** — when new orchestrator features require skill updates, the orchestrator can type instructions to update it

#### 7.9.1 Skill Installation Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  SKILL INSTALLATION (triggered on session creation or update)              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  STEP 1: Check if skill already exists                                     │
│  ─────────────────────────────────────                                      │
│  The orchestrator checks for the skill file on the remote session:         │
│                                                                             │
│    tmux send-keys -t orchestrator:$SESSION \                               │
│      "ls .claude/commands/orchestrator.md 2>/dev/null && echo EXISTS \      │
│       || echo MISSING" Enter                                               │
│                                                                             │
│  Parse terminal output to determine if skill exists.                       │
│                                                                             │
│  STEP 2: If MISSING, install the skill                                     │
│  ─────────────────────────────────────                                      │
│  Type into Claude Code like a real user:                                   │
│                                                                             │
│    tmux send-keys -t orchestrator:$SESSION \                               │
│      "Please create a custom slash command at                               │
│       .claude/commands/orchestrator.md with the following content.          │
│       This is an orchestrator integration skill that I need you to         │
│       use for reporting progress. [SKILL CONTENT BELOW]" Enter             │
│                                                                             │
│  STEP 3: If EXISTS and version is outdated, update                         │
│  ────────────────────────────────────────────────                           │
│  Check the version marker in the skill file. If outdated:                  │
│                                                                             │
│    tmux send-keys -t orchestrator:$SESSION \                               │
│      "Please update .claude/commands/orchestrator.md with the              │
│       following updated content. [UPDATED SKILL CONTENT]" Enter            │
│                                                                             │
│  STEP 4: Verify installation                                               │
│  ───────────────────────────                                                │
│  After Claude Code confirms creation, verify:                              │
│                                                                             │
│    tmux send-keys -t orchestrator:$SESSION \                               │
│      "ls .claude/commands/orchestrator.md" Enter                           │
│                                                                             │
│  Parse output to confirm file exists.                                      │
│                                                                             │
│  TRIGGERS:                                                                  │
│  • Session creation (new rdev added to orchestrator)                       │
│  • Session recovery (skill found missing after crash/re-clone)             │
│  • Orchestrator upgrade (skill version < current version)                  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 7.9.2 Skill Template Content

The skill is stored in the DB (`skill_templates` table) and rendered with session-specific variables at install time. The content instructs Claude Code how to communicate with the orchestrator:

```markdown
# Orchestrator Integration Skill
<!-- orchestrator-skill-version: ${SKILL_VERSION} -->

You are connected to an orchestrator system managing multiple Claude Code
sessions. Use this skill to report your progress and request decisions.

## Environment

- Session Name: ${SESSION_NAME}
- Orchestrator URL: ${ORCHESTRATOR_URL}

## Report Progress

After completing significant milestones, report them:

    curl -sX POST ${ORCHESTRATOR_URL}/api/report \
      -H "Content-Type: application/json" \
      -d '{"session":"${SESSION_NAME}","event":"task_progress",
           "data":{"task":"DESCRIPTION","progress":PERCENT,
                   "subtasks":[{"name":"...","done":true/false}]}}'

## Report PR Creation

When you create a pull request:

    curl -sX POST ${ORCHESTRATOR_URL}/api/report \
      -H "Content-Type: application/json" \
      -d '{"session":"${SESSION_NAME}","event":"pr_created",
           "data":{"url":"PR_URL","title":"PR_TITLE"}}'

## Request Decision

For architectural decisions or when you need user input:

    curl -sX POST ${ORCHESTRATOR_URL}/api/decision \
      -H "Content-Type: application/json" \
      -d '{"session":"${SESSION_NAME}",
           "question":"YOUR QUESTION","options":["A","B"],
           "context":"CONTEXT","urgency":"normal"}'

Then wait — the user will respond through the orchestrator.

## Check for Guidance

Before starting major work:

    curl -s "${ORCHESTRATOR_URL}/api/guidance?session=${SESSION_NAME}"

## Report Errors

When blocked by errors:

    curl -sX POST ${ORCHESTRATOR_URL}/api/report \
      -H "Content-Type: application/json" \
      -d '{"session":"${SESSION_NAME}","event":"error",
           "data":{"type":"ERROR_TYPE","message":"DESCRIPTION"}}'

## Best Practices

1. Report progress after completing each significant subtask
2. Request decisions for architectural choices
3. Check for guidance at the start of each major task
4. Report PRs immediately after creation
5. Report errors when blocked

The orchestrator may send you messages directly through the terminal.
Always acknowledge received instructions.
```

#### 7.9.3 Why Skills Over CLAUDE.md

| Aspect | CLAUDE.md Injection | Skill Installation |
|--------|--------------------|--------------------|
| **Repo conflict** | Overwrites or conflicts with existing CLAUDE.md in the MP | No conflict — `.claude/commands/` is user-space, not repo content |
| **Persistence** | Must be re-injected on every restart or re-clone | Persists in `.claude/commands/`; survives `/compact` and restarts |
| **Installation** | Requires file system write access before Claude Code starts | Orchestrator types into Claude Code like a user — works even on locked-down environments |
| **Updates** | Must overwrite file and hope Claude Code re-reads it | Orchestrator types update instruction; Claude Code handles the edit |
| **Visibility** | Hidden file that Claude Code may deprioritize | Explicit slash command that the user or orchestrator can invoke by name |
| **Idempotence** | Must check for existing file, handle merge conflicts | Check if skill exists + version marker; skip if current |

---

## 8. System Architecture

### 8.0 Core Design Principles

Before diving into components, these principles govern all implementation decisions:

| Principle | Description |
|-----------|-------------|
| **Zero Hard-Coded Context** | The orchestrator must **never** hard-code domain-specific knowledge, project context, repo names, team conventions, or workflow patterns. All such context lives in the database and is loaded dynamically at runtime. The orchestrator is a general-purpose engine — it learns about *your* projects, *your* repos, and *your* preferences entirely through DB-stored state. |
| **DB-Driven Everything** | Session configurations, project definitions, task descriptions, worker capabilities, decision history, learned patterns, skill templates, approval policies — all stored in and read from the database. If the DB is empty, the orchestrator starts with zero assumptions and builds context through usage. |
| **Smart Context Selection** | When the total context from DB (sessions, tasks, decisions, history, learned patterns) exceeds the LLM context window, the orchestrator applies a **smart context selection algorithm** to pick and compact the most critical working context. This is not simple truncation — it is relevance-weighted selection based on recency, active status, relationship to the current query, and urgency. See Section 8.5.5 for details. |
| **Local-First** | All data and processing stays on the user's machine. No cloud dependencies beyond the LLM API. |
| **Fail Gracefully** | Every component assumes other components may fail. Communication channels have fallbacks. State is persisted on every mutation. |
| **Explicit Actions** | The orchestrator never takes action on behalf of the user without explicit approval (unless the user has configured Autonomous Mode for specific action types). |

### 8.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           USER (Staff Engineer)                             │
│                                                                             │
│                    ┌──────────────────────────────────┐                    │
│                    │     🌐 Web Dashboard (localhost)  │                    │
│                    │                                   │                    │
│                    │  • Session grid visualization    │                    │
│                    │  • Decision approval buttons     │                    │
│                    │  • Chat interface                │                    │
│                    │  • Terminal takeover             │                    │
│                    └────────────────┬─────────────────┘                    │
│                                     │                                      │
└─────────────────────────────────────│──────────────────────────────────────┘
                                      │ HTTP/WebSocket
┌─────────────────────────────────────│──────────────────────────────────────┐
│                                     │                                      │
│  ┌──────────────────────────────────▼───────────────────────────────────┐  │
│  │                      ORCHESTRATOR (Python)                            │  │
│  ├───────────────────────────────────────────────────────────────────────┤  │
│  │                                                                       │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │  │
│  │  │   Web UI    │  │    LLM      │  │   State     │  │  Terminal   │  │  │
│  │  │   Server    │  │   Brain     │  │   Store     │  │   Manager   │  │  │
│  │  │             │  │             │  │             │  │             │  │  │
│  │  │ - FastAPI/  │  │ - Anthropic │  │ - SQLite    │  │ - tmux      │  │  │
│  │  │   FastAPI   │  │   API       │  │ - Sessions  │  │   control   │  │  │
│  │  │ - WebSocket │  │ - Decision  │  │ - PRs       │  │ - SSH       │  │  │
│  │  │ - REST API  │  │   logic     │  │ - History   │  │   wrapper   │  │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘  │  │
│  │                                                                       │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │  │
│  │  │   Passive   │  │   Active    │  │   Action    │  │   Vector    │  │  │
│  │  │   Monitor   │  │   Listener  │  │   Executor  │  │   Store     │  │  │
│  │  │             │  │             │  │             │  │             │  │  │
│  │  │ - Poll      │  │ - /api/     │  │ - send-keys │  │ - ChromaDB  │  │  │
│  │  │   output    │  │   report    │  │ - Commands  │  │ - Learning  │  │  │
│  │  │ - Detect    │  │ - /api/     │  │ - Approval  │  │ - RAG       │  │  │
│  │  │   states    │  │   decision  │  │   queue     │  │ (Phase 3)   │  │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘  │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ORCHESTRATOR                                                               │
│  MACHINE                                                                    │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                         tmux session: orchestrator                    │  │
│  ├───────────────────────────────────────────────────────────────────────┤  │
│  │                                                                       │  │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐     │  │
│  │  │Window 0    │ │Window 1    │ │Window 2    │ │Window 3    │     │  │
│  │  │voyager-web │ │payments-api│ │identity-svc│ │notifs      │     │  │
│  │  │            │ │            │ │            │ │            │     │  │
│  │  │ssh rdev-1  │ │ssh rdev-2  │ │ssh rdev-3  │ │ssh rdev-4  │     │  │
│  │  │ └─claude   │ │ └─claude   │ │ └─claude   │ │ └─claude   │     │  │
│  │  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘     │  │
│  │                                                                       │  │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐     │  │
│  │  │Window 4    │ │Window 5    │ │Window 6    │ │Window 7    │     │  │
│  │  │analytics   │ │docs        │ │scripts     │ │scratch     │     │  │
│  │  │            │ │            │ │            │ │            │     │  │
│  │  │ssh rdev-5  │ │local       │ │local       │ │local       │     │  │
│  │  │ └─claude   │ │ └─claude   │ │ └─claude   │ │ └─claude   │     │  │
│  │  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘     │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ SSH / curl API
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            REMOTE RDEVS                                     │
│                                                                             │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐       │
│  │ rdev-1       │ │ rdev-2       │ │ rdev-3       │ │ rdev-4       │ ...   │
│  │ voyager-web  │ │ payments-api │ │ identity-svc │ │ notifications│       │
│  │              │ │              │ │              │ │              │       │
│  │ Claude Code  │ │ Claude Code  │ │ Claude Code  │ │ Claude Code  │       │
│  │ running...   │ │ running...   │ │ running...   │ │ running...   │       │
│  │              │ │              │ │              │ │              │       │
│  │ curl →       │ │ curl →       │ │ curl →       │ │ curl →       │       │
│  │ orchestrator │ │ orchestrator │ │ orchestrator │ │ orchestrator │       │
│  │ /api/report  │ │ /api/report  │ │ /api/report  │ │ /api/report  │       │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 8.2 Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Orchestrator                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────────────┐     ┌──────────────────────┐                     │
│  │   web/               │     │   api/               │                     │
│  │   ├── server.py      │     │   ├── routes.py      │                     │
│  │   ├── templates/     │     │   ├── websocket.py   │                     │
│  │   │   └── index.html │     │   └── handlers.py    │                     │
│  │   └── static/        │     │   (report, decision) │                     │
│  │       ├── app.js     │     └──────────────────────┘                     │
│  │       └── styles.css │                                                  │
│  └──────────────────────┘                                                  │
│                                                                             │
│  ┌──────────────────────┐     ┌──────────────────────┐                     │
│  │   auth/              │     │   terminal/          │                     │
│  │   ├── keychain.py    │     │   ├── manager.py     │                     │
│  │   └── token.py       │     │   ├── session.py     │                     │
│  └──────────────────────┘     │   ├── monitor.py     │                     │
│                               │   └── ssh.py         │                     │
│  ┌──────────────────────┐     └──────────────────────┘                     │
│  │   llm/               │                                                  │
│  │   ├── client.py      │     ┌──────────────────────┐                     │
│  │   ├── brain.py       │     │   state/             │                     │
│  │   ├── actions.py     │     │   ├── db.py          │                     │
│  │   └── prompts.py     │     │   ├── models.py      │                     │
│  └──────────────────────┘     │   ├── decisions.py   │                     │
│                               │   └── sessions.py    │                     │
│  ┌──────────────────────┐     └──────────────────────┘                     │
│  │   knowledge/         │                                                  │
│  │   ├── vectors.py     │     ┌──────────────────────┐                     │
│  │   └── learning.py    │     │   core/              │                     │
│  │   (Phase 3)          │     │   ├── orchestrator.py│                     │
│  └──────────────────────┘     │   └── lifecycle.py   │                     │
│                               └──────────────────────┘                     │
│                                                                             │
│  ┌──────────────────────┐     ┌──────────────────────┐                     │
│  │   comm/              │     │   scheduler/         │                     │
│  │   ├── mcp_server.py  │     │   ├── matcher.py     │                     │
│  │   ├── hooks.py       │     │   ├── scheduler.py   │                     │
│  │   ├── heartbeat.py   │     │   ├── dependencies.py│                     │
│  │   └── reconciler.py  │     │   └── conflicts.py   │                     │
│  └──────────────────────┘     └──────────────────────┘                     │
│                                                                             │
│  ┌──────────────────────┐     ┌──────────────────────┐                     │
│  │   recovery/          │     │   cost/              │                     │
│  │   ├── detector.py    │     │   ├── tracker.py     │                     │
│  │   ├── snapshot.py    │     │   ├── budget.py      │                     │
│  │   └── rebrief.py     │     │   └── reports.py     │                     │
│  └──────────────────────┘     └──────────────────────┘                     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 8.3 API Endpoints

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  ORCHESTRATOR REST API                                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─ Dashboard & UI ─────────────────────────────────────────────────────┐  │
│  │  GET  /                    → Dashboard HTML                          │  │
│  │  GET  /static/*            → Static assets (JS, CSS)                 │  │
│  │  WS   /ws                  → WebSocket for real-time updates         │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ Session Management ─────────────────────────────────────────────────┐  │
│  │  GET  /api/sessions        → List all sessions                       │  │
│  │  POST /api/sessions        → Create new session                      │  │
│  │  GET  /api/sessions/:id    → Get session details                     │  │
│  │  POST /api/sessions/:id/send   → Send message to session             │  │
│  │  POST /api/sessions/:id/takeover → Enable takeover mode              │  │
│  │  POST /api/sessions/:id/release  → Release takeover                  │  │
│  │  DEL  /api/sessions/:id    → Remove session (don't kill terminal)    │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ Active Reporting (called by Claude Code) ───────────────────────────┐  │
│  │  POST /api/report          → Report event (progress, PR, error)      │  │
│  │  POST /api/decision        → Request decision from user              │  │
│  │  GET  /api/guidance        → Check for pending instructions          │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ Decision Management ────────────────────────────────────────────────┐  │
│  │  GET  /api/decisions       → List pending decisions                  │  │
│  │  POST /api/decisions/:id/respond → Respond to decision               │  │
│  │  POST /api/decisions/:id/dismiss → Dismiss decision                  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ Chat Interface ─────────────────────────────────────────────────────┐  │
│  │  POST /api/chat            → Send message to orchestrator            │  │
│  │  GET  /api/chat/history    → Get chat history                        │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ Hooks & MCP (automatic events from Claude Code) ──────────────────┐  │
│  │  POST /api/hook            → Receive hook-triggered events           │  │
│  │  POST /api/mcp/register    → Register MCP server connection          │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ Project Management ───────────────────────────────────────────────┐  │
│  │  GET  /api/projects        → List all projects                      │  │
│  │  POST /api/projects        → Create new project                     │  │
│  │  GET  /api/projects/:id    → Get project details                    │  │
│  │  PUT  /api/projects/:id    → Update project                         │  │
│  │  GET  /api/projects/:id/tasks → List tasks for project              │  │
│  │  POST /api/projects/:id/tasks → Create task in project              │  │
│  │  PUT  /api/tasks/:id       → Update task (status, assignment)       │  │
│  │  GET  /api/projects/:id/activity → Get project activity timeline    │  │
│  │  GET  /api/projects/:id/report → Generate standup report            │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ Scheduling & Capabilities ────────────────────────────────────────┐  │
│  │  GET  /api/sessions/:id/capabilities → Get worker capabilities      │  │
│  │  PUT  /api/sessions/:id/capabilities → Update worker capabilities   │  │
│  │  POST /api/sessions/:id/rebrief      → Force re-brief session       │  │
│  │  GET  /api/schedule/suggestions      → Get task assignment recs     │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ Cost & Health ────────────────────────────────────────────────────┐  │
│  │  GET  /api/costs           → Get cost summary (by session, project) │  │
│  │  GET  /api/costs/budget    → Get budget status and alerts           │  │
│  │  GET  /api/health          → Get system health and comm channel status│ │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─ PR Management ────────────────────────────────────────────────────┐  │
│  │  GET  /api/prs             → List all PRs across sessions           │  │
│  │  GET  /api/prs/dependencies → Get PR dependency graph               │  │
│  │  POST /api/prs/dependencies → Add PR dependency                     │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 8.4 Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  DATA FLOW: User checks status via Dashboard                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  User opens http://localhost:8080                                           │
│                    │                                                        │
│                    ▼                                                        │
│            ┌───────────────┐                                                │
│            │  Web Server   │ ──► Serves dashboard HTML/JS                   │
│            └───────┬───────┘                                                │
│                    │                                                        │
│                    ▼                                                        │
│            ┌───────────────┐                                                │
│            │  WebSocket    │ ◄──► Real-time status updates                  │
│            │  Connection   │                                                │
│            └───────┬───────┘                                                │
│                    │                                                        │
│                    ▼                                                        │
│            ┌───────────────┐     ┌───────────────┐                          │
│            │   State DB    │ ←─► │ Passive       │ Polls every 5s           │
│            │   (SQLite)    │     │ Monitor       │ tmux capture-pane        │
│            └───────────────┘     └───────────────┘                          │
│                                                                             │
│  Browser receives: JSON with all session statuses, PRs, pending decisions   │
│  Dashboard renders: Visual grid with color-coded cards                      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│  DATA FLOW: Claude Code reports progress                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Claude Code (on rdev) runs:                                                │
│  curl -X POST http://orchestrator:8080/api/report -d '...'                  │
│                    │                                                        │
│                    ▼                                                        │
│            ┌───────────────┐                                                │
│            │  API Handler  │ ──► Validates JSON payload                     │
│            │  /api/report  │                                                │
│            └───────┬───────┘                                                │
│                    │                                                        │
│                    ▼                                                        │
│            ┌───────────────┐                                                │
│            │   State DB    │ ──► Updates session status, task, PRs          │
│            └───────┬───────┘                                                │
│                    │                                                        │
│                    ▼                                                        │
│            ┌───────────────┐                                                │
│            │  WebSocket    │ ──► Broadcasts update to dashboard             │
│            │  Broadcast    │                                                │
│            └───────────────┘                                                │
│                                                                             │
│  Dashboard auto-refreshes: Session card updates with new status             │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│  DATA FLOW: User sends command via chat                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  User types: "Tell voyager-web to focus on error handling"                  │
│                    │                                                        │
│                    ▼                                                        │
│            ┌───────────────┐                                                │
│            │  Chat Handler │ ──► POST /api/chat                             │
│            └───────┬───────┘                                                │
│                    │                                                        │
│                    ▼                                                        │
│            ┌───────────────┐                                                │
│            │  LLM Brain    │ ──► Anthropic API                              │
│            │               │ ◄── "Send message to voyager-web"              │
│            └───────┬───────┘                                                │
│                    │                                                        │
│                    ▼                                                        │
│            ┌───────────────┐                                                │
│            │   Approval    │ ──► Check if action needs approval             │
│            │   Check       │     (sending to session = medium risk)         │
│            └───────┬───────┘                                                │
│                    │                                                        │
│          ┌────────┴────────┐                                                │
│          │                  │                                               │
│          ▼                  ▼                                               │
│   [Auto-approved]    [Needs approval]                                       │
│          │                  │                                               │
│          │           ┌─────────────┐                                        │
│          │           │ Show dialog │                                        │
│          │           │ in dashboard│                                        │
│          │           └──────┬──────┘                                        │
│          │                  │                                               │
│          │           User clicks [Approve]                                  │
│          │                  │                                               │
│          ▼                  ▼                                               │
│            ┌───────────────┐                                                │
│            │ Action        │                                                │
│            │ Executor      │                                                │
│            └───────┬───────┘                                                │
│                    │                                                        │
│                    ▼                                                        │
│            ┌───────────────┐                                                │
│            │ tmux send-keys│ ──► Sends to voyager-web terminal              │
│            └───────────────┘                                                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 8.5 LLM Brain Design

The LLM Brain is the orchestrator's reasoning engine. It translates user intent into structured actions and provides intelligent monitoring. This section defines how and when it is invoked.

#### 8.5.1 Invocation Triggers

The LLM brain is **not** invoked on every poll cycle. It is invoked only when:

| Trigger | Description | Expected Cost |
|---------|-------------|---------------|
| **User query** | User sends a chat message or command | Per-query (primary use) |
| **State change** | Passive monitor or hooks detect a meaningful state change (session went idle, error detected, PR created) | Low — only on transitions, not continuous polling |
| **Decision request** | A worker requests a decision via MCP/API | Per-decision |
| **Scheduled reconciliation** | Every 5 minutes, the brain reviews overall project health | Fixed interval |
| **Manual re-brief** | User triggers `/rebrief` or orchestrator detects context loss | On-demand |

The LLM brain is **never** invoked for:
- Routine terminal output that matches known patterns (handled by regex)
- Heartbeat checks (handled by simple timestamp comparison)
- Session status polling (handled by tmux commands + pattern matching)

#### 8.5.2 Tiered Intelligence

```
Terminal Output / Event
        │
        ▼
┌─────────────────────┐
│  TIER 1: Regex      │  Fast, deterministic, free
│  Pattern Matching   │
│                     │
│  Detects:           │
│  - "All tests pass" │
│  - "PR #\d+ created"│
│  - Error stack trace│
│  - Claude prompt    │
│    (idle detection) │
│  - Build failed     │
└─────────┬───────────┘
          │
     Ambiguous?
     ┌────┴────┐
     │         │
    No        Yes
     │         │
     ▼         ▼
  Update   ┌─────────────────────┐
  State    │  TIER 2: LLM Brain  │  Expensive, nuanced
  Directly │                     │
           │  Handles:           │
           │  - "Is this session │
           │    stuck or just    │
           │    thinking?"       │
           │  - "What should the │
           │    next task be?"   │
           │  - User NL queries  │
           │  - Decision routing │
           └─────────────────────┘
```

#### 8.5.3 Prompt Architecture

The LLM brain uses a **system prompt + dynamic context** pattern:

```
SYSTEM PROMPT (fixed):
  You are the Claude Orchestrator brain. You manage multiple Claude Code
  sessions. You can take these actions:
  - send_message(session, message): Send a message to a session
  - assign_task(session, task_id): Assign a task to a worker
  - create_task(project_id, title, description): Create a new task
  - update_task(task_id, status): Update task status
  - respond_decision(decision_id, response): Respond to a pending decision
  - alert_user(message, urgency): Surface something to the user
  - rebrief_session(session): Re-send context to a session

  Respond with a JSON array of actions. Include reasoning.

DYNAMIC CONTEXT (assembled per invocation from DB — never hard-coded):
  All context is loaded from the database at query time. The orchestrator
  has zero built-in knowledge about specific projects, repos, or conventions.

  Context sources (all from DB reads):
  - Current session states (name, status, current task, last activity)
  - Worker capabilities (repo, language, tools — from worker_capabilities table)
  - Pending decisions (with context and wait time)
  - Recent activity log (last N events)
  - Project status (if query is project-scoped)
  - Task dependencies and scheduling state
  - Relevant decision history (from decision_history + vector store)
  - Learned patterns (from learned_patterns table)
  - Session snapshots (from session_snapshots table, for recovery context)
  - User query or trigger event description

CONTEXT SELECTION (when total context exceeds budget):
  When the DB contains more context than fits in the LLM window, the
  Smart Context Selector runs (see Section 8.5.5). It does NOT truncate.
  It scores and selects the most relevant items using:
  - Relevance to the current query/trigger
  - Recency (recent events weighted higher)
  - Status (active/blocked items over completed)
  - Urgency (critical decisions over low-priority tasks)
  - Relationship graph (items connected to the query topic)

COST CONTROL:
  - Context is pruned to only include relevant sessions/projects
  - For status queries: include all sessions (summary only)
  - For session-specific queries: include full detail for target, summary for others
  - Max context budget: configurable, default ~4K tokens for context, ~2K for response
```

#### 8.5.4 Action Schema

LLM brain responses are parsed into structured actions:

```json
{
  "reasoning": "The user wants to unblock identity-service and assign idle workers.",
  "actions": [
    {
      "type": "respond_decision",
      "decision_id": "dec-123",
      "response": "Use Redis with PostgreSQL backup for critical sessions.",
      "requires_approval": true
    },
    {
      "type": "assign_task",
      "session": "notifications",
      "task_id": "task-456",
      "message": "Start working on notification auth update.",
      "requires_approval": true
    }
  ],
  "summary": "I'll send your decision to identity-service and assign notifications to the auth update task."
}
```

Actions with `requires_approval: true` are held until the user approves (unless the orchestrator is in Autonomous Mode and the action type is in the auto-approve list).

#### 8.5.5 Smart Context Selection Algorithm

When the orchestrator manages many sessions, projects, tasks, and has accumulated significant decision history, the total available context will exceed the LLM's context window. The Smart Context Selector addresses this by **scoring and selecting** the most relevant context items — never by simple truncation.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  SMART CONTEXT SELECTION                                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  INPUT: All available context items from DB                                │
│  OUTPUT: A context package that fits within the token budget               │
│                                                                             │
│  STEP 1: Categorize context items                                          │
│  ─────────────────────────────────                                          │
│                                                                             │
│  Category A — ALWAYS INCLUDE (high-priority, compact):                     │
│    • System state summary (N sessions, M projects, K pending decisions)    │
│    • Active session list (name + status + current task, one line each)     │
│    • Pending decisions (question + urgency, compact form)                  │
│    • The user's current query / trigger event                              │
│    Estimated: ~500-1000 tokens                                              │
│                                                                             │
│  Category B — SCORE AND SELECT (relevance-ranked):                         │
│    • Session details (full task description, recent output, PRs)           │
│    • Task details (description, subtasks, dependencies)                    │
│    • Decision history (past decisions with context)                        │
│    • Activity log entries                                                   │
│    • Learned patterns                                                      │
│    • Project descriptions and progress details                             │
│                                                                             │
│  Category C — COMPACT SUMMARIES (for items that don't fit):               │
│    • Completed tasks → "12 tasks completed in voyager-web this week"      │
│    • Old decisions → "Previously decided: always use PostgreSQL for..."   │
│    • Inactive sessions → "3 sessions idle, last active 2 hours ago"       │
│                                                                             │
│  STEP 2: Score Category B items                                            │
│  ──────────────────────────────                                             │
│                                                                             │
│  Each item gets a relevance score (0-1) based on:                          │
│                                                                             │
│  score = w1 * query_relevance    # How related to the current query?       │
│        + w2 * recency            # How recent? (exponential decay)         │
│        + w3 * status_weight      # Active/blocked > completed > archived   │
│        + w4 * urgency            # Critical > high > normal > low          │
│        + w5 * connection_depth   # How many hops from query topic?         │
│                                                                             │
│  Default weights: w1=0.35, w2=0.25, w3=0.20, w4=0.10, w5=0.10            │
│                                                                             │
│  STEP 3: Pack context within budget                                        │
│  ──────────────────────────────────                                         │
│                                                                             │
│  1. Include all Category A items (mandatory)                               │
│  2. Sort Category B items by score (descending)                            │
│  3. Greedily add B items until budget is 80% consumed                      │
│  4. Fill remaining 20% with Category C compact summaries                   │
│     for important items that didn't fit in full form                       │
│  5. If budget is still available, add lower-scored B items                  │
│                                                                             │
│  STEP 4: Format for LLM                                                   │
│  ───────────────────────                                                    │
│                                                                             │
│  Structure the selected context as:                                        │
│  ```                                                                        │
│  ## Current State                                                           │
│  [Category A: always-include items]                                        │
│                                                                             │
│  ## Relevant Details                                                        │
│  [Category B: scored and selected items, highest score first]              │
│                                                                             │
│  ## Background                                                              │
│  [Category C: compact summaries of items that didn't fit]                  │
│                                                                             │
│  ## Your Query                                                              │
│  [The user's question or the trigger event]                                │
│  ```                                                                        │
│                                                                             │
│  SCALING BEHAVIOR:                                                          │
│  ─────────────────                                                          │
│  • 1-5 sessions:   Everything likely fits. No selection needed.            │
│  • 5-15 sessions:  Category B scoring kicks in. Full details for           │
│                    relevant sessions, summaries for others.                │
│  • 15-50 sessions: Aggressive compaction. Only query-relevant sessions     │
│                    get full context. Others are one-line summaries.        │
│  • 50+ sessions:   Category A becomes the dominant context.                │
│                    B is limited to top-3 by score. C covers the rest.     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 8.6 State Schema

```sql
-- =============================================================================
-- CORE ENTITIES
-- =============================================================================

-- Projects (high-level initiatives)
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'active',  -- active, paused, completed, archived
    target_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Workers (Sessions/Claude Code instances)
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    host TEXT NOT NULL,              -- SSH host or 'local'
    mp_path TEXT,                    -- Working directory
    tmux_window TEXT,                -- tmux target (e.g., orchestrator:0)
    status TEXT DEFAULT 'idle',      -- idle, working, waiting, error, disconnected
    takeover_mode BOOLEAN DEFAULT FALSE,
    current_task_id TEXT REFERENCES tasks(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_activity TIMESTAMP
);

-- Project-Worker assignments (many-to-many)
CREATE TABLE project_workers (
    project_id TEXT REFERENCES projects(id),
    session_id TEXT REFERENCES sessions(id),
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, session_id)
);

-- =============================================================================
-- TASK MANAGEMENT
-- =============================================================================

-- Tasks within projects
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'todo',      -- todo, in_progress, done, blocked
    priority INTEGER DEFAULT 0,
    assigned_session_id TEXT REFERENCES sessions(id),
    blocked_by_decision_id TEXT REFERENCES decisions(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

-- Task dependencies (optional)
CREATE TABLE task_dependencies (
    task_id TEXT REFERENCES tasks(id),
    depends_on_task_id TEXT REFERENCES tasks(id),
    PRIMARY KEY (task_id, depends_on_task_id)
);

-- =============================================================================
-- PR TRACKING
-- =============================================================================

-- Pull requests (linked to tasks and sessions)
CREATE TABLE pull_requests (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES tasks(id),
    session_id TEXT REFERENCES sessions(id),
    url TEXT NOT NULL,
    number INTEGER,
    title TEXT,
    status TEXT DEFAULT 'open',      -- open, in_review, approved, merged, closed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    merged_at TIMESTAMP
);

-- =============================================================================
-- DECISION MANAGEMENT
-- =============================================================================

-- Decision queue (blockers requiring human input)
CREATE TABLE decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    task_id TEXT REFERENCES tasks(id),
    session_id TEXT REFERENCES sessions(id),
    question TEXT NOT NULL,
    options TEXT,                    -- JSON array of options
    context TEXT,
    urgency TEXT DEFAULT 'normal',   -- low, normal, high, critical
    status TEXT DEFAULT 'pending',   -- pending, responded, dismissed
    response TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    resolved_by TEXT                 -- user who resolved
);

-- Decision history (for learning)
CREATE TABLE decision_history (
    id TEXT PRIMARY KEY,
    decision_id TEXT REFERENCES decisions(id),
    project_id TEXT,
    question TEXT,
    context TEXT,
    decision TEXT,
    user_feedback TEXT,
    was_helpful BOOLEAN,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- ACTIVITY TRACKING
-- =============================================================================

-- Activity log (timeline events)
CREATE TABLE activities (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    task_id TEXT REFERENCES tasks(id),
    session_id TEXT REFERENCES sessions(id),
    event_type TEXT NOT NULL,        -- task_started, task_completed, pr_created, 
                                     -- pr_merged, decision_requested, decision_made,
                                     -- blocker_added, blocker_resolved, worker_assigned
    event_data TEXT,                 -- JSON with event-specific data
    actor TEXT,                      -- 'system', 'user', or session name
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- LEARNING & INTELLIGENCE
-- =============================================================================

-- Learned patterns
CREATE TABLE learned_patterns (
    id TEXT PRIMARY KEY,
    pattern_type TEXT,               -- decision, task_routing, error_handling
    pattern_key TEXT,
    pattern_value TEXT,
    confidence REAL,
    usage_count INTEGER DEFAULT 0,
    last_used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- WORKER CAPABILITIES & TASK SCHEDULING
-- =============================================================================

-- Worker capability profiles
CREATE TABLE worker_capabilities (
    session_id TEXT REFERENCES sessions(id),
    capability_type TEXT NOT NULL,     -- repo, language, tool, environment
    capability_value TEXT NOT NULL,    -- e.g., "voyager-web", "typescript", "rdev"
    PRIMARY KEY (session_id, capability_type, capability_value)
);

-- Task requirements (what capabilities a task needs)
CREATE TABLE task_requirements (
    task_id TEXT REFERENCES tasks(id),
    requirement_type TEXT NOT NULL,    -- repo, language, tool, environment
    requirement_value TEXT NOT NULL,
    PRIMARY KEY (task_id, requirement_type, requirement_value)
);

-- PR dependencies (merge ordering)
CREATE TABLE pr_dependencies (
    pr_id TEXT REFERENCES pull_requests(id),
    depends_on_pr_id TEXT REFERENCES pull_requests(id),
    PRIMARY KEY (pr_id, depends_on_pr_id)
);

-- =============================================================================
-- COST TRACKING
-- =============================================================================

-- API cost tracking per session and orchestrator
CREATE TABLE cost_events (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),  -- NULL for orchestrator's own costs
    source TEXT NOT NULL,             -- 'worker_session', 'orchestrator_brain', 'orchestrator_monitor'
    model TEXT,                       -- model ID used
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_cost_usd REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- SESSION RECOVERY
-- =============================================================================

-- Context snapshots for session recovery after /compact or restart
CREATE TABLE session_snapshots (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    task_summary TEXT,               -- Current task description and progress
    key_decisions TEXT,              -- JSON: recent decisions relevant to this session
    file_paths TEXT,                 -- JSON: files the session was working on
    last_known_state TEXT,           -- What the session was doing when snapshot was taken
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- COMMUNICATION RELIABILITY
-- =============================================================================

-- Track communication channel health per session
CREATE TABLE comm_events (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    channel TEXT NOT NULL,           -- 'mcp', 'hooks', 'curl', 'passive'
    event_type TEXT NOT NULL,        -- 'message_sent', 'message_received', 'missed_event', 'channel_down'
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Schema version tracking for migrations
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

-- =============================================================================
-- INDEXES
-- =============================================================================

CREATE INDEX idx_tasks_project ON tasks(project_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_session ON tasks(assigned_session_id);
CREATE INDEX idx_prs_task ON pull_requests(task_id);
CREATE INDEX idx_decisions_status ON decisions(status);
CREATE INDEX idx_activities_project ON activities(project_id);
CREATE INDEX idx_activities_created ON activities(created_at);
CREATE INDEX idx_cost_events_session ON cost_events(session_id);
CREATE INDEX idx_cost_events_created ON cost_events(created_at);
CREATE INDEX idx_session_snapshots_session ON session_snapshots(session_id);
CREATE INDEX idx_comm_events_session ON comm_events(session_id);
CREATE INDEX idx_worker_caps_session ON worker_capabilities(session_id);
CREATE INDEX idx_config_key ON config(key);

-- =============================================================================
-- CONFIGURATION & TEMPLATES (DB-Driven, Zero Hard-Coded Context)
-- =============================================================================

-- System configuration (approval policies, context weights, autonomy settings, budgets)
CREATE TABLE config (
    key TEXT PRIMARY KEY,              -- e.g., 'approval_policy.send_message', 'context.weight.recency'
    value TEXT NOT NULL,               -- JSON-encoded value
    description TEXT,                  -- Human-readable description of this setting
    category TEXT,                     -- 'approval', 'context', 'autonomy', 'budget', 'monitoring'
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- LLM prompt templates (loaded at runtime, never hard-coded)
CREATE TABLE prompt_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,         -- e.g., 'system_prompt', 'status_query', 'task_planning', 'rebrief'
    template TEXT NOT NULL,            -- Prompt template with ${variable} placeholders
    description TEXT,                  -- What this template is used for
    version INTEGER DEFAULT 1,
    is_active BOOLEAN DEFAULT TRUE,    -- Allow A/B testing of templates
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Skill templates (installed into remote sessions via tmux send-keys, stored in DB not code)
CREATE TABLE skill_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,         -- e.g., 'orchestrator', 'orchestrator-minimal'
    version INTEGER DEFAULT 1,         -- Version marker for update detection
    template TEXT NOT NULL,            -- Skill content with ${variable} placeholders
    install_instruction TEXT,          -- The message typed into Claude Code to create this skill
    description TEXT,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 8.7 Code Structure

The orchestrator codebase follows a modular architecture where each module maps to a functional domain. Modules communicate through well-defined interfaces (Python protocols/ABCs), and all domain-specific context flows from the database — never from hard-coded values in the source.

```
orchestrator/
├── pyproject.toml                     # Project metadata, dependencies
├── config.yaml                        # Bootstrap config (DB path, port — NOT domain context)
│
├── orchestrator/                      # Main Python package
│   ├── __init__.py
│   ├── __main__.py                    # Entry point: python -m orchestrator
│   ├── main.py                        # Application init, dependency injection, lifecycle
│   │
│   ├── core/                          # Orchestration engine
│   │   ├── __init__.py
│   │   ├── orchestrator.py            # Main orchestrator: event loop, coordination
│   │   ├── lifecycle.py               # Startup, shutdown, restart, session adoption
│   │   └── events.py                  # Event bus: internal pub/sub between modules
│   │
│   ├── auth/                          # Authentication
│   │   ├── __init__.py
│   │   ├── keychain.py                # macOS Keychain read/write
│   │   └── token.py                   # Token validation, refresh, caching
│   │
│   ├── terminal/                      # tmux & SSH management
│   │   ├── __init__.py
│   │   ├── manager.py                 # tmux session CRUD (create, list, kill windows)
│   │   ├── session.py                 # Individual session lifecycle (connect, send, capture)
│   │   ├── ssh.py                     # SSH connection wrapper (connect, tunnel, health check)
│   │   ├── skill_installer.py         # Install/update /orchestrator skill in Claude Code sessions
│   │   └── output_parser.py           # Tier 1: regex patterns for terminal output detection
│   │
│   ├── comm/                          # Communication channels (Claude Code ↔ Orchestrator)
│   │   ├── __init__.py
│   │   ├── mcp_server.py              # MCP server: expose orchestrator tools to Claude Code
│   │   ├── hooks.py                   # Hook configuration: generate & inject .claude/hooks/
│   │   ├── heartbeat.py               # Heartbeat monitor: detect stale/unresponsive sessions
│   │   └── reconciler.py              # State reconciliation: cross-check DB vs actual state
│   │
│   ├── state/                         # Database & state management
│   │   ├── __init__.py
│   │   ├── db.py                      # SQLite connection, WAL mode, query helpers
│   │   ├── models.py                  # Dataclasses: Session, Task, Project, Decision, PR, etc.
│   │   ├── repositories/              # Data access layer (one per entity)
│   │   │   ├── __init__.py
│   │   │   ├── sessions.py            # CRUD for sessions + worker_capabilities
│   │   │   ├── projects.py            # CRUD for projects + project_workers
│   │   │   ├── tasks.py               # CRUD for tasks + task_dependencies + task_requirements
│   │   │   ├── decisions.py           # Decision queue: create, respond, dismiss, history
│   │   │   ├── pull_requests.py       # PR tracking + pr_dependencies
│   │   │   ├── activities.py          # Activity log: append-only event stream
│   │   │   ├── config.py              # Config table: get/set/list by category
│   │   │   └── templates.py           # Prompt templates + skill templates
│   │   └── migrations/                # Schema versioning
│   │       ├── __init__.py
│   │       ├── runner.py              # Migration runner: detect version, apply pending
│   │       └── versions/              # One .sql file per migration
│   │           ├── 001_initial.sql
│   │           ├── 002_add_comm_tables.sql
│   │           └── ...
│   │
│   ├── scheduler/                     # Task scheduling & worker matching
│   │   ├── __init__.py
│   │   ├── matcher.py                 # Match tasks to workers by capability requirements
│   │   ├── scheduler.py               # Priority queue: pick next task for idle workers
│   │   ├── dependencies.py            # Dependency graph: resolve order, detect cycles
│   │   └── conflicts.py               # Conflict detection: overlapping file paths, semantic
│   │
│   ├── llm/                           # LLM Brain (Tier 2 intelligence)
│   │   ├── __init__.py
│   │   ├── client.py                  # Anthropic API client (SDK or curl fallback)
│   │   ├── brain.py                   # Core reasoning: assemble context → call LLM → parse actions
│   │   ├── context_selector.py        # Smart context selection algorithm (Section 8.5.5)
│   │   ├── actions.py                 # Action schema: parse, validate, execute action list
│   │   └── templates.py               # Template renderer: load from DB, substitute variables
│   │
│   ├── recovery/                      # Session recovery & context preservation
│   │   ├── __init__.py
│   │   ├── detector.py                # Detect: /compact, restart, crash, context loss
│   │   ├── snapshot.py                # Create & restore session context snapshots
│   │   └── rebrief.py                 # Re-brief: compose and send recovery context to session
│   │
│   ├── cost/                          # Cost tracking & budget management
│   │   ├── __init__.py
│   │   ├── tracker.py                 # Log cost events per LLM call
│   │   ├── budget.py                  # Budget enforcement: check ceiling, emit alerts
│   │   └── reports.py                 # Cost reports: by session, project, time period
│   │
│   ├── api/                           # REST API (for dashboard + Claude Code reporting)
│   │   ├── __init__.py
│   │   ├── app.py                     # FastAPI app factory
│   │   ├── routes/                    # Route modules (one per domain)
│   │   │   ├── __init__.py
│   │   │   ├── sessions.py            # /api/sessions/*
│   │   │   ├── projects.py            # /api/projects/*
│   │   │   ├── tasks.py               # /api/tasks/*
│   │   │   ├── decisions.py           # /api/decisions/*
│   │   │   ├── chat.py                # /api/chat/*
│   │   │   ├── reporting.py           # /api/report, /api/decision, /api/guidance, /api/hook
│   │   │   ├── costs.py               # /api/costs/*
│   │   │   ├── prs.py                 # /api/prs/*
│   │   │   └── health.py              # /api/health
│   │   ├── websocket.py               # Native WebSocket: real-time dashboard updates
│   │   └── middleware.py              # Auth middleware, request logging
│   │
│   ├── web/                           # Web dashboard (served by API module)
│   │   ├── templates/
│   │   │   └── index.html             # SPA shell
│   │   └── static/
│   │       ├── app.js                 # Dashboard frontend (vanilla JS or lightweight framework)
│   │       ├── styles.css             # Dashboard styles
│   │       └── xterm.min.js           # Terminal emulator for takeover mode
│   │
│   └── knowledge/                     # Vector store & learning (Phase 3)
│       ├── __init__.py
│       ├── vectors.py                 # ChromaDB: store/query decision embeddings
│       └── learning.py                # Pattern extraction: analyze decision history
│
├── tests/                             # Test suite
│   ├── conftest.py                    # Shared fixtures (test DB, mock tmux, mock LLM)
│   ├── unit/                          # Unit tests (no external deps)
│   │   ├── test_models.py
│   │   ├── test_context_selector.py
│   │   ├── test_matcher.py
│   │   ├── test_dependencies.py
│   │   ├── test_actions.py
│   │   ├── test_output_parser.py
│   │   └── test_budget.py
│   ├── integration/                   # Integration tests (real DB, mock tmux)
│   │   ├── test_repositories.py
│   │   ├── test_migrations.py
│   │   ├── test_api_routes.py
│   │   ├── test_mcp_server.py
│   │   └── test_terminal_manager.py
│   └── e2e/                           # End-to-end tests (full stack with tmux fixtures)
│       ├── test_session_lifecycle.py
│       ├── test_decision_flow.py
│       └── test_recovery.py
│
├── scripts/
│   ├── test_auth.py                   # Auth testing script (exists)
│   └── seed_db.py                     # Seed DB with sample config, templates, test data
│
└── docs/
    ├── PRD.md
    └── IMPLEMENTATION.md
```

#### 8.7.1 Module Dependency Rules

To keep the codebase maintainable, modules follow strict dependency rules:

```
                    ┌─────────┐
                    │  core/  │  ← Depends on all modules below
                    └────┬────┘
                         │
         ┌───────────────┼───────────────────┐
         │               │                   │
    ┌────▼────┐    ┌─────▼─────┐    ┌───────▼───────┐
    │  llm/   │    │ scheduler/│    │   recovery/   │
    └────┬────┘    └─────┬─────┘    └───────┬───────┘
         │               │                   │
         └───────────────┼───────────────────┘
                         │
         ┌───────────────┼───────────────────┐
         │               │                   │
    ┌────▼────┐    ┌─────▼─────┐    ┌───────▼───────┐
    │  comm/  │    │ terminal/ │    │    cost/      │
    └────┬────┘    └─────┬─────┘    └───────┬───────┘
         │               │                   │
         └───────────────┼───────────────────┘
                         │
                    ┌────▼────┐
                    │ state/  │  ← Foundation: DB, models, repositories
                    └────┬────┘
                         │
                    ┌────▼────┐
                    │  auth/  │  ← Standalone: no deps on other modules
                    └─────────┘

    api/ and web/ sit alongside core/ — they expose modules via HTTP/WebSocket.
    knowledge/ is optional and plugs into llm/ when available.
```

**Rules:**
- **state/** depends on nothing (except stdlib and SQLite). All other modules depend on state/.
- **auth/** is standalone — no dependencies on other orchestrator modules.
- **terminal/**, **comm/**, **cost/** depend only on **state/**.
- **llm/**, **scheduler/**, **recovery/** depend on **state/** and lower-tier modules.
- **core/** depends on everything — it's the composition root.
- **api/** exposes all modules via HTTP; it depends on core/ but modules do NOT depend on api/.
- No circular dependencies. Dependency injection (constructor params) is used throughout.

#### 8.7.2 Key Design Patterns

| Pattern | Usage |
|---------|-------|
| **Repository pattern** | All DB access goes through `state/repositories/`. No raw SQL outside this layer. |
| **Event bus** | `core/events.py` — modules publish events, other modules subscribe. Decouples terminal detection from LLM reasoning from dashboard updates. |
| **Strategy pattern** | `comm/` — communication channels (MCP, hooks, curl, passive) share an interface. The orchestrator tries channels in priority order. |
| **Template method** | `llm/brain.py` — context assembly → LLM call → action parsing follows a fixed flow; each step is overridable. |
| **Dependency injection** | `main.py` wires all modules together. Tests inject mocks. No module creates its own dependencies. |

---

## 9. User Interface

### 9.1 CLI Interface

#### 9.1.1 Main Chat Loop

```
🚀 Claude Orchestrator v1.0
   Managing 3 sessions | 5 PRs open | 2 decisions pending

🧑 You: What's the status of all sessions?

🤖 Orchestrator: Here's the current status:

┌─────────────────┬──────────┬─────────────────────┬──────────┐
│ Session         │ Status   │ Current Task        │ PRs      │
├─────────────────┼──────────┼─────────────────────┼──────────┤
│ rdev-voyager    │ Working  │ Implementing login  │ 2 open   │
│ rdev-payments   │ Waiting  │ Need decision on DB │ 1 merged │
│ rdev-api        │ Idle     │ Completed refactor  │ 2 open   │
└─────────────────┴──────────┴─────────────────────┴──────────┘

⚠️  rdev-payments is waiting for your decision:
    "Should I use PostgreSQL or keep MySQL for the new service?"

🧑 You: Let's go with PostgreSQL for better JSON support

🤖 Orchestrator: Got it. I'll send this decision to rdev-payments.

📤 Sending to rdev-payments: "Use PostgreSQL for the new service. 
   Reason: Better JSON support."

✅ Sent. rdev-payments will continue with the implementation.
```

#### 9.1.2 Slash Commands

| Command | Description | Example |
|---------|-------------|---------|
| `/add <name> <host> [path]` | Add new session | `/add voyager rdev-voyager.host /src/voyager-web` |
| `/remove <name>` | Remove session | `/remove voyager` |
| `/list` | List all sessions | `/list` |
| `/status [name]` | Get detailed status | `/status voyager` |
| `/output <name> [lines]` | Get terminal output | `/output voyager 50` |
| `/send <name> <message>` | Send message to session | `/send voyager focus on the login bug` |
| `/broadcast <message>` | Send to all sessions | `/broadcast pause and wait for review` |
| `/attach <name>` | Attach to tmux session | `/attach voyager` |
| `/decisions` | Show pending decisions | `/decisions` |
| `/prs` | Show all PRs | `/prs` |
| `/config` | Show/edit configuration | `/config` |
| `/export` | Export conversation | `/export` |
| `/help` | Show help | `/help` |

#### 9.1.3 Status Bar

```
─────────────────────────────────────────────────────────────────
📊 Sessions: 3 active | 🔀 PRs: 5 open, 2 merged | ⚠️  Decisions: 2
─────────────────────────────────────────────────────────────────
```

### 9.2 Direct tmux Access

The orchestrator's tmux session is **globally accessible** from any terminal on the same machine. This provides power users with a direct, no-dashboard-needed interface to browse and interact with sessions.

**How it works:** The orchestrator creates a tmux session (e.g., `orchestrator`) with one window per Claude Code session. Any terminal can attach independently:

```
# List orchestrator sessions
$ tmux ls
orchestrator: 4 windows (created ...)

# Attach and browse all sessions interactively
$ tmux attach -t orchestrator

# Attach in read-only mode (watch without accidentally typing)
$ tmux attach -t orchestrator -r
```

**Multi-client support:** Multiple terminals can attach to the same tmux session simultaneously. Each client independently navigates to different windows — the user can be looking at `voyager-web` (window 0) in one terminal while the orchestrator works with `payments-api` (window 3) in another, without interference.

**Dashboard integration:** The web dashboard shows "Attach Terminal" instructions per session and a global `tmux attach -t orchestrator` command in the header. For users who prefer terminal-native workflows, direct tmux access provides the same capability without a browser.

### 9.3 Web Dashboard

```
┌─────────────────────────────────────────────────────────────────┐
│  Claude Orchestrator Dashboard                    [Settings] [?] │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  Sessions                                        [+ Add]    ││
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐       ││
│  │  │ voyager │  │ payments│  │ api     │  │ ...     │       ││
│  │  │ 🟢 work │  │ 🟡 wait │  │ 🔵 idle │  │         │       ││
│  │  │ 2 PRs   │  │ 1 PR    │  │ 2 PRs   │  │         │       ││
│  │  └─────────┘  └─────────┘  └─────────┘  └─────────┘       ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  ┌──────────────────────┐  ┌──────────────────────────────────┐│
│  │  Decision Queue       │  │  Recent Activity                 ││
│  │  ━━━━━━━━━━━━━━━━━━  │  │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━  ││
│  │  ⚠️ payments: DB?     │  │  10:32 - voyager: PR #123 created││
│  │  ⚠️ voyager: API ver? │  │  10:28 - payments: Task started  ││
│  │                       │  │  10:15 - api: PR #456 merged     ││
│  └──────────────────────┘  └──────────────────────────────────┘│
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  Chat with Orchestrator                                     ││
│  │  ─────────────────────────────────────────────────────────  ││
│  │  🤖: All systems operational. 2 decisions pending.          ││
│  │  🧑: What's blocking payments?                              ││
│  │  🤖: Waiting for DB choice: PostgreSQL vs MySQL             ││
│  │  ─────────────────────────────────────────────────────────  ││
│  │  [                                          ] [Send]        ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

---

## 10. Security & Privacy

### 10.1 Authentication Security

| Concern | Mitigation |
|---------|------------|
| API token storage | Use macOS Keychain (encrypted) |
| Token in memory | Clear after use, don't log |
| Token transmission | HTTPS only to Anthropic API |

### 10.2 SSH Security

| Concern | Mitigation |
|---------|------------|
| SSH credentials | Use SSH agent, no password storage |
| Host verification | Respect known_hosts |
| Session isolation | Each session in separate tmux window |

### 10.3 Data Privacy

| Data Type | Handling |
|-----------|----------|
| Terminal output | Stored locally only, auto-purged |
| Conversation history | Local SQLite, encrypted at rest option |
| Code context | Never sent to external services except Anthropic |
| Decision history | Local only, used for learning |

### 10.4 Access Control

- Orchestrator runs locally on user's machine only
- No network exposure by default
- Optional REST API requires authentication token

---

## 11. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Token expiration mid-session | Medium | High | Detect 401 and prompt re-auth |
| SSH connection drops | High | Medium | Auto-reconnect with backoff |
| tmux session lost | Low | High | Persist session names, reconnect |
| LLM hallucination | Medium | Medium | Require confirmation for actions |
| Terminal output overflow | Medium | Low | Limit capture size, rotate logs |
| User sends wrong command | Medium | Medium | Confirmation prompts, undo support |
| rdev not accessible | Medium | Medium | Graceful error handling, retry |
| Claude Code doesn't use /orchestrator skill proactively | Medium | Medium | Orchestrator types explicit reminders via tmux send-keys; hooks + passive monitor as safety net |
| Claude Code `/compact` loses orchestrator context | High | Medium | Skill persists in .claude/commands/ (survives /compact); orchestrator re-briefs with task state |
| Orchestrator LLM costs exceed budget | Medium | Medium | Tiered intelligence (regex first); cost ceiling with alerts |
| Two workers make conflicting changes | Medium | High | Conflict detection via overlapping file path analysis; alert before merge |
| Worker creates PR that breaks CI | High | Medium | Monitor CI status; auto-assign fix-up task to same worker |
| Orchestrator crashes mid-operation | Low | High | SQLite WAL mode; state persisted on every mutation; clean recovery on restart |
| Network routing: rdev cannot reach orchestrator API | Medium | High | SSH reverse tunnel setup during session creation; fallback to passive-only monitoring |
| Session context window exhausted | Medium | Medium | Detect via passive monitor; trigger `/compact` then re-brief with task context |

### 11.1 Failure Scenarios & Recovery Playbooks

#### Scenario 1: SSH Connection Drops Mid-Task

```
Detection: Passive monitor sees no output change + SSH health check fails
Timeline:  0s → stale detection (60s) → SSH reconnect attempt → retry with backoff
Recovery:
  1. Mark session as "disconnected" in dashboard (show red indicator)
  2. Attempt SSH reconnect with exponential backoff (5s, 15s, 30s, 60s)
  3. If reconnect succeeds:
     a. Check if Claude Code is still running (detect prompt in terminal)
     b. If running: resume passive monitoring, log "reconnected" event
     c. If not running: restart Claude Code, verify /orchestrator skill, send re-brief
  4. If reconnect fails after 5 attempts:
     a. Alert user via dashboard notification
     b. Pause any tasks assigned to this worker
     c. Suggest reassignment of in-progress tasks to other available workers
```

#### Scenario 2: Claude Code Compacts or Restarts

```
Detection: Passive monitor detects Claude Code startup banner or /compact output
           OR active reports stop arriving while terminal is still active
Recovery:
  1. Wait 10 seconds for Claude Code to fully initialize
  2. Check if /orchestrator skill still exists in .claude/commands/
     - If missing (e.g., repo was re-cloned): reinstall skill via tmux send-keys
     - If present: skip (skill survives /compact and restarts)
  3. Re-register MCP server connection (if using MCP channel)
  4. Send re-brief message via tmux send-keys:
     "You are working on project [name], task: [title].
      Progress: [summary of completed subtasks].
      Key decisions made: [list of relevant decisions].
      Use /orchestrator to report progress. Please continue where you left off."
  5. Log "context_recovery" event in activity timeline
  6. Monitor for 60s to confirm session resumes productive work
```

#### Scenario 3: Worker Creates a Failing PR

```
Detection: Hooks detect PR creation → orchestrator polls CI status → CI fails
           OR passive monitor detects CI failure output in terminal
Recovery:
  1. Update PR status in database to "ci_failing"
  2. Notify user via dashboard: "PR #123 from voyager-web has failing CI"
  3. If auto-fix is enabled (configurable):
     a. Send message to the same worker: "PR #123 CI is failing. Please investigate
        and fix the issues. CI output: [summary of failures]"
     b. Track this as a sub-task of the original task
  4. If auto-fix is disabled: surface as a decision for the user
```

#### Scenario 4: Conflicting Changes Across Workers

```
Detection: Two workers report PRs modifying overlapping file paths
           OR LLM brain detects semantic conflict in task descriptions
Recovery:
  1. Alert user immediately via dashboard (high urgency)
  2. Show: which workers, which files, which PRs
  3. Suggest resolution options:
     a. Pause one worker until the other's PR merges
     b. Have workers coordinate (send context from Worker A to Worker B)
     c. User manually resolves
  4. If task dependencies exist, enforce merge order
```

#### Scenario 5: Orchestrator Crashes and Restarts

```
Detection: User runs `orchestrator` command and it detects a previous unclean shutdown
Recovery:
  1. Load last known state from SQLite (WAL mode ensures consistency)
  2. Enumerate existing tmux windows: `tmux list-windows -t orchestrator`
  3. For each tmux window:
     a. Match against saved sessions in DB
     b. Check if Claude Code is still running
     c. Capture recent terminal output to infer current state
  4. For matched sessions: restore monitoring (passive + re-register MCP)
  5. For orphaned windows (in tmux but not in DB): prompt user to adopt or ignore
  6. For missing windows (in DB but not in tmux): mark as "disconnected", attempt reconnect
  7. Resume all pending decisions and tasks
  8. Log "orchestrator_recovery" event with summary of recovered state
```

#### Scenario 6: Task Dependency Deadlock

```
Detection: Scheduling engine detects a cycle in task dependencies
           OR a task has been in "blocked" state for > N hours
Recovery:
  1. Visualize the dependency chain in the dashboard
  2. Highlight the cycle or the long-blocked bottleneck
  3. Suggest resolution:
     a. Remove a dependency to break the cycle
     b. Reassign the blocking task to a higher-priority worker
     c. Decompose the blocking task into smaller, unblocked pieces
  4. Alert user if a blocked task is on the critical path for the project deadline
```

---

## 12. Future Considerations

### 12.1 Version 2.0 Features

- **Multi-user support**: Share orchestrator across team
- **Jira integration**: Sync tasks with tickets
- **GitHub App**: Automatic PR status updates
- **Slack notifications**: Alert on critical decisions
- **Custom workflows**: Define approval chains
- **Audit logging**: Compliance-ready logging

### 12.2 Platform Expansion

- **Linux support**: Alternative to macOS Keychain
- **Windows support**: WSL integration
- **Cloud deployment**: Remote orchestrator option

### 12.3 AI Enhancements

- **Predictive decisions**: Suggest decisions based on patterns
- **Anomaly detection**: Alert on unusual behavior
- **Cross-session learning**: Share learnings across sessions
- **Natural language configuration**: "Don't bother me with formatting PRs"

### 12.4 Autonomy & Intelligence

- **Autonomous Mode vs Advisory Mode**: User-configurable autonomy spectrum. In Advisory Mode, the orchestrator only reports status and surfaces decisions — it never acts without explicit approval. In Autonomous Mode, it can assign tasks to idle workers, route routine decisions based on learned patterns, create new sessions, and even trigger re-briefs — all governed by configurable guard rails and an audit log. Users can tune the level per action category (e.g., "auto-approve task assignment but always ask before sending messages").

- **Natural Language Project Planning**: Instead of manually creating tasks, the user describes an initiative in natural language (e.g., "Modernize auth across voyager-web, identity-service, and payments-api to use OAuth 2.0 + JWT"). The LLM brain decomposes this into a task graph with dependencies, suggests worker assignments based on repo capabilities, and estimates effort based on historical velocity. The user reviews and approves before execution begins.

- **Session-to-Session Communication**: Workers sometimes need to coordinate — e.g., "identity-service, please expose endpoint X so payments-api can consume it." The orchestrator mediates cross-session messaging: Worker A can request a capability from Worker B, the orchestrator routes the request, and tracks the dependency until fulfilled.

### 12.5 Observability & Review

- **Execution Replay**: Since all events, decisions, and state transitions are logged, build a "DVR for AI development" — a replay UI that lets users step through a project's entire execution history chronologically. Useful for debugging ("why did the payments worker go down this path?"), learning ("how did we resolve the last auth migration?"), and sharing post-mortems with the team.

- **PR Dependency Graph**: Visualize and enforce PR merge ordering across repositories. In multi-repo refactors, PRs often have implicit ordering constraints (e.g., the identity-service PR must merge before the payments-api PR that consumes it). The orchestrator can infer these from task dependencies and alert when merge order is violated.

- **Decision Pattern Analytics**: Analyze the decision history to surface patterns: "You always choose PostgreSQL over MySQL", "You prefer to investigate test failures rather than skip them", "You typically approve formatting PRs without review." Use these insights to suggest automation rules.

### 12.6 Cost Intelligence

- **Cost Attribution**: Break down API costs by project, task, and worker. Surface which types of tasks are most expensive and which workers are most cost-efficient.
- **Smart Session Management**: Auto-pause idle sessions to reduce API costs. Resume on demand or when new tasks are assigned.
- **Model Selection per Task**: Use cheaper/faster models for routine tasks (status checks, pattern matching) and more capable models for complex reasoning (architectural decisions, cross-session conflict resolution).

---

## Appendix A: Glossary

| Term | Definition |
|------|------------|
| **rdev** | Remote development environment (VM for development) |
| **MP** | Multiproduct - LinkedIn's term for a repository/service |
| **Session** | A managed connection to a remote Claude Code instance |
| **Worker** | A Claude Code session (terminal) that executes tasks; synonymous with Session in execution context |
| **Decision** | A question from a Claude Code session requiring human input |
| **Orchestrator** | The meta-agent managing all sessions |
| **MCP** | Model Context Protocol — structured communication interface between Claude Code and external tools/servers |
| **Hooks** | Shell commands configured to fire automatically in response to Claude Code tool-call events |
| **Skill (Slash Command)** | A custom Claude Code command stored in `.claude/commands/`. The orchestrator installs an `/orchestrator` skill that teaches Claude Code how to report to the orchestrator API |
| **Re-brief** | The act of re-sending current task context to a session after context loss (compact/restart) |
| **Passive Monitor** | Background process that polls terminal output to detect session state changes |
| **Active Reporting** | When Claude Code proactively sends events to the orchestrator (via MCP, curl, or hooks) |
| **Reconciliation** | Periodic cross-check between known state and actual state (tmux, git, PRs) to detect missed events |
| **Tiered Intelligence** | Pattern: use cheap regex matching for routine detection, invoke LLM only for ambiguous situations |
| **Advisory Mode** | Orchestrator only reports status and surfaces decisions; never acts without explicit approval |
| **Autonomous Mode** | Orchestrator can take configurable actions (assign tasks, route decisions) without per-action approval |
| **Claude Code** | Anthropic's CLI tool for AI-assisted software development, running in terminals |
| **tmux** | Terminal multiplexer — enables managing multiple terminal sessions from a single window, scriptable via commands |
| **LLM Brain** | The orchestrator's reasoning engine that uses LLM API calls to process user queries and decide actions |
| **Context Window** | The maximum amount of text an LLM can process in a single call; exceeding it requires context selection |
| **RAG** | Retrieval-Augmented Generation — enhancing LLM responses by retrieving relevant stored information |
| **WAL Mode** | Write-Ahead Logging — SQLite journal mode that enables concurrent reads during writes and crash recovery |
| **Seed Script** | A script that populates the database with initial configuration, prompt templates, and default settings; used for bootstrapping a fresh installation |
| **Event Bus** | Internal publish/subscribe system that decouples modules — e.g., terminal monitor publishes "session_idle", scheduler subscribes to assign next task |
| **Repository Pattern** | Data access pattern where all DB queries are encapsulated in repository classes, keeping SQL out of business logic |

## Appendix B: References

- [Claude Code Documentation](https://docs.anthropic.com/claude-code)
- [tmux Manual](https://man7.org/linux/man-pages/man1/tmux.1.html)
- [Anthropic API Reference](https://docs.anthropic.com/claude/reference)

---

*Document Version History*

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-07 | Yudong Qiu | Initial draft |
| 1.1 | 2026-02-07 | Yudong Qiu | Added: Executive Summary, Communication Robustness (MCP server, hooks, heartbeat/reconciliation), Session Recovery & Context Preservation, Worker Capability & Task Scheduling, LLM Brain Design (tiered intelligence, prompt architecture, action schema), Cross-Session Communication, Cost & Resource Management, PR Dependency Management, Replay & Audit, Failure Scenarios & Recovery Playbooks, Testing Strategy, Schema Migration Strategy, Integration Strategy, expanded glossary, new DB schema tables, new API endpoints, Future Considerations (Autonomous/Advisory mode, NL project planning, session-to-session comms, execution replay, cost intelligence) |
| 1.2 | 2026-02-07 | Yudong Qiu | Added: Core Design Principles (Zero Hard-Coded Context, DB-Driven Everything, Smart Context Selection), Context Management FR section (6.18) with 11 requirements, Smart Context Selection Algorithm (Section 8.5.5) with scoring formula and scaling behavior, updated LLM Brain prompt architecture to be fully DB-driven. Fixed author name. |
| 1.3 | 2026-02-07 | Yudong Qiu | Final review: Added Section 8.7 Code Structure with full module tree, dependency rules, and design patterns. Added missing DB tables (config, prompt_templates, skill_templates). Fixed Table of Contents ordering and completeness. Expanded glossary with 11 additional terms. |
| 1.4 | 2026-02-07 | Yudong Qiu | Replaced CLAUDE.md injection with skill-based approach. The orchestrator now installs a custom `/orchestrator` slash command in each session by typing into Claude Code like a real user (via tmux send-keys). Skills persist in .claude/commands/, don't conflict with repo files, survive /compact, and are idempotent. Updated Section 7.9, communication protocol (0.8), recovery flows, risk table, DB schema (claudemd_templates → skill_templates), code structure (added skill_installer.py), and glossary. |
| 1.5 | 2026-02-07 | Yudong Qiu | Standardized on FastAPI + native WebSocket (removed Flask/Socket.IO references). Added Section 9.2 Direct tmux Access as first-class feature. Updated testing strategy with Playwright development workflow for iterative UI development. Aligned with IMPLEMENTATION.md v2.0. |
