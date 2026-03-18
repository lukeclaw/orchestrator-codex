# Development Learnings

Lessons learned during development -- mistakes made (often by Claude Code), root causes, fixes, and rules to follow going forward. Each file captures a specific topic. Reference these when working on related areas.

## Index

| # | File | Topic | Key Rule |
|---|------|-------|----------|
| 1 | [001-health-check-fail-closed.md](001-health-check-fail-closed.md) | Health checks must fail-closed | Exception/timeout = unhealthy, never default to `alive=True` |
| 2 | [002-sql-defaults-must-match-model.md](002-sql-defaults-must-match-model.md) | SQL DEFAULT vs Python model defaults | Always verify SQL column DEFAULT matches the dataclass/model default |
| 3 | [003-socket-eof-ambiguity.md](003-socket-eof-ambiguity.md) | Socket EOF is ambiguous | `recv()` returning empty doesn't mean the remote process died |
| 4 | [004-no-return-in-finally.md](004-no-return-in-finally.md) | Never `return` inside `finally` | A `return` in `finally` silently swallows in-flight exceptions |
| 5 | [005-migration-side-effects.md](005-migration-side-effects.md) | Migration side effects | Data migrations that override user preferences need communication |
| 6 | [006-test-discipline.md](006-test-discipline.md) | Test assertion discipline | Don't weaken assertions to fix flaky tests; fix the root cause |
| 7 | [007-reconnect-postmortem-2026-03.md](007-reconnect-postmortem-2026-03.md) | Reconnect post-mortem (March 2026) | Full analysis of commits d238d5e, 71b4d76, c17473d |
| 8 | [008-never-kill-daemon-on-transient-failure.md](008-never-kill-daemon-on-transient-failure.md) | Never kill daemon on transient failure | Don't destroy remote resources to recover from local connection failures |
| 9 | [009-tunnel-monitor-must-update-status.md](009-tunnel-monitor-must-update-status.md) | Tunnel monitor must update status | The component that detects a failure should propagate it |
| 10 | [010-patch-endpoint-must-be-task-aware.md](010-patch-endpoint-must-be-task-aware.md) | PATCH endpoint must be task-aware | Always call `_recovery_status()` when setting status to "idle" |
| 11 | [011-daemon-path-non-interactive-ssh.md](011-daemon-path-non-interactive-ssh.md) | Daemon PATH in non-interactive SSH | Never assume binaries are on PATH in daemon launched via SSH |
| 12 | [012-pyinstaller-text-read-files.md](012-pyinstaller-text-read-files.md) | PyInstaller misses text-read files | Files loaded via `read_text()`/`open()` must be added to `datas` in the spec |

## Origin

These learnings were extracted from a post-mortem analysis of reconnect bugs in March 2026. The full post-mortem with commit-level detail is preserved in [007-reconnect-postmortem-2026-03.md](007-reconnect-postmortem-2026-03.md).
