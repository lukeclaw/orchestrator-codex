# 039: Provider-Aware Claude and Codex Task Backlog

This document turns the provider-compatibility plan into an execution backlog with milestones, dependencies, batching guidance, and subagent-ready task packets.

Primary reference: `../design_logs/038-provider-codex-compatibility-plan.md`

## Confirmed Product Decisions

- Provider is selectable per session.
- Settings define default providers separately for new workers and the brain.
- Claude and Codex may coexist in the same dashboard.
- Provider badges must be visible anywhere a session is shown.
- Unsupported provider-specific features should remain visible but disabled with tooltip explanations.
- Preserving current Claude behavior is the highest priority.
- Codex MVP must support both workers and the brain.
- Remote Codex support is not required for MVP.

## Milestones

### M1: Provider Spine

Complete:

- `PC-001`
- `PC-002`
- `PC-003`
- `PC-004`

Outcome:

- Sessions have a provider.
- Worker and brain defaults are separately configurable.
- Provider data flows through backend and frontend without changing Claude behavior.

### M2: Provider UX

Complete:

- `PC-005`
- `PC-006`
- `PC-007`

Outcome:

- Users can choose providers intentionally.
- Provider badges are visible everywhere.
- Unsupported controls are tooltip-disabled instead of hidden.

### M3: Codex MVP Launch

Complete:

- `PC-008`
- `PC-009`
- `PC-010`
- `PC-011`
- `PC-012`

Outcome:

- Claude runtime behavior is preserved behind adapters.
- Local Codex workers and brain can launch and operate.

### M4: Lifecycle Hardening

Complete:

- `PC-013`
- `PC-014`
- `PC-015`

Outcome:

- Mixed-provider lifecycle behavior is safe.
- Claude reconnect remains intact.
- Codex lifecycle behavior is explicit and tested.

## Task Backlog

### PC-001: Provider Capability Registry

Status: planned

Goal:

Create a single source of truth for provider capabilities and disabled-tooltip reasons.

Dependencies:

- None

Primary files:

- New provider registry module under `orchestrator/providers/` or similar
- Shared frontend/backend types if needed

Acceptance criteria:

- `claude` and `codex` capability sets are defined in one place.
- Disabled UI states can resolve to a short tooltip reason from this registry.
- No behavior changes to existing Claude flows.

### PC-002: Session Provider Data Model

Status: planned

Goal:

Persist `provider` on sessions and default existing rows safely to `claude`.

Dependencies:

- None

Primary files:

- `orchestrator/state/models.py`
- `orchestrator/state/repositories/sessions.py`
- `orchestrator/state/migrations/versions/*`

Acceptance criteria:

- Session records include `provider`.
- Existing sessions migrate safely to `provider='claude'`.
- API consumers can receive provider data without breaking Claude behavior.

### PC-003: Split Default Provider Settings

Status: planned

Goal:

Add separate settings for default worker provider and default brain provider.

Dependencies:

- `PC-001`

Primary files:

- `orchestrator/config_defaults.py`
- `orchestrator/api/routes/settings.py`

Acceptance criteria:

- Worker default provider and brain default provider are independently configurable.
- Defaults are merged and returned through the existing settings API.

### PC-004: Frontend Provider Plumbing

Status: planned

Goal:

Teach frontend types and app state about session providers and default provider settings.

Dependencies:

- `PC-002`
- `PC-003`

Primary files:

- `frontend/src/api/types.ts`
- frontend context/state files that consume session payloads and settings

Acceptance criteria:

- Frontend session models expose `provider`.
- Default provider settings are readable in the UI layer.
- Existing Claude screens still render correctly.

### PC-005: Provider Selector in Create Flows

Status: planned

Goal:

Add provider selection to worker creation and expose separate worker/brain defaults in settings.

Dependencies:

- `PC-004`

Primary files:

- `frontend/src/components/sessions/AddSessionModal.tsx`
- `frontend/src/pages/SettingsPage.tsx`

Acceptance criteria:

- New worker creation allows provider selection.
- Worker and brain default providers are configurable in settings.
- Selected/default provider values flow to the backend correctly.

### PC-006: Provider Badges Everywhere

Status: planned

Goal:

Display provider badges anywhere a session is visible.

Dependencies:

- `PC-004`

Primary files:

- `frontend/src/pages/DashboardPage.tsx`
- `frontend/src/pages/WorkersPage.tsx`
- `frontend/src/pages/SessionDetailPage.tsx`
- worker card components
- task worker preview components
- any other session rendering surfaces

Acceptance criteria:

- Every session list, card, row, preview, and detail view shows a provider badge.
- Badge styling is consistent and visually clear in mixed-provider dashboards.

### PC-007: Tooltip-Disabled Capability Gating

Status: planned

Goal:

Keep unsupported controls visible but disabled with tooltip explanations.

Dependencies:

- `PC-001`
- `PC-004`

Primary files:

- `frontend/src/pages/SettingsPage.tsx`
- `frontend/src/components/brain/BrainPanel.tsx`
- `frontend/src/pages/SessionDetailPage.tsx`

Acceptance criteria:

- Codex sessions do not present active Claude-only controls.
- Disabled controls show concise tooltip reasons.
- Claude sessions continue to expose the full existing control set.

### PC-008: Provider Adapter Interface and Claude Wrapper

Status: planned

Goal:

Introduce provider runtime interfaces and wrap the current Claude implementation behind them.

Dependencies:

- `PC-001`

Primary files:

- New provider adapter package
- `orchestrator/terminal/session.py`
- `orchestrator/api/routes/brain.py`

Acceptance criteria:

- Existing Claude worker and brain flows are routed through adapter interfaces.
- Claude behavior remains functionally unchanged.
- Provider branching is centralized, not spread across route handlers.

### PC-009: Worker Creation Through Adapters

Status: planned

Goal:

Make worker session creation resolve launch behavior through provider adapters.

Dependencies:

- `PC-008`
- `PC-002`

Primary files:

- `orchestrator/api/routes/sessions.py`

Acceptance criteria:

- Worker startup is chosen by session provider.
- Existing Claude worker startup still works exactly as before.

### PC-010: Local Codex Worker Adapter

Status: planned

Goal:

Implement local Codex worker startup and supporting assets for MVP.

Dependencies:

- `PC-008`
- `PC-009`

Primary files:

- Codex provider runtime files
- any provider-specific deploy/asset files needed for local worker launch

Acceptance criteria:

- Local Codex workers can launch successfully.
- Terminal interaction works for Codex workers.
- MVP experience remains coherent in mixed Claude/Codex dashboards.

### PC-011: Brain Lifecycle Through Adapters

Status: planned

Goal:

Route brain start/stop/redeploy through provider adapters and gate brain quick actions by capability.

Dependencies:

- `PC-008`
- `PC-003`

Primary files:

- `orchestrator/api/routes/brain.py`
- related provider runtime files

Acceptance criteria:

- Claude brain behavior is preserved.
- Brain lifecycle resolution is provider-aware.

### PC-012: Local Codex Brain Adapter

Status: planned

Goal:

Implement local Codex brain startup and minimum brain workflow support for MVP.

Dependencies:

- `PC-011`

Primary files:

- Codex brain runtime files
- provider-specific asset deployment and prompt/settings files as needed

Acceptance criteria:

- Local Codex brain can start, stop, and accept user input.
- Brain remains a core workflow path, not a degraded side feature.

### PC-013: Provider-Aware Health Checks

Status: planned

Goal:

Move alive detection behind provider logic.

Dependencies:

- `PC-008`

Primary files:

- `orchestrator/session/health.py`

Acceptance criteria:

- Claude health checks are unchanged in behavior.
- Health detection no longer assumes every session is Claude.

### PC-014: Provider-Aware Reconnect Strategy

Status: planned

Goal:

Move reconnect logic behind provider-aware strategies while protecting the current Claude path.

Dependencies:

- `PC-013`
- `PC-009`
- `PC-011`

Primary files:

- `orchestrator/session/reconnect.py`

Acceptance criteria:

- Claude reconnect behavior remains intact.
- Codex reconnect behavior is implemented safely or explicitly constrained for MVP.

### PC-015: Mixed-Provider Regression and MVP Tests

Status: planned

Goal:

Cover mixed-provider core flows and protect current Claude behavior from regression.

Dependencies:

- `PC-006` through `PC-014` as applicable

Primary files:

- backend tests
- frontend tests

Acceptance criteria:

- Claude baseline flows are regression-covered.
- Mixed-provider session rendering is covered.
- Codex MVP worker and brain launch flows are covered.

## Suggested Batching

### Batch A: Foundation

Tasks:

- `PC-001`
- `PC-002`
- `PC-003`
- `PC-004`

Reason:

Everything else depends on this contract layer.

### Batch B: UI Surface

Tasks:

- `PC-005`
- `PC-006`
- `PC-007`

Reason:

Can proceed once provider data and capabilities are available.

### Batch C: Adapter Extraction

Tasks:

- `PC-008`
- `PC-009`
- `PC-011`

Reason:

Create clean seams before implementing Codex runtime behavior.

### Batch D: Codex MVP Runtime

Tasks:

- `PC-010`
- `PC-012`

Reason:

Depends on adapter seams but can largely proceed provider-locally afterward.

### Batch E: Lifecycle Hardening

Tasks:

- `PC-013`
- `PC-014`
- `PC-015`

Reason:

This is safer after launch and UI behavior are stable.

## Subagent-Ready Task Packets

### Packet 1: Capability and Settings Contract

Contains:

- `PC-001`
- `PC-003`

Goal:

Define provider capabilities and backend default-provider settings.

Owned files:

- provider registry module(s)
- `orchestrator/config_defaults.py`
- `orchestrator/api/routes/settings.py`

Constraints:

- No Claude behavior changes.
- Keep changes additive and contract-focused.

Expected output:

- Provider capability source of truth
- Worker and brain default-provider settings exposed via API

### Packet 2: Session Provider Data Plumbing

Contains:

- `PC-002`
- `PC-004`

Goal:

Persist provider on sessions and surface it through backend/frontend data flow.

Owned files:

- `orchestrator/state/models.py`
- `orchestrator/state/repositories/sessions.py`
- migration files
- `frontend/src/api/types.ts`
- frontend state consumers

Constraints:

- Existing rows default to `claude`.
- Maintain backward-safe API behavior.

Expected output:

- Session provider stored, returned, and rendered-capable in frontend state

### Packet 3: Provider UX Surface

Contains:

- `PC-005`
- `PC-006`
- `PC-007`

Goal:

Make provider choice and provider constraints visible throughout the UI.

Owned files:

- `frontend/src/components/sessions/AddSessionModal.tsx`
- `frontend/src/pages/SettingsPage.tsx`
- `frontend/src/components/brain/BrainPanel.tsx`
- dashboard/session/worker rendering components

Constraints:

- Provider badges must appear everywhere sessions are shown.
- Unsupported controls remain visible but tooltip-disabled.

Expected output:

- Mixed-provider UI that is understandable before Codex runtime parity fully lands

### Packet 4: Adapter Extraction

Contains:

- `PC-008`
- `PC-009`
- `PC-011`

Goal:

Wrap Claude runtime paths behind provider adapters and route worker/brain creation through them.

Owned files:

- provider adapter package
- `orchestrator/terminal/session.py`
- `orchestrator/api/routes/sessions.py`
- `orchestrator/api/routes/brain.py`

Constraints:

- Prefer moving existing Claude code behind interfaces over rewriting it.
- Preserve existing Claude behavior.

Expected output:

- Centralized provider branching
- Claude adapter in place as reference implementation

### Packet 5: Local Codex MVP Runtime

Contains:

- `PC-010`
- `PC-012`

Goal:

Implement local Codex worker and brain support for MVP.

Owned files:

- Codex provider runtime files
- provider-specific deploy/asset files as needed

Constraints:

- Local only for MVP.
- Keep mixed-provider UX coherent.

Expected output:

- Local Codex worker and brain startup support

### Packet 6: Lifecycle Hardening and Coverage

Contains:

- `PC-013`
- `PC-014`
- `PC-015`

Goal:

Make lifecycle management provider-aware and protect the migration with tests.

Owned files:

- `orchestrator/session/health.py`
- `orchestrator/session/reconnect.py`
- related tests

Constraints:

- Claude reconnect is the protected baseline.
- Codex lifecycle behavior must be safe even if parity is partial.

Expected output:

- Provider-aware lifecycle logic
- Mixed-provider regression coverage

## Recommended Execution Order

1. `Packet 1`
2. `Packet 2`
3. `Packet 3`
4. `Packet 4`
5. `Packet 5`
6. `Packet 6`

## Execution Notes

- Keep provider branching centralized in adapters and capability maps.
- Do not start by rewriting Claude internals in place.
- Prefer disjoint write scopes for parallel work.
- Use Claude behavior as the regression baseline throughout the migration.
