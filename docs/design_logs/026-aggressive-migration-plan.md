# 026 — Aggressive Legacy Migration & Brain CLI Commands

## 1. Problem

The codebase maintains two parallel code paths for remote workers:
- **Legacy**: tmux pane → SSH → GNU Screen → Claude PTY
- **New**: RWS daemon → PTY (ringbuffer, direct TCP)

The gradual migration strategy (wait for Claude to die, then migrate on reconnect) means:
- Both code paths must be maintained indefinitely
- Every endpoint has `if rws_pty_id: ... else: ... ` branching
- Screen detection, health check, and reconnect code (~500 lines) stays alive
- The `screen_detached` status exists solely for legacy

## 2. Aggressive Migration: Kill Screen on Startup

### Approach

On server restart, immediately migrate all legacy remote sessions:

```
App startup (lifespan)
  → find all remote sessions WHERE rws_pty_id IS NULL
  → for each: SSH kill screen session (best-effort)
  → set status = "disconnected"
  → auto-reconnect triggers → new RWS PTY path
```

Claude conversations are preserved — `reconnect_remote_worker` does `--resume` using the session's `claude_session_id`. The user loses the current in-progress response (if any), but Claude picks up where it left off.

### Implementation

Add to `orchestrator/api/app.py` lifespan, after `recover_tunnels()`:

```python
# Migrate legacy remote sessions to RWS PTY architecture
try:
    _migrate_legacy_remote_sessions(conn)
except Exception:
    logger.exception("Legacy migration failed (non-fatal)")
```

New function in `orchestrator/core/lifecycle.py`:

```python
def _migrate_legacy_remote_sessions(conn):
    """Kill screen sessions for legacy remote workers, triggering RWS reconnect."""
    from orchestrator.state.repositories import sessions as repo
    from orchestrator.terminal.ssh import is_remote_host

    sessions = repo.list_sessions(conn)
    legacy = [s for s in sessions if is_remote_host(s.host) and not s.rws_pty_id
              and s.status not in ("idle", "disconnected")]

    if not legacy:
        return

    logger.info("Migrating %d legacy remote sessions to RWS PTY", len(legacy))

    for s in legacy:
        # Kill screen on remote (best-effort, non-blocking)
        try:
            screen_name = get_screen_session_name(s.id)
            subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", s.host,
                 f"screen -ls | grep -w '{screen_name}' | awk '{{print $1}}' | "
                 f"while read sid; do screen -X -S \"$sid\" quit; done"],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass

        # Mark disconnected to trigger auto-reconnect
        repo.update_session(conn, s.id, status="disconnected")
        logger.info("Migrated %s: killed screen, set disconnected", s.name)
```

### Code Deletion After Migration Ships

Once all remote workers have been migrated (give it one server restart cycle), delete:

| File/Function | What | Lines (approx) |
|---|---|---|
| `health.py: check_screen_and_claude_remote()` | Screen detection via SSH | ~180 |
| `health.py: get_screen_session_name()` | Screen name helper | ~10 |
| `health.py: check_screen_and_claude_rdev` | Alias | ~1 |
| `reconnect.py` | Screen kill in `reconnect_remote_worker()` | ~20 |
| `session/__init__.py` | Screen-related exports | ~5 |
| `session/state_machine.py` | `SCREEN_DETACHED` state | ~1 |
| `sessions.py` | `screen_detached` references | ~5 |
| `terminal/session.py: _get_screen_session_name()` | Duplicate helper | ~5 |
| **Total** | | **~230 lines** |

The `screen_detached` status can be removed from the state machine — all remote sessions use only: `connecting → working → waiting → disconnected → idle`.

## 3. Brain CLI Commands

### Current State

The brain interacts with workers through two mechanisms:
1. **Terminal preview capture**: Brain sync builds a prompt with `capture_pty()` / `capture_output()` for each worker
2. **Actions via curl**: Brain is told to run `curl -X POST .../sessions/{id}/stop` etc.

The brain runs as a Claude Code process in a local tmux pane. It executes curl commands to the API, which then routes to tmux (local) or RWS (remote).

### Problem

The curl-to-API path works but is indirect:
- Brain → tmux → shell → curl → API → RWS → remote PTY
- Every action is a full HTTP round-trip from within a Claude subprocess
- Error handling is poor (brain sees curl's stderr, not structured errors)

### Current State

`orch-workers` already exists at `agents/brain/bin/orch-workers` (bash) with: `list`, `show`, `create`, `delete`, `stop`, `reconnect`. All are thin curl wrappers around the API.

Additionally `orch-send` exists for sending messages to workers.

The brain prompt (`agents/brain/prompt.md`) tells brain to read worker terminals via:
```bash
tmux capture-pane -p -t orchestrator:<worker-name> -S -50
```

This **only works for local workers**. Remote workers need the API preview endpoint.

### Missing Commands

| Command | Purpose | API endpoint |
|---------|---------|-------------|
| `orch-workers preview <name>` | Read worker terminal (local & remote) | `GET /sessions/{id}/preview` |
| `orch-workers pause <name>` | Pause worker (send Escape) | `POST /sessions/{id}/pause` |
| `orch-workers continue <name>` | Resume paused worker | `POST /sessions/{id}/continue` |
| `orch-workers prepare <name>` | Clear and prepare for new task | `POST /sessions/{id}/prepare-for-task` |
| `orch-workers health <name>` | Run health check | `POST /sessions/{id}/health-check` |

### Brain Prompt Updates

Replace the `tmux capture-pane` pattern with:
```bash
# Read worker terminal (works for both local and remote)
orch-workers preview <worker-name>
```

The brain sync prompt in `brain.py` already uses the API to capture previews. But the brain's *own* ad-hoc terminal reading (when it decides to check a worker) should use `orch-workers preview` instead of `tmux capture-pane`.

### Benefits

- **One interface** for brain to interact with all workers (local and remote)
- Brain prompt is simpler (no tmux knowledge needed)
- Error handling is consistent
- Same commands available to human users

**Not in scope**: Making the brain itself run via RWS PTY. The brain is always local.

## 4. Execution Order

1. **Implement aggressive migration** (startup kill-screen + reconnect)
2. **Ship and verify** — one restart cycle confirms all remotes migrate
3. **Delete legacy screen code** — remove ~230 lines
4. **Extend `orch-workers` CLI** — add preview/send/control subcommands
5. **Update brain sync prompt** — use CLI commands instead of curl
