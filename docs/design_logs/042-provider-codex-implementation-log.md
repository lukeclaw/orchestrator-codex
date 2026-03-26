# 042 Provider/Codex Implementation Log

Date: 2026-03-25

## Purpose

This document records the implementation history and design decisions behind the provider-aware Orchestrator work, with Claude preserved as the reference path and Codex added as a first-class local provider.

It is intended to answer:
- what changed
- why the architecture changed the way it did
- which commits matter
- what remains intentionally incomplete

## Core Product Decisions

These decisions shaped the implementation:

- provider is selectable per session
- worker default provider and brain default provider are configured separately
- Claude and Codex can coexist in the same dashboard
- provider badges should be visible anywhere a session or brain identity is shown
- unsupported features should generally remain visible but disabled with tooltip explanation
- preserving Claude behavior is the protected baseline
- local Codex support is in scope for MVP; remote Codex is not
- reconnect for Codex should fail safely until the runtime contract is strong enough

## Architecture Decisions

### 1. Provider-aware core, not a Claude-vs-Codex fork

The system was moved toward:
- provider-aware session identity
- provider capabilities
- provider runtime adapters

This avoided scattering `if provider == "codex"` branches through routes and UI.

### 2. Claude remains the reference implementation

Claude behavior was wrapped rather than rewritten.

This reduced migration risk:
- current Claude launch behavior stayed intact
- current Claude brain lifecycle stayed intact
- current Claude reconnect semantics stayed intact

### 3. Capability-driven UX

Provider support is represented as capabilities rather than ad hoc UI conditions.

This drives:
- disabled tooltip reasons
- provider-aware settings visibility
- quick-action gating
- reconnect gating

### 4. Provider-specific context with shared fallback

Context items gained a nullable `provider` field:
- `NULL` means shared
- `"claude"` means Claude-specific
- `"codex"` means Codex-specific

Consumers now read shared plus matching-provider context by default.

### 5. Codex parity should prefer safe product behavior over fake Claude parity

Examples:
- Codex reconnect remains disabled
- Codex quick-clear and heartbeat were only enabled after explicit runtime behavior existed
- Codex hooks were investigated but not integrated

## Major Implementation Milestones

### Provider spine

Commits:
- `4107dc5` `feat: add provider registry and default settings`
- `406ad32` `feat: persist session provider metadata`

What changed:
- provider registry and capability model
- worker and brain default provider settings
- `provider` persisted on sessions
- provider exposed through backend and frontend types

### Provider UX and visibility

Commits:
- `9b040e9` `feat: add provider selection to worker modal`
- `3907330` `feat: add provider defaults and gating to settings`
- `89dd133` `feat: gate brain quick actions by provider`
- `52e3977` `feat: add provider badges to worker surfaces`
- `cbafb63` `feat: add provider badges across task and brain views`
- `9fda2be` `fix: clarify mixed-provider ui copy`
- `b65e6f2` `feat: show brain provider across mixed-provider ui`

What changed:
- provider selection when creating workers
- provider defaults in settings
- provider badges across workers, brain, tasks, notifications, and project surfaces
- tooltip-based feature gating
- reduced Claude-only wording in mixed-provider UI

### Runtime adapter extraction

Commits:
- `9fd3117` `refactor: route worker launch through provider runtime`
- `7a74fc0` `refactor: route brain lifecycle through provider runtime`
- `28ff6e8` `fix: stabilize brain route validation coverage`

What changed:
- worker launch moved behind provider runtime interfaces
- brain start/stop/redeploy moved behind provider runtime interfaces
- Claude became an explicit provider adapter instead of implicit global behavior

### Local Codex runtime

Commits:
- `2e66e41` `feat: add local codex runtime adapter`
- `ee285e7` `fix: make codex session controls provider-aware`

What changed:
- local Codex worker runtime
- local Codex brain runtime
- provider-aware control handling
- dedicated Codex prompts under `agents/codex/...`

### Codex reconnect safety

Commits:
- `6a4a864` `fix: disable unsupported codex reconnect flows`
- `694a4e7` `feat: disable codex reconnect controls in ui`

What changed:
- Codex reconnect and auto-reconnect fail safely
- UI reconnect controls remain visible but disabled for Codex

### Provider-specific settings

Commits:
- `2bb7ddc` `feat: add provider-specific model settings`
- `0d4d7ae` `feat: split settings by provider`

What changed:
- Codex-specific model and effort defaults
- cleaner provider settings model
- settings API and UI aligned to separate worker/brain provider defaults

### Brain provider correctness

Commit:
- `8bb5176` `fix: honor brain provider changes after restart`

What changed:
- stopped brain sessions now adopt updated configured provider on next start
- provider state is persisted correctly across restart

### Provider-scoped context

Commits:
- `e60a3f3` `feat: add provider scoping to context api`
- `c660ef9` `feat: add provider controls to context ui`
- `75bea95` `feat: scope brain memory by provider`
- `476602a` `feat: scope agent context helpers by provider`

What changed:
- context items gained optional provider scope
- UI can create shared, Claude-specific, and Codex-specific context
- brain wisdom injection is provider-aware
- brain and worker helper scripts read shared + matching-provider context by default

### Codex hooks investigation

Commit:
- `18db63f` `docs: add codex hooks investigation`

What changed:
- documented that public docs confirm `notify`
- local binary inspection shows `codex_hooks` as under development
- no production integration was added

### Codex heartbeat and health parity

Commit:
- `734e42e` `feat: add codex brain heartbeat and health parity`

What changed:
- Codex heartbeat loop is now app-managed rather than disabled
- Codex quick-clear became a real prepare-and-reset flow
- Codex now has provider-specific tmp-dir deploy manifests for worker and brain
- health recovery validates and regenerates Codex assets directly

## Current State

### Claude

Claude remains the most complete provider:
- local and remote sessions
- reconnect
- hook-driven lifecycle behavior
- native `/loop ... /heartbeat`
- Claude-specific settings and skills deployment

### Codex

Codex currently supports:
- local workers
- local brain
- provider-specific model and effort defaults
- mixed-provider UI
- provider-scoped context
- Codex brain quick-clear
- Codex brain heartbeat via app-managed scheduling
- provider-specific tmp-dir health recovery

Codex intentionally does not yet support:
- remote sessions
- reconnect / auto-reconnect
- full skills parity
- hook-driven lifecycle automation in product behavior

## Important Design Decisions That Are Easy To Miss

### Codex heartbeat is real, but not provider-native

Codex heartbeat is implemented in the app runtime, not via a Codex-native `/loop` equivalent.

That was chosen because:
- Claude already has a native heartbeat path
- Codex hooks are not yet integrated safely
- app-managed scheduling is safer than pretending Codex has Claude semantics

This gives Codex useful autonomous monitoring now, but it is not as deeply integrated as Claude's internal hook/loop path.

### Codex quick-clear is not a literal slash-command port

For Codex, quick-clear means:
- redeploy fresh brain assets
- refresh prompt state
- send a translated reset instruction

This is product-equivalent behavior, not command-equivalent behavior.

### Provider-specific context is additive, not isolating

Provider-scoped reads are intentionally:
- shared context
- plus matching provider context

This avoids fragmenting knowledge unnecessarily while still allowing provider-specific instructions and memory.

## Known Residual Risks

### 1. Codex heartbeat semantics

Codex heartbeat is app-scheduled, not provider-native.

Risk:
- timing and session-state behavior may differ from Claude
- future Codex hook support may warrant a redesign

### 2. Mixed-provider stale state

Several bugs were fixed around provider display and brain restarts, but stale UI or in-memory state remains a class of risk in mixed-provider usage.

### 3. Codex hooks remain unresolved

Evidence exists that Codex has an experimental hooks feature, but the runtime contract is not verified enough for product integration.

### 4. Reconnect remains asymmetric

This is intentional for now. Claude reconnect is mature; Codex reconnect is not enabled.

## What Should Be Done Next

Recommended next work:

1. tighten mixed-provider state refresh behavior so provider changes are reflected without full app restart
2. perform a dedicated Codex hooks spike against the local runtime contract
3. improve Codex lifecycle confidence around stop/start/check paths
4. defer reconnect parity until Codex runtime state is stronger

## Related Design Logs

For planning and investigation context:
- `038-provider-codex-compatibility-plan.md`
- `039-provider-codex-task-backlog.md`
- `040-provider-codex-detailed-task-packets.md`
- `041-codex-hooks-investigation.md`
