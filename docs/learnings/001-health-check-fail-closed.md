# Health Checks Must Fail-Closed

**Date**: 2026-03-16
**Related**: [007-reconnect-postmortem-2026-03.md](007-reconnect-postmortem-2026-03.md)

## The Mistake

The health check pipeline in `health.py` defaulted to `alive=True` when exceptions or timeouts occurred:

```python
# _check_one exception handler (line ~1090):
except Exception as e:
    return session, {"alive": True, "status": session.status, "reason": str(e)}, is_precheck

# future.result timeout handler (line ~1148):
except Exception:
    s, is_precheck = futures[future]
    result = {"alive": True, "status": s.status}
```

This meant that when a disconnected remote worker's health check timed out (common -- SSH + daemon + fallback can exceed 12s), the worker was classified as "alive" and excluded from auto-reconnect candidates. Workers stayed disconnected indefinitely.

## Why It Happened

The intent was to avoid false negatives -- don't mark a healthy worker as dead just because the health check errored. But for a *health check*, unknown status should mean "potentially unhealthy," not "assumed healthy." The design chose fail-open when it should have chosen fail-closed.

## The Deeper Problem

The health check pipeline had a redundant in-memory classification layer (`_tally_result` / `auto_reconnect_candidates`) sitting between the authoritative DB writes and the auto-reconnect loop. The health checks already wrote correct status to the DB, and the auto-reconnect loop re-read from DB. The middle layer was a gate that filtered candidates using in-memory results, and its exception handlers fed it `alive=True` -- overriding what the DB said.

## The Fix

Eliminate the middleman. After health checks complete (they update the DB directly), query the DB for sessions needing reconnect:

```python
# After health checks run and update DB status:
all_sessions = repo.list_sessions(db, session_type="worker")
for s in all_sessions:
    if s.status in ("disconnected", "error") and s.auto_reconnect:
        if is_user_active(s.id):
            continue
        if _reconnect_backoff.should_skip(s.id):
            continue
        trigger_reconnect(s, db, ...)
```

Tactical quick fix: change exception defaults to `alive=False`.

## Rule

**Health checks must fail-closed.** An unknown/error/timeout result = "unhealthy" (worker potentially dead), never "healthy" (worker alive). When in doubt, trigger reconnection -- a redundant reconnect attempt is cheap, a missed one leaves users stuck.
