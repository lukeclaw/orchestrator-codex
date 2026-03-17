# Post-Mortem: Reconnect Bugs (March 2026)

**Date**: 2026-03-16
**Commits**: d238d5e, 71b4d76, c17473d
**Symptom**: rdev workers disconnect and are unable to reconnect.

This is the full post-mortem analysis. Extracted lessons are in the sibling files ([001](001-health-check-fail-closed.md) through [006](006-test-discipline.md)).

---

## Overview

Three commits were made on top of `origin/main` (`02c59cf`, version 1.3.9).

| Commit | Summary | Risk |
|--------|---------|------|
| `c17473d` | test: add setup_path tests | None (test-only) |
| `71b4d76` | fix: interactive CLI reconnect loop on PTY exit and stream drop | **Medium** -- false-positive pty_exit on tunnel death delays recovery; `return` in `finally` swallows exceptions |
| `d238d5e` | fix: legacy migration filter and auto_reconnect default | **Medium** -- migration 037 is a blunt instrument |

---

## Commit 1: `c17473d` -- test: add setup_path tests

### Problem
Regression in commit `23761ca` removed LinkedIn CLI PATH entries. No test caught it.

### Fix
Added `tests/unit/test_launcher.py` with parametrized tests for all required PATH directories (Homebrew Intel/ARM, LinkedIn CLI).

### Side Effects
None. Test-only commit.

---

## Commit 2: `71b4d76` -- fix: interactive CLI reconnect loop on PTY exit and stream drop

### Problem Being Solved
Two bugs in the interactive CLI terminal WebSocket:

1. **Infinite reconnect on "exit"**: When the user typed `exit`, `stream_remote_pty` could only confirm the PTY was dead by querying the RWS daemon. If the daemon was unreachable (tunnel died simultaneously), no `pty_exit` message was sent. The frontend retried forever via close code 4004.

2. **CLI lost on stream drop**: After `stream_remote_pty` returned, `_active_clis.pop(session_id, None)` unconditionally removed the CLI from the registry. On a stream drop (tunnel death), the PTY was still alive but the registry entry was gone. Frontend retries would fall back to `recover_cli`.

### The Fix

**Backend (`ws_terminal.py`)**:
- Changed `stream_remote_pty` return type from `None` to `bool` (True = PTY exited, False = stream dropped).
- Conditional logic: `pty_exited=True` -> pop from registry, send `pty_exit` + close(4005). `pty_exited=False` -> keep in registry, frontend retries via 4004.

**Frontend (`TerminalView.tsx`)**:
- Removed dead `'initial'` overlay type
- Simplified reconnect overlay conditions

### Side Effects

**False-positive `pty_exited` on tunnel death** (see [003-socket-eof-ambiguity.md](003-socket-eof-ambiguity.md)):
Socket RST from tunnel death produces `pty_exited=True` (false positive). The new code sends `pty_exit` + close(4005), frontend stops retrying. Recovery then depends on the health check auto-reconnect + sessionStatus watch, adding up to 5 minutes of delay.

**`return` in `finally` swallows exceptions** (see [004-no-return-in-finally.md](004-no-return-in-finally.md)):
`return pty_exited` at the end of the `finally` block silently suppresses unexpected exceptions.

**Net assessment**: The new code is a clear improvement for common cases (user typing "exit", idle timeouts, slow network drops). It introduces a delay (not a failure) for clean tunnel death where the socket receives RST.

---

## Commit 3: `d238d5e` -- fix: legacy migration filter and auto_reconnect default

### Problem Being Solved

1. **Legacy migration re-runs on every restart**: `migrate_legacy_screen_sessions` matched disconnected RWS sessions (no `rws_pty_id`, status is `"disconnected"`), causing unnecessary SSH attempts on every restart.

2. **auto_reconnect SQL default mismatch** (see [002-sql-defaults-must-match-model.md](002-sql-defaults-must-match-model.md)): Migration 025 `DEFAULT 0` vs Python `True` silently broke auto-reconnect for all sessions.

### The Fix

- Legacy migration filter: added `"disconnected"` to exclusion list
- INSERT: explicitly pass `auto_reconnect=1`
- Migration 037: `UPDATE sessions SET auto_reconnect = 1 WHERE auto_reconnect = 0`

### Side Effects

Migration 037 overrides intentional user preferences (see [005-migration-side-effects.md](005-migration-side-effects.md)). SQL column DEFAULT was not changed (see 002).

---

## Interaction Between the Commits

The `auto_reconnect=False` bug (migration 025) was the **root cause** of "workers stay disconnected":

1. All sessions since migration 025 had `auto_reconnect = 0` in DB
2. Health check, WS terminal connect, and session viewed all check `if s.auto_reconnect:` -> False -> skip
3. Workers disconnect and stay disconnected forever

Commit `71b4d76`'s interactive CLI change compounded the problem: after false-positive `pty_exit`, the frontend relied on the sessionStatus watch to recover, which required auto-reconnect to trigger. With `auto_reconnect=False`, this created a complete dead end.

Migration 037 broke the deadlock by fixing `auto_reconnect`, but recovery was still delayed by the health check interval.

---

## Critical Finding: The Health Check `alive=True` Default

See [001-health-check-fail-closed.md](001-health-check-fail-closed.md) for the full analysis.

Even with `auto_reconnect=True` (after migration 037), the health check failed to auto-reconnect due to the separate `alive=True` default bug. Exception/timeout handlers in the health check pipeline defaulted to `alive=True`, causing disconnected workers to be classified as alive and excluded from auto-reconnect candidates.

The only reliable reconnect path was `record_session_viewed` (clicking into the worker details page), which bypassed all health check guards.
