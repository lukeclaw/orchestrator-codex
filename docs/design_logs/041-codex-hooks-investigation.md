# 041 Codex Hooks Investigation

Date: 2026-03-25

## Goal

Determine whether Codex has a hook system that can support parity with the current Claude-oriented hook flows in Orchestrator, and define what implementation would require.

This document records findings only. No Codex hook implementation is part of this change.

## Current Claude Hook Usage In Orchestrator

Claude hooks are a core part of the current orchestration behavior.

Worker hooks:
- automatic worker status transitions in `agents/worker/hooks/update-status.sh`
- command safety gating in `agents/shared/hooks/check-command.sh`

Brain hooks:
- prompt enrichment in `agents/brain/hooks/inject-focus.sh`
- pre-compaction memory flush in `agents/brain/hooks/pre-compact.sh`
- post-clear / post-compaction redeploy in `agents/brain/hooks/on-session-start.sh`

Hook registration is wired through:
- `agents/worker/settings.json`
- `agents/brain/settings.json`
- `orchestrator/agents/deploy.py`

These flows currently rely on Claude emitting lifecycle and tool events that invoke external hook commands.

## Public Documentation Findings

I checked official OpenAI sources for public Codex hook documentation.

Confirmed:
- Codex has documented `notify` support in the Codex configuration reference.
  - https://developers.openai.com/codex/config-reference
- The `openai/codex` repo documentation points back to the same config reference and includes a `Notify` section.
  - https://github.com/openai/codex/blob/main/docs/config.md

Not confirmed in public docs:
- `HookEventName`
- `.codex/hooks.json`
- `features.codex_hooks=true`
- a public schema or reference page for event hooks analogous to Claude hooks

Conclusion from public docs:
- `notify` is documented.
- broader event hooks were not publicly documented in the sources checked on 2026-03-25.

## Local Binary Findings

I inspected the locally installed Codex CLI directly.

Commands used:
- `codex features list`
- `codex --help`
- `codex -c features.codex_hooks=true --help`
- `strings /opt/homebrew/bin/codex | rg -i "HookEventName|hooks.json|codex_hooks|SessionStart|notify|hook_event_name"`

Observed:
- `codex features list` reports:
  - `codex_hooks under development false`
- normal CLI help does not expose a user-facing hooks command or documented hook configuration flow
- binary strings include:
  - `HookEventName.ts`
  - `hook_event_name`
  - `codex_hooks`
- local config at `~/.codex/config.toml` does not currently use hooks
- no local `hooks.json` example or schema was identified during the quick inspection

Conclusion from local inspection:
- Codex hooks appear to exist as an experimental or under-development capability in the installed binary.
- The exact contract is not yet verified.

## What This Means For Orchestrator

We should no longer assume "Codex has no hooks."

The more accurate statement is:
- Codex appears to have experimental hook support.
- Public documentation is incomplete or not yet published.
- We do not yet know whether the event model is rich enough to replace the current Claude hook flows.

Because of that, Codex hooks should be treated as:
- possible
- promising
- not production-safe until verified against the real runtime contract

## What Would Need To Be Verified Before Implementation

We need a minimal local spike that captures the actual payloads and behavior.

Verification questions:
- how hooks are configured exactly
- whether config lives in `config.toml`, `hooks.json`, or both
- whether `features.codex_hooks=true` is required
- which events are actually emitted
- whether event names include `SessionStart`, `Stop`, and `notify`
- whether payloads contain structured fields comparable to Claude:
  - event name
  - session/thread identifier
  - tool name
  - tool input
  - notification type
  - source / startup cause
- whether hook output can influence runtime behavior or is observational only
- whether a `PreToolUse`-style interception mechanism exists

## Expected Parity Outlook

Likely feasible if Codex hooks are real and stable:
- brain session-start redeploy behavior
- stop/notification-driven status updates
- lightweight prompt enrichment on supported events
- notify-based orchestration callbacks

Potentially not feasible, or at least not 1:1:
- full Claude-style `PreToolUse` safety gating
- exact replacement for Claude `UserPromptSubmit`
- exact replacement for Claude `PreCompact`
- full worker status automation if Codex emits fewer lifecycle events

## Recommended Future Implementation Approach

If we pursue this later, the safest rollout is:

1. Build a tiny local probe hook that logs every Codex hook payload to a temp file.
2. Verify the real event contract manually with a short interactive session.
3. Add a Codex hook capability flag to the provider registry only after the contract is confirmed.
4. Implement the lowest-risk hooks first:
   - brain session-start refresh
   - notify integration
   - stop/status handling
5. Keep Claude hooks as the reference path and do not rewrite them.
6. Gate Codex hooks behind an explicit experimental capability until they are stable.

## Current Decision

Do not implement Codex hooks yet.

Reason:
- evidence is strong enough to justify future work
- evidence is not strong enough to safely wire production behavior today

The correct next step, if prioritized later, is a focused runtime spike rather than direct integration work.
