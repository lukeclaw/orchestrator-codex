# Work Verification Improvement Plan

## Problem

The brain's current verification of completed work is shallow:

1. **PR tasks**: Brain checks if the PR is merged. That's it. It doesn't verify test results, CI check details, review comment resolution, or changed file relevance.
2. **Non-PR tasks**: Brain falls back to "verify the deliverable exists" — effectively manual. Docs, config changes, investigations, and research tasks have no automated verification.
3. **No evidence collection**: Workers claim "Task complete" in natural language. The brain has no structured way to verify what was actually done, what tests were run, or what the results were.
4. **No quality gate**: The brain can mark tasks done even if CI is failing, tests weren't run, or review comments weren't addressed — as long as the PR is merged.

**What OpenClaw teaches us**: Don't trust agent claims. Require evidence. Use runtime-derived status signals (not model output). Run lint/build/test gates before declaring completion. Use structured claim verification matrices.

---

## Current State

### What the brain checks today

```
Worker says "done"
  → Brain reads terminal, detects completion signal
  → orch-prs --repo org/repo <numbers>
  → action == "merged"? → Mark done + stop
  → action == "ready_to_merge"? → Tell worker to merge
  → action == "ci_failing"? → Tell worker to fix
  → No PR? → "Needs human" or check if file exists
```

### What data is already available but unused

The `GET /api/prs/detail` endpoint (pr_preview.py) already returns:
- **Individual CI check runs**: name, status, conclusion (not just the rollup)
- **Changed files**: filename, additions, deletions
- **Review threads**: per-reviewer comments with resolution state
- **Review decisions**: per-reviewer approve/request-changes

The brain only uses the `orch-prs` rollup (merged/ci_failing/review_pending). It never looks at the detail.

---

## Improvement Plan

### Level 1: Richer PR Verification (prompt + skill changes only)

Enhance the `/check_worker` and `/heartbeat` verification procedure to use data the brain already has access to.

**Before marking a PR task done, the brain should verify:**

| Check | How | Tool |
|-------|-----|------|
| PR is merged | `orch-prs` action field | Already done |
| CI checks all passed | `gh pr checks <number> --repo org/repo` | gh CLI (already allowed) |
| No unresolved review threads | `gh pr view <number> --json reviewDecision,reviews` | gh CLI |
| Changed files are relevant to the task | `gh pr view <number> --json files` then compare to task description | gh CLI + brain judgment |
| Tests were run locally (worker evidence) | Check task notes/subtask notes for test output | `orch-tasks show` |

**Implementation**: Update the "Slow Path" verification in `check_worker.md` and the completion handling in `heartbeat.md` to run these additional checks before declaring done.

New verification procedure:

```markdown
### Verifying completion (enhanced)

When a worker claims completion and orch-prs shows PR merged:

1. **Check CI details**: `gh pr checks <number> --repo org/repo`
   - All checks should be passing (not just rollup)
   - If any required check failed, flag it

2. **Check review resolution**: `gh pr view <number> --json reviews,reviewThreads`
   - reviewDecision should be APPROVED
   - No unresolved review threads

3. **Check changed files match task scope**:
   `gh pr view <number> --json files --jq '.files[].path'`
   - Skim the file list — do the changes make sense for this task?
   - Flag if changes are suspiciously narrow (only docs) or wide (50+ files)

4. **Check for worker's own verification evidence**:
   - Look in task notes for test output, build logs, or verification steps
   - If the worker didn't record any evidence, note it but don't block

5. **If all checks pass**: Mark done + stop + notify with verification summary
6. **If any check fails**: Notify user with specifics instead of auto-marking done
```

**Effort**: Half a day. Prompt/skill changes only, no backend work.

### Level 2: Worker Completion Protocol + Evidence Nudges

Teach workers to provide structured evidence, and have the brain remind them when it's missing.

**Worker-side** — new "Signaling Completion" step in the worker prompt workflow:

1. Run tests and linter
2. **Include evidence on the PR itself** (not just task notes):
   - **API changes**: QEI/qprod test results in PR description
   - **Frontend/UI changes**: Screenshots or screen recordings in PR description
   - **All PRs**: Brief description of what changed and how it was tested
3. Record structured verification in task notes (`## Verification` section)
4. Then say "Task complete"

**Brain-side** — evidence nudges catch missing evidence BEFORE the PR is merged:

When the brain (via `/check_worker` or `/heartbeat`) sees an open PR, it checks the PR body and changed files:
- API changes without test results → nudge worker to add QEI/qprod evidence
- Frontend changes without screenshots → nudge worker to add visual evidence
- Change type is heuristically detected from file paths (`.py` in `api/routes/` = API, `.tsx` in `components/` = UI)

This catches the common failure mode where workers forget evidence until it's too late.

**Effort**: Half a day. Prompt changes to worker + brain skills.

### Level 3: Deep PR Review via Claude Code's `/review`

For high-value tasks, the brain uses Claude Code's built-in `/review` command for a deeper review before marking done. No custom sub-agent skill needed — `/review` already reads diffs, checks for issues, and reports findings.

**When to trigger**: High-priority tasks, large PRs (>500 lines changed), or when the brain is uncertain about completion quality.

**How the brain uses it**:
```
/review
Context: The worker's task was "<task description>".
Check that the PR changes address this task and include proper test evidence.
```

**Brain then decides**: auto-mark done (if `/review` approves) or notify user (if `/review` flags concerns).

**Effort**: Zero new code — just a prompt addition telling the brain when and how to use `/review`.

### Level 4: Verification Memory (learning from past verification)

The brain records verification outcomes in its memory logs:
- "PR #234 in voyager-web was marked done but had a failing optional CI check — user approved anyway"
- "Worker on espresso tasks consistently forgets to run local tests — always check CI carefully"
- "Tasks in ml-pipeline repo never have tests — always need manual verification"

Over time, the brain learns which repos/workers/task-types need more scrutiny and adjusts its verification depth accordingly.

**Effort**: Zero — this happens naturally through the existing `orch-memory log` system and the heartbeat's "record notable patterns" step. Just needs a prompt nudge to record verification outcomes.

---

## What We're NOT Doing

- **Running tests from the brain**: The brain is an orchestrator, not a developer. It doesn't have access to the worker's repo/environment. Workers run their own tests.
- **Blocking merges**: The brain doesn't have GitHub admin access to enforce merge gates. It verifies after the fact and reports.
- **Formal approval workflows**: No Lobster-style resumable pipelines. The brain's verification is advisory — it recommends "mark done" or "needs review" but the human makes the final call (except in heartbeat autonomous mode where the brain acts on high-confidence verdicts).
- **Custom CI integration**: We use GitHub's existing CI status via `gh` CLI. No custom CI runners or webhook listeners.

---

## Implementation

All levels implemented via prompt/skill changes only. No backend, frontend, or test changes needed.

**Files modified:**
- `agents/worker/prompt.md` — completion protocol with evidence requirements (Level 2)
- `agents/brain/skills/check_worker.md` — enhanced Slow Path with verification checklist + evidence nudges (Level 1 + 2)
- `agents/brain/skills/heartbeat.md` — enhanced completion verification + evidence nudges during scan (Level 1 + 2)
- `agents/brain/prompt.md` — verification memory nudge + `/review` in skills list (Level 3 + 4)
- `agents/brain/skills/unblock.md` — record verification outcomes (Level 4)

---

## Key Insight from OpenClaw

OpenClaw's principle: **status comes from runtime signals, not model claims**. For us, "runtime signals" means:
- GitHub PR state (merged, CI status, review decision) — already available
- CI check details (individual check names and conclusions) — available via `gh pr checks`
- Changed files list — available via `gh pr view --json files`
- Task notes with structured verification sections — available via `orch-tasks show`

The brain should treat worker "Task complete" as a *trigger to verify*, not as *evidence of completion*. It already does this for PR merges. The improvement is extending that rigor to CI details, review threads, file relevance, and non-PR tasks.
