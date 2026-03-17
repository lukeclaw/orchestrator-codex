# Test Assertion Discipline

**Date**: 2026-03-16
**Related**: [007-reconnect-postmortem-2026-03.md](007-reconnect-postmortem-2026-03.md)

## Mistake 1: Weakening Assertions to Fix Flaky Tests

`assert_called_once_with` was changed to `assert_any_call` to fix a "flaky" test. `assert_any_call` passes if the function was called with those arguments *at any point*, even if it was also called with other arguments or called extra times. This can mask real bugs where a function is called more times than expected.

**Rule**: If a test is flaky because of background thread interference, fix the test isolation (proper mocking, thread synchronization), not the assertion strength. Weaker assertions hide bugs.

## Mistake 2: Relaxing Consistency Checks Without Root Cause Analysis

A cursor consistency test was relaxed from "all 3 agree" to "2 of 3 agree" because shell prompt updates were shifting the cursor. The root cause (prompt timing) was acknowledged but not addressed.

**Rule**: If a consistency check needs relaxing, document the root cause and add a follow-up task to fix it. Don't leave weakened assertions as permanent fixtures.

## Mistake 3: Missing Tests for Critical Reconnect Paths

The reconnect commits modified critical paths but included no integration tests:
- No test for "tunnel dies -> interactive CLI behavior"
- No test for "ws_interactive_cli sends pty_exit when pty_exited=True but confirmed_dead=False"
- No test for the auto-reconnect watch recovering after false pty_exit

These are the highest-risk paths in the application.

**Rule**: Changes to reconnect/recovery logic must include tests that exercise the failure scenario being fixed. At minimum, unit tests for the new conditional branches. The reconnect system is complex enough that untested changes will introduce regressions.
