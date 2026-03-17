# SQL Defaults Must Match Model Defaults

**Date**: 2026-03-16
**Related**: [007-reconnect-postmortem-2026-03.md](007-reconnect-postmortem-2026-03.md)

## The Mistake

Migration 025 added a column with `auto_reconnect BOOLEAN NOT NULL DEFAULT 0`, but the Python `Session` model had `auto_reconnect: bool = True`. The Python default is only used when constructing Session objects without the field -- when reading from DB, the SQL value `0` (False) wins.

Every session created after migration 025 had `auto_reconnect=False` in the database, silently breaking auto-reconnect for all workers.

## Why It Was Hard to Catch

- The Python model showed `auto_reconnect = True`, so inspecting the code looked correct
- The DB value only diverged when reading back from the database, not when constructing in-memory objects
- The feature appeared to work in fresh test setups (where Python defaults were used), but failed in production (where DB defaults applied)
- No test verified the round-trip: create session -> read from DB -> check `auto_reconnect`

## The Fix

1. Explicitly pass `auto_reconnect=1` in the INSERT statement (don't rely on SQL DEFAULT)
2. Migration 037: `UPDATE sessions SET auto_reconnect = 1 WHERE auto_reconnect = 0` (imprecise but necessary)

## Rule

**When adding a new column, the SQL `DEFAULT` must match the application model's default value.** Always verify this at review time. Better yet, explicitly set the value in INSERT statements rather than relying on SQL DEFAULT -- this makes the code self-documenting and avoids silent divergence.

SQLite doesn't support `ALTER COLUMN ... SET DEFAULT`, so fixing a wrong default requires a table rebuild migration. Get it right the first time.
