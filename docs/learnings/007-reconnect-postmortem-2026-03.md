# Post-Mortem: Reconnect Bugs (March 2026)

**Date**: 2026-03-16
**Commits**: d238d5e, 71b4d76, c17473d
**Symptom**: rdev workers disconnect and are unable to reconnect.

Extracted lessons are in [001](001-health-check-fail-closed.md) through [006](006-test-discipline.md). **This file covers the interaction between commits** — read it only when you need the full causal chain, not for individual rules.

---

## The Three Commits

| Commit | Summary | Risk | Extracted Lessons |
|--------|---------|------|-------------------|
| `c17473d` | test: add setup_path tests | None | — |
| `71b4d76` | fix: interactive CLI reconnect on PTY exit / stream drop | Medium | [003](003-socket-eof-ambiguity.md), [004](004-no-return-in-finally.md) |
| `d238d5e` | fix: legacy migration filter and auto_reconnect default | Medium | [002](002-sql-defaults-must-match-model.md), [005](005-migration-side-effects.md) |

## How the Bugs Interacted

The `auto_reconnect=False` bug (migration 025, fixed in d238d5e) was the **root cause**:

1. All sessions since migration 025 had `auto_reconnect = 0` in DB
2. Health check, WS terminal connect, and session-viewed all check `if s.auto_reconnect:` -> False -> skip
3. Workers disconnect and stay disconnected forever

Commit `71b4d76` **compounded** the problem: after false-positive `pty_exit` (tunnel death misread as PTY exit — [003](003-socket-eof-ambiguity.md)), the frontend relied on sessionStatus watch to recover, which required auto-reconnect. With `auto_reconnect=False` in DB, this was a complete dead end.

Migration 037 broke the deadlock by fixing `auto_reconnect`, but recovery was **still delayed** by the health check's separate `alive=True` default bug ([001](001-health-check-fail-closed.md)) — exception/timeout handlers classified disconnected workers as alive, excluding them from auto-reconnect candidates.

## The Only Working Recovery Path

With all bugs active, clicking into the worker details page (`record_session_viewed`) was the only reliable reconnect trigger — it bypassed both the health check guards and the auto-reconnect flag check. This is why manual clicking "fixed" the problem while automated recovery did not.
