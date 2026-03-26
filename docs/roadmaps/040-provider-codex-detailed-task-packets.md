# 040: Provider-Aware Claude and Codex Detailed Task Packets

This document expands the task backlog into execution-grade packets that can be assigned to subagents with minimal rewriting.

Primary references:

- `../design_logs/038-provider-codex-compatibility-plan.md`
- `039-provider-codex-task-backlog.md`

## Why This Document Exists

The backlog in `039` is sufficient for milestone planning, but subagent execution needs more precision:

- exact write scope
- exact goal and non-goals
- constraints to protect Claude behavior
- expected interfaces or artifacts
- required tests
- clear acceptance criteria

These packets are the intended handoff units for parallel implementation.

## Global Constraints

Every packet below inherits these rules:

- Claude behavior is the protected baseline.
- Prefer wrapping existing Claude behavior over rewriting it.
- Keep provider branching centralized in capability maps and adapters.
- Do not implement remote Codex support for MVP.
- Unsupported provider features should remain visible in the UI but disabled with tooltip explanations.
- Provider badges must be visible everywhere a session is shown.
- Worker and brain default providers are separate settings.
- Mixed-provider dashboards must remain understandable.

## Shared Vocabulary

Use this conceptual model consistently:

- `provider`: concrete runtime identity for a session, initially `claude` or `codex`
- `capability`: feature support flag for UI and backend branching
- `adapter`: provider-specific runtime implementation
- `default worker provider`: used when creating new workers unless overridden
- `default brain provider`: used when starting/creating the brain unless overridden

## Packet 1: Capability and Settings Contract

Contains:

- `PC-001`
- `PC-003`

### Goal

Create the provider capability registry and split worker/brain default provider settings so the rest of the stack has a stable contract to build on.

### Why First

Every other packet needs:

- a canonical list of provider names
- a canonical list of provider capabilities
- separate worker and brain defaults

Without this packet, UI gating and runtime branching will drift.

### Primary write scope

- new provider capability module(s), likely under `orchestrator/providers/`
- `orchestrator/config_defaults.py`
- `orchestrator/api/routes/settings.py`

### Read-only context to consult

- `docs/design_logs/038-provider-codex-compatibility-plan.md`
- `docs/roadmaps/039-provider-codex-task-backlog.md`
- `frontend/src/pages/SettingsPage.tsx`

### Deliverables

1. A backend-visible provider registry that defines:
   - provider ID
   - display label
   - capability flags
   - short disabled-tooltip reasons for unsupported features where relevant
2. New setting defaults for:
   - default worker provider
   - default brain provider
3. Settings API responses that can surface these defaults cleanly.

### Suggested structure

- Add a provider definition object or module with entries for `claude` and `codex`.
- Keep this additive and simple; do not over-generalize for hypothetical providers.
- Capabilities should be coarse and product-facing, not low-level implementation toggles.

### Minimum capability list

At minimum, define capabilities for:

- model selection
- effort selection
- dangerous skip permissions
- quick clear
- heartbeat loop
- hooks
- skills deployment
- provider session identity tracking
- reconnect support

### Non-goals

- No session DB changes.
- No worker/brain launch changes.
- No frontend component refactors beyond what is needed to expose settings data cleanly.

### Tests

- Settings defaults tests for the new worker/brain provider keys.
- API tests showing the new settings appear exactly once and merge correctly.
- Small unit tests for provider registry integrity if appropriate.

### Acceptance criteria

- Backend has one source of truth for `claude` and `codex` capabilities.
- Worker and brain default provider settings exist and are returned by the settings API.
- No Claude launch or UI behavior changes yet.

## Packet 2: Session Provider Data Plumbing

Contains:

- `PC-002`
- `PC-004`

### Goal

Add `provider` as a first-class session field and plumb it through backend responses and frontend state.

### Why First

The UI cannot show badges or branch behavior safely until provider is actually stored and returned on sessions.

### Primary write scope

- `orchestrator/state/models.py`
- `orchestrator/state/repositories/sessions.py`
- new migration file under `orchestrator/state/migrations/versions/`
- `orchestrator/api/routes/sessions.py`
- `frontend/src/api/types.ts`
- frontend state/context code that consumes session payloads

### Read-only context to consult

- existing session create/list/serialize flow in `orchestrator/api/routes/sessions.py`
- migration patterns under `orchestrator/state/migrations/versions/`
- frontend session consumers under `frontend/src/context/`

### Deliverables

1. Session DB schema supports `provider`.
2. Existing rows migrate to `provider='claude'`.
3. Session repository create/read/list/update paths preserve provider.
4. Session APIs serialize provider.
5. Frontend session types and state carry provider.

### Suggested behavior

- New worker sessions should default provider from the worker default setting unless explicitly provided.
- Brain session provider handling may initially remain routed via default brain provider at start time, but the session object itself should still carry a provider value once created.
- Avoid renaming `claude_session_id` in this packet.

### Non-goals

- No UI changes yet beyond not breaking session rendering.
- No runtime adapter work.

### Tests

- Migration test for old rows receiving `provider='claude'`.
- Repository tests for create/list/get returning provider.
- API route tests for session create/list/get returning provider.
- Frontend type and state tests if existing harness makes that practical.

### Acceptance criteria

- Provider exists end-to-end in session payloads.
- Existing Claude sessions behave as before.
- Existing data upgrades safely.

## Packet 3: Provider UX Surface

Contains:

- `PC-005`
- `PC-006`
- `PC-007`

### Goal

Expose provider choice and provider constraints throughout the UI without confusing mixed-provider usage.

### Why Now

Once provider exists in state, the UI can become honest about what is supported. This improves clarity even before Codex runtime support is complete.

### Primary write scope

- `frontend/src/components/sessions/AddSessionModal.tsx`
- `frontend/src/pages/SettingsPage.tsx`
- `frontend/src/components/brain/BrainPanel.tsx`
- `frontend/src/pages/DashboardPage.tsx`
- `frontend/src/pages/WorkersPage.tsx`
- `frontend/src/pages/SessionDetailPage.tsx`
- worker card/session badge/task preview components
- shared tooltip or common UI utilities if needed

### Read-only context to consult

- provider capability registry from Packet 1
- session type plumbing from Packet 2

### Deliverables

1. Provider selector in worker creation.
2. Separate default provider controls for worker and brain settings.
3. Provider badges shown everywhere a session appears.
4. Tooltip-disabled controls for unsupported capabilities.
5. Copy updates where the app currently implies all sessions are Claude.

### Provider badge rule

Any component that shows a session name, worker identity, or brain session identity should show a provider badge nearby. This includes:

- dashboard worker cards
- workers page cards/rows
- session detail header
- worker assignment previews
- task-level worker previews
- notifications or session-linked labels where it adds clarity

### Disabled control rule

- Unsupported controls should remain visible.
- Disabled state should be obvious.
- Tooltip text should explain why the control is unavailable for that provider.
- Do not silently hide major controls unless the surface would otherwise become misleading or broken.

### Non-goals

- No Codex runtime implementation.
- No reconnect/health changes.

### Tests

- Frontend tests for provider selector rendering and submission.
- Frontend tests for provider badge presence in major surfaces.
- Frontend tests for tooltip-disabled controls based on capabilities.

### Acceptance criteria

- Users can see and choose providers clearly.
- Mixed Claude/Codex dashboards remain understandable.
- Codex sessions no longer expose active Claude-only controls.
- Claude sessions still expose the current control set.

## Packet 4: Adapter Extraction

Contains:

- `PC-008`
- `PC-009`
- `PC-011`

### Goal

Introduce provider runtime interfaces and route worker/brain lifecycle through them, with Claude wrapped as the reference adapter.

### Why This Matters

This is the most important structural change. Without it, Codex implementation will either duplicate logic badly or contaminate the Claude path with scattered conditionals.

### Primary write scope

- new provider adapter package, likely:
  - `orchestrator/providers/base.py`
  - `orchestrator/providers/claude.py`
  - provider registry helpers
- `orchestrator/terminal/session.py`
- `orchestrator/api/routes/sessions.py`
- `orchestrator/api/routes/brain.py`

### Read-only context to consult

- current Claude launch paths in `orchestrator/terminal/session.py`
- current brain lifecycle in `orchestrator/api/routes/brain.py`

### Deliverables

1. Provider adapter interface(s) for worker and brain lifecycle.
2. Claude implementation moved behind those interfaces.
3. Worker creation route chooses runtime behavior by provider.
4. Brain lifecycle route chooses runtime behavior by provider.

### Design guidance

- Keep interfaces narrow and based on current actual needs.
- Separate worker and brain concerns if that keeps the interface cleaner.
- Prefer adapter methods that return normalized status/result objects.
- Do not try to solve reconnect in this packet.

### Concrete responsibilities to move behind adapters

Worker-side:

- deploy provider assets
- build launch command
- start local worker
- start remote worker if provider supports it

Brain-side:

- deploy provider assets
- start brain
- stop brain
- redeploy brain assets
- expose quick-command capabilities

### Non-goals

- No Codex implementation yet beyond wiring seams.
- No lifecycle parity work.

### Tests

- Route-level tests proving Claude worker and brain paths still function through adapters.
- Unit tests around adapter selection and normalized return values.

### Acceptance criteria

- Claude behavior is preserved.
- Session and brain routes do not inline provider-specific launch details anymore.
- Codex can be added as a new adapter without further architectural changes.

## Packet 5: Local Codex MVP Runtime

Contains:

- `PC-010`
- `PC-012`

### Goal

Implement local Codex support for both workers and the brain with a workflow that feels coherent enough for MVP.

### Why Local Only

Remote Codex support is explicitly out of MVP scope. Keeping this packet local-only reduces risk and protects the Claude remote path.

### Primary write scope

- `orchestrator/providers/codex.py` or similar
- provider-specific deploy/assets
- any Codex-specific prompt/settings/templates
- provider-specific brain/worker startup logic
- minor UI alignment changes if capability assumptions need refinement

### Read-only context to consult

- Claude adapter implementation from Packet 4
- capability matrix from Packet 1

### Deliverables

1. Local Codex worker adapter.
2. Local Codex brain adapter.
3. Provider-specific asset deployment and launch behavior for Codex.
4. Codex-aligned capability choices reflected in the UI.

### Minimum viable support

Workers:

- create local worker with `provider='codex'`
- launch into usable terminal session
- send text to session
- view terminal output

Brain:

- start local brain with `provider='codex'`
- stop brain
- use the brain terminal as a real workflow surface

### Known likely deviations from Claude

Codex may not support:

- Claude-style hooks
- Claude-style slash commands
- Claude-specific heartbeat loop model
- Claude-specific settings file shapes
- Claude-specific session identity tracking

Where parity is not possible in MVP:

- leave the UI control visible but disabled via capabilities
- do not emulate behavior weakly unless it is essential to usability

### Non-goals

- Remote Codex support
- Full reconnect parity

### Tests

- Worker create/start tests for local Codex path.
- Brain start/stop tests for local Codex path.
- Mixed-provider render tests where appropriate.

### Acceptance criteria

- Local Codex workers and brain are usable.
- Mixed-provider dashboards remain clear.
- Claude local and remote behavior remain intact.

## Packet 6: Lifecycle Hardening and Coverage

Contains:

- `PC-013`
- `PC-014`
- `PC-015`

### Goal

Make health and reconnect provider-aware and add regression coverage to keep the migration safe.

### Why Last

Lifecycle behavior is tightly coupled to current Claude semantics and is high-risk. It should be adapted only after provider routing and local Codex launch are stable.

### Primary write scope

- `orchestrator/session/health.py`
- `orchestrator/session/reconnect.py`
- provider lifecycle helper modules as needed
- backend and frontend tests

### Read-only context to consult

- Claude adapter implementation
- current Claude-specific lifecycle code
- any Codex runtime constraints discovered in Packet 5

### Deliverables

1. Provider-aware health checks.
2. Provider-aware reconnect strategy selection.
3. Clear Codex lifecycle behavior for MVP, even if limited.
4. Mixed-provider regression tests.

### Guidance on scope

- Preserve Claude reconnect behavior as much as possible by moving it behind a Claude lifecycle strategy.
- For Codex, it is acceptable in MVP to have limited reconnect support if:
  - behavior is explicit
  - UI reflects the limitation
  - sessions fail safely rather than pretending to recover

### Non-goals

- Perfect provider-agnostic lifecycle abstraction if it overcomplicates the code.
- Renaming all legacy Claude lifecycle fields in the same packet.

### Tests

- Claude reconnect regression tests.
- Health-check tests that branch by provider.
- Mixed-provider session list/detail rendering tests if UI behavior changes due to lifecycle state.

### Acceptance criteria

- Claude lifecycle behavior remains intact.
- Codex lifecycle behavior is safe and explicit.
- Mixed-provider flows are covered by tests at the MVP level.

## Remaining Product Questions

These are the main questions that may still affect implementation detail:

- Which Codex capabilities, if any, should be approximated rather than disabled when there is no direct Claude-equivalent feature?
- Should the brain session display its provider badge differently from worker sessions to emphasize that it is the primary orchestration surface?
- If Codex reconnect support is partial in MVP, should the UI show a provider-specific warning in session detail or only expose disabled reconnect controls?

## Recommended Next Step for Execution

Start with Packets 1 and 2 in parallel. They have mostly disjoint write scopes and create the contract needed for all downstream work.

After that:

- Packet 3 can proceed in parallel with Packet 4.
- Packet 5 should wait until Packet 4 stabilizes.
- Packet 6 should follow once Codex local runtime behavior is understood.
