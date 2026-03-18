# Development Learnings

Lessons learned during development — mistakes made, root causes, fixes, and rules to follow going forward. **Consult these before working on related areas.**

---

## Quick Reference (all rules)

Scan this list first. Follow the link for full context only when working in that area.

| Rule | File |
|------|------|
| Health check exception/timeout = unhealthy. Never default to `alive=True`. | [001](001-health-check-fail-closed.md) |
| SQL column `DEFAULT` must match the Python model default. Prefer explicit INSERT values. | [002](002-sql-defaults-must-match-model.md) |
| `recv()` returning empty means "socket closed," not "process died." Confirm via out-of-band channel. | [003](003-socket-eof-ambiguity.md) |
| Never `return` inside `finally` — it silently swallows in-flight exceptions. | [004](004-no-return-in-finally.md) |
| Data migrations that override user preferences need user communication. Fix the schema default too. | [005](005-migration-side-effects.md) |
| Don't weaken test assertions to fix flaky tests. Fix isolation. Reconnect changes need tests. | [006](006-test-discipline.md) |
| Never destroy remote resources (daemon/PTYs) to recover from local connection failures. Retry the connection. | [008](008-never-kill-daemon-on-transient-failure.md) |
| The component that detects a failure should propagate status. Don't defer to a slower polling loop. | [009](009-tunnel-monitor-must-update-status.md) |
| Server-side PATCH must call `_recovery_status()` when setting status to "idle." Don't trust callers. | [010](010-patch-endpoint-must-be-task-aware.md) |
| Non-interactive SSH has minimal PATH. Search known locations explicitly for binaries like `npx`. | [011](011-daemon-path-non-interactive-ssh.md) |
| Files loaded via `read_text()`/`open()` must be added to `datas` in the PyInstaller spec. | [012](012-pyinstaller-text-read-files.md) |
| Never `sendall()` on a non-blocking socket with large data. VT renderers strip colors — send raw bytes. | [013](013-nonblocking-sendall-and-rendering-strips-colors.md) |
| Never skip tunnel recovery for any worker status. Orphaned SSH processes block ports permanently. | [014](014-recover-tunnels-must-include-disconnected.md) |

---

## By Category

### Connection & Reconnect Recovery

The largest cluster of learnings. Most originated from the March 2026 reconnect post-mortem ([007](007-reconnect-postmortem-2026-03.md)).

| # | File | Summary |
|---|------|---------|
| 1 | [001-health-check-fail-closed.md](001-health-check-fail-closed.md) | Health check exception handlers defaulted to `alive=True`, hiding disconnected workers from auto-reconnect. Fix: fail-closed (unknown = unhealthy), or better yet query DB directly after health checks. |
| 3 | [003-socket-eof-ambiguity.md](003-socket-eof-ambiguity.md) | `recv()` returning `b""` conflated tunnel death with PTY exit. Fix: return `StreamResult` with both `pty_exited` (ambiguous) and `confirmed_dead` (authoritative). |
| 8 | [008-never-kill-daemon-on-transient-failure.md](008-never-kill-daemon-on-transient-failure.md) | `_start_in_background` killed the remote daemon on connection failure, destroying all PTYs. Fix: retry connection only — daemon is independent of orchestrator process. |
| 9 | [009-tunnel-monitor-must-update-status.md](009-tunnel-monitor-must-update-status.md) | Tunnel monitor detected dead tunnels but didn't update session status, deferring to 5-min health check. Fix: set "disconnected" + publish event immediately on tunnel failure. |
| 10 | [010-patch-endpoint-must-be-task-aware.md](010-patch-endpoint-must-be-task-aware.md) | SessionStart hook sent `status="idle"` via PATCH, overwriting task-aware "waiting" status. Fix: server-side guard calls `_recovery_status()` before accepting "idle". |
| 14 | [014-recover-tunnels-must-include-disconnected.md](014-recover-tunnels-must-include-disconnected.md) | `recover_tunnels()` skipped disconnected workers, leaving orphaned SSH processes holding port 8093. Fix: recover tunnels for ALL remote workers regardless of status. |

**Post-mortem reference**: [007-reconnect-postmortem-2026-03.md](007-reconnect-postmortem-2026-03.md) — full commit-level analysis of the March 2026 reconnect bugs. The extracted lessons are in 001-006; read 007 only for the interaction analysis between commits.

### Database & Migrations

| # | File | Summary |
|---|------|---------|
| 2 | [002-sql-defaults-must-match-model.md](002-sql-defaults-must-match-model.md) | Migration 025 `DEFAULT 0` vs Python `True` silently broke auto-reconnect. SQLite can't ALTER DEFAULT — get it right the first time. |
| 5 | [005-migration-side-effects.md](005-migration-side-effects.md) | Migration 037 blanket-updated `auto_reconnect=1`, overriding intentional user preferences. Communicate preference resets to users. |

### Remote Daemon & SSH

| # | File | Summary |
|---|------|---------|
| 11 | [011-daemon-path-non-interactive-ssh.md](011-daemon-path-non-interactive-ssh.md) | Daemon launched via SSH has no `.bashrc` PATH. Also: don't block the daemon event loop with long-running ops; wrap blocking I/O in `asyncio.to_thread()`. |
| 13 | [013-nonblocking-sendall-and-rendering-strips-colors.md](013-nonblocking-sendall-and-rendering-strips-colors.md) | Non-blocking `sendall()` busy-loops on large data. VT renderers discard ANSI colors — send raw bytes as binary WebSocket frames for display. |

### Python & Code Patterns

| # | File | Summary |
|---|------|---------|
| 4 | [004-no-return-in-finally.md](004-no-return-in-finally.md) | `return` in `finally` suppresses in-flight exceptions silently. Always return after the try/finally block. |
| 6 | [006-test-discipline.md](006-test-discipline.md) | Don't weaken assertions (`assert_called_once_with` -> `assert_any_call`) to fix flaky tests. Reconnect/recovery changes must include failure-scenario tests. |

### Build & Packaging

| # | File | Summary |
|---|------|---------|
| 12 | [012-pyinstaller-text-read-files.md](012-pyinstaller-text-read-files.md) | Extracting code to a runtime-read file (not imported) silently drops it from PyInstaller bundle. Automated guard in `test_pyinstaller_spec.py`. |
