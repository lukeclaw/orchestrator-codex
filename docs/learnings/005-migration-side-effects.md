# Migration Side Effects

**Date**: 2026-03-16
**Related**: [007-reconnect-postmortem-2026-03.md](007-reconnect-postmortem-2026-03.md)

## The Mistake

Migration 037 fixed the `auto_reconnect` default bug with a blanket UPDATE:

```sql
UPDATE sessions SET auto_reconnect = 1 WHERE auto_reconnect = 0;
```

This set `auto_reconnect=1` for ALL sessions where it was `0`, regardless of whether the user explicitly disabled it. Users who intentionally turned off auto-reconnect for specific workers found it silently re-enabled.

## Why It Was Done

There was no way to distinguish "user explicitly set to 0" from "bug set it to 0 via wrong SQL DEFAULT." The tradeoff was accepted: fix the majority case (bug-caused 0s) at the cost of overriding the minority case (intentional 0s).

## What Should Have Been Done Differently

1. **Communicate the change**: Show a one-time notification or changelog entry so users know their preferences may have been reset
2. **Be more surgical if possible**: If there's an `updated_at` or audit log, distinguish user-modified values from default values
3. **Consider the SQL DEFAULT too**: Migration 025's `DEFAULT 0` was left in place. The INSERT was fixed to explicitly pass `1`, but any future code path that inserts without specifying `auto_reconnect` would silently get `0` again. Ideally, also fix the schema default (requires table rebuild in SQLite).

## Rule

**Data migrations that override user preferences need user communication.** Even when the tradeoff is acceptable, users should be informed so they can re-apply their intentional settings. Also: fix the root cause (the schema default), not just the symptom (the existing rows).
