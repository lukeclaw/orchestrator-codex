# PATCH Endpoint Must Be Task-Aware for Status

**Date**: 2026-03-16
**Commit**: 6e15185
**Area**: `api/routes/sessions.py` -- `update_session` (PATCH endpoint)

## Symptom

After reconnect, worker status briefly shows "waiting" (correct, has assigned task) then flips to "idle" ~2 seconds later. The task is still assigned but the worker appears idle.

## Root Cause

The reconnect flow correctly sets `status="waiting"` via `_recovery_status()` which checks for assigned tasks. ~2 seconds later, Claude boots in the new PTY and fires a `SessionStart(source=startup)` hook, which sends `PATCH /sessions/{id}` with `status="idle"` -- unconditionally overwriting the task-aware status.

The hook runs on the remote host via bash/curl and has no way to check for assigned tasks. The PATCH endpoint accepted whatever status was sent without any server-side validation.

## Fix

Added a server-side guard in the PATCH endpoint: when `status="idle"` is requested, call `_recovery_status(db, session_id)` to check for assigned tasks. If tasks exist, promote to "waiting" instead.

This follows the same pattern already used in `health.py` and `ws_terminal.py` -- always use `_recovery_status()` when setting idle/waiting status.

## Rule

**Server-side endpoints that accept status updates must be task-aware.** Don't trust callers (hooks, CLI tools, frontend) to know about task assignments. The server is the single source of truth for task state and should enforce consistency.

Pattern: whenever setting status to "idle", call `_recovery_status()` first. If there are assigned tasks, use "waiting" instead.
