# 037: Brain Learning from User Corrections

## Problem

The brain's heartbeat autonomy takes actions on workers (sending fixes, marking tasks done, nudging). When the brain gets something wrong, the user discovers it hours later and follows up with the worker. The brain never learns from these corrections — it repeats the same mistakes.

## Key Insight

Workers already capture the ground truth. Workers write task notes (progress, findings, what fixed the issue) and project context (reusable learnings). The brain should read these to learn outcomes — not parse noisy terminal scrollback.

## Design

### Title prefix convention on existing learning logs

Use `orch-memory log` with title prefixes to track brain actions and corrections:

| Prefix | Meaning | Lifecycle |
|--------|---------|-----------|
| `action: <worker> — ...` | Brain took an autonomous action | Deleted after review (effective) or converted to correction |
| `correction: <context> — ...` | Lesson from a wrong/ineffective action | Curated into wisdom over time, then deleted |
| (no prefix) | Regular learning log | Permanent |

No new DB categories, CLI commands, or tables. Just conventions on existing tools.

### How the brain learns (integrated into heartbeat)

1. **Log actions**: After every significant autonomous action (fix sent, task marked done, worker stopped), the brain logs it with an `action:` title prefix.

2. **Review outcomes inline**: During the per-worker scan on subsequent heartbeats, the brain checks for pending `action:` notes for that worker. It reads the worker's task notes to compare its suggestion against what actually happened:
   - Worker followed the suggestion → effective, delete the action note
   - Worker took a different approach → correction, record the lesson
   - No activity yet → skip, check next cycle

3. **Search before acting**: Before taking any new action, the brain searches for `correction:` logs on similar situations and factors them in.

4. **Self-reflection**: When the brain has accumulated correction logs, it curates patterns into its wisdom document and notifies the user.

### Learning signals (ordered by reliability)

| Signal | Source |
|--------|--------|
| Worker's task notes & project context | `orch-tasks show`, `orch-ctx list --scope project` |
| Task status changes (re-opened, restarted) | `orch-tasks show`, `orch-workers list` |
| Terminal observation (fallback) | `orch-workers preview` |

### Worker documentation guidance

Workers are instructed to document what worked and what didn't in task notes — especially when the actual fix diverged from a suggestion they received. This gives the brain clear ground truth to compare against.

## Files Changed

- `agents/brain/skills/heartbeat.md` — action logging, inline outcome review, self-reflection
- `agents/brain/prompt.md` — correction awareness in operational memory section
- `agents/worker/prompt.md` — guidance on documenting root causes and fixes

## Risks

- **Empty task notes**: Worker didn't document the fix. Brain leaves the action note for next cycle; terminal is the fallback. Stale notes get cleaned up during self-reflection.
- **Complementary vs. correction**: User extends (not contradicts) the brain's suggestion. Skill guidance distinguishes the two.
- **Over-generalization**: Brain learns "never do X" from one context. Corrections must be recorded with full situational context (repo, error, worker state).
- **Post-compaction**: Learning logs are in the DB, not the context window. Outcome review works correctly after compaction.
