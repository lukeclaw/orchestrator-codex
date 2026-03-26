# 038: Provider-Aware Claude and Codex Compatibility Plan

## Goal

Make the orchestrator provider-aware so Claude and Codex sessions can coexist cleanly in the same dashboard, with Claude behavior preserved and Codex introduced as a first-class option.

## Product Decisions

- Provider is selectable per session.
- Settings define default providers separately for new workers and the brain.
- Claude and Codex may coexist in the same dashboard.
- Provider badges must be visible anywhere a session is shown.
- Unsupported provider-specific features should remain visible but disabled with tooltip explanations.
- Preserving current Claude behavior is the highest priority.
- Codex MVP must support both workers and the brain.
- Remote Codex support is not required for MVP.

## Non-Goals for MVP

- Full remote Codex lifecycle parity.
- Full reconnect parity if Codex resume semantics differ materially.
- Renaming every Claude-specific internal field during the first migration.

## Current Problem

The app is structurally a Claude-native orchestrator, not a provider-neutral one.

Claude assumptions are embedded in:

- Session creation and worker launch
- Brain start/stop/redeploy flows
- Settings and frontend copy
- Agent prompt, hook, and settings deployment
- Health checks and reconnect logic
- Session identity tracking via `claude_session_id`
- UI affordances such as `/clear`, `/loop`, `/heartbeat`, skip-permissions, and model/effort controls

This means Codex support cannot be added safely by sprinkling provider conditionals across the codebase.

## Architectural Direction

Introduce four concepts:

### 1. Provider identity

Each session has a `provider`, initially `claude` or `codex`.

### 2. Provider capabilities

A single source of truth defines what each provider supports. Example capabilities:

- model selection
- effort selection
- hooks
- skills deployment
- dangerous skip permissions
- resume support
- provider session identity tracking
- heartbeat loop
- quick clear command

Both backend and frontend should derive behavior from this matrix.

### 3. Provider runtime adapters

Provider-specific logic should move behind a narrow interface:

- launch worker
- launch brain
- deploy provider assets
- build launch command
- check alive
- stop
- reconnect
- expose UI capabilities

The existing Claude path should be wrapped first, not rewritten.

### 4. Provider UX policy

The UI should show a clear provider badge everywhere a session appears and use tooltip-disabled controls for unsupported actions.

## Migration Principles

- Preserve Claude behavior by isolating it behind a Claude adapter.
- Avoid a broad rename of legacy Claude identifiers during early phases.
- Prefer capability-driven UI over ad hoc `if provider === ...` checks.
- Separate provider-neutral orchestration from provider-specific runtime concerns.
- Deliver Codex MVP on local workflows first.

## Major Touch Points

### Data model and settings

- `orchestrator/state/models.py`
- `orchestrator/state/repositories/sessions.py`
- `orchestrator/state/migrations/versions/*`
- `orchestrator/config_defaults.py`
- `orchestrator/api/routes/settings.py`
- `frontend/src/api/types.ts`

### Session and brain lifecycle

- `orchestrator/api/routes/sessions.py`
- `orchestrator/api/routes/brain.py`
- `orchestrator/terminal/session.py`
- `orchestrator/session/health.py`
- `orchestrator/session/reconnect.py`

### Agent assets and prompts

- `orchestrator/agents/deploy.py`
- `agents/brain/prompt.md`
- `agents/worker/prompt.md`
- `agents/brain/settings.json`
- `agents/worker/settings.json`

### Frontend capability gating

- `frontend/src/pages/SettingsPage.tsx`
- `frontend/src/components/sessions/AddSessionModal.tsx`
- `frontend/src/components/brain/BrainPanel.tsx`
- `frontend/src/pages/DashboardPage.tsx`
- `frontend/src/pages/WorkersPage.tsx`
- `frontend/src/pages/SessionDetailPage.tsx`

## Phased Plan

### Phase 0: Capability definition and migration guardrails

- Define provider capability matrix.
- Define disabled-tooltip UX policy.
- Define provider badge placement policy.
- Freeze Claude behavior as reference implementation.

### Phase 1: Provider spine

- Add session-level provider.
- Add separate default provider settings for worker and brain creation.
- Return provider information from APIs.
- Plumb provider through frontend state and types.

### Phase 2: Capability-driven UI

- Add provider selection to worker creation.
- Expose default worker/brain provider settings.
- Show provider badge everywhere a session is rendered.
- Convert Claude-only UI affordances into tooltip-disabled controls when unsupported.

### Phase 3: Worker runtime adapters

- Wrap existing Claude worker launch path behind a provider adapter.
- Add Codex worker adapter.
- Separate provider-neutral setup from provider-specific launch and asset deployment.

### Phase 4: Brain runtime adapters

- Wrap existing Claude brain lifecycle behind a provider adapter.
- Add Codex brain adapter.
- Gate brain quick actions by capability.

### Phase 5: Health and reconnect

- Move provider-specific alive detection behind adapters.
- Move provider-specific resume and reconnect behavior behind adapters.
- Preserve existing Claude reconnect behavior while adding Codex-safe behavior.

### Phase 6: Hardening and cleanup

- Add mixed-provider test coverage.
- Audit copy, docs, and onboarding.
- Normalize naming only where safe.

## MVP Definition

MVP should include:

- Per-session provider selection
- Separate default providers for worker and brain creation
- Visible provider badges across the app
- Tooltip-disabled unsupported controls
- Claude behavior preserved
- Local Codex worker launch
- Local Codex brain launch
- Basic terminal-driven workflow continuity for both

MVP may defer:

- Remote Codex support
- Full reconnect parity if Codex lifecycle semantics differ
- Advanced automation parity where Codex lacks a direct equivalent

## Key Risks

### Brain automation parity

Claude currently relies on CLI semantics such as `/loop`, `/heartbeat`, `/clear`, and hook-driven lifecycle integration. Codex may require a different automation model.

### Hook-driven worker status

The current worker status model is closely tied to Claude hooks and metadata. Codex may need alternative status integration.

### Session identity and reconnect

`claude_session_id` is deeply wired into the current reconnect path. The safest migration is to isolate it first and generalize later.

### Remote deployment assumptions

Current deployment assumes Claude-specific config directories and launch conventions. Codex should be introduced through a parallel provider path, not by mutating the Claude path in place.

## Recommended Execution Strategy

- Use small, reviewable phases with disjoint write scopes when possible.
- Parallelize UI and plumbing work after the provider spine lands.
- Keep provider branching centralized in adapters and capability maps.
- Do not rewrite the current Claude implementation in place until it has been wrapped behind provider abstractions.
