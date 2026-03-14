# Session Identity Management: Design Analysis

## 1. What Is This System?

The Claude Orchestrator manages N parallel Claude Code workers from a single web
dashboard. Each worker is a Claude Code process running either locally or on a
remote rdev VM, communicating back to the orchestrator via hooks and CLI scripts.

### Session identity serves four purposes

| Purpose | How session_id is used | Where |
|---------|----------------------|-------|
| **API routing** | Hooks and CLI scripts send status updates to `PATCH /api/sessions/{id}` | `update-status.sh`, `lib.sh`, all `orch-*` scripts |
| **Process identification** | Health checks grep for session_id in `ps aux` output | `health.py`, `reconnect.py` |
| **Conversation persistence** | Claude CLI gets `--session-id {id}` or `-r {id}` to target a specific conversation | `terminal/session.py`, `reconnect.py` |
| **Screen session naming** | GNU Screen sessions are named `claude-{id}` for rdev workers | `health.py`, `terminal/session.py` |

### How session_id flows through the system

```
                        ┌──────────────────────────────────────┐
                        │  Orchestrator DB (session.id = UUID) │
                        └──────────┬───────────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                     │
    ┌─────────▼─────────┐  ┌──────▼──────┐  ┌──────────▼──────────┐
    │  Deployed files    │  │  Claude CLI  │  │  Screen / health    │
    │  (baked at setup)  │  │  (at launch) │  │  (at setup/check)   │
    │                    │  │              │  │                      │
    │  lib.sh:           │  │  --session-id│  │  screen -S           │
    │   ORCH_SESSION_ID  │  │  or -r {id}  │  │   claude-{id}       │
    │                    │  │              │  │                      │
    │  update-status.sh: │  │  Becomes     │  │  ps aux | grep {id}  │
    │   SESSION_ID       │  │  Claude's    │  │                      │
    │                    │  │  internal ID │  │                      │
    │  prompt.md:        │  │              │  │                      │
    │   Session ID: {id} │  │              │  │                      │
    └───────────────────-┘  └──────────────┘  └──────────────────────┘
         SURVIVES /clear      BREAKS on /clear    SURVIVES /clear
```

## 2. The Core Problem

When a user (or Claude itself) runs `/clear` inside a Claude Code session:

1. Claude Code ends the current conversation and starts a new one
2. A **new internal session ID** is generated (a different UUID)
3. The Claude **process stays running** — same PID, same command-line args
4. The `SessionStart` hook fires with `source: "clear"` and the **new** session_id

### What still works after /clear

- **Hook reporting**: `update-status.sh` uses the baked-in `{{SESSION_ID}}` (orchestrator's
  ID), not Claude's internal ID. Status updates continue routing correctly.
- **CLI tools**: `lib.sh` exports `ORCH_SESSION_ID` baked at deploy time. All
  `orch-task`, `orch-notify`, etc. continue working.
- **Screen session**: Named `claude-{orch_id}`, unchanged.
- **Process detection**: `ps aux | grep {orch_id}` still matches the original
  launch command in the process list.

### What breaks after /clear

- **Reconnect**: If Claude crashes after a `/clear`, the orchestrator tries
  `claude -r {orch_id}` which resumes the **old** conversation (pre-clear),
  not the current one. Or if the old session file was cleaned up, it falls back
  to `--session-id {orch_id}` which creates a blank session with the old ID
  instead of continuing the recent work.
- **Session file checks**: `_check_claude_session_exists_remote()` looks for
  `~/.claude/projects/*/{orch_id}.jsonl`. After `/clear`, the active conversation
  is under a different filename. The check may find the old (stale) file and
  incorrectly attempt `-r` to resume it.

### Severity assessment

The breakage is **narrow but real**: it only manifests when a worker crashes or
disconnects AFTER a `/clear` and the orchestrator attempts to reconnect. During
normal operation (no crashes), everything works fine because the process stays
alive and the baked-in infrastructure files are unaffected.

## 3. Constraints

| Constraint | Description |
|-----------|-------------|
| **Claude Code is a black box** | We cannot modify Claude Code's behavior. `/clear` will always generate a new session ID. |
| **Hooks are the only observation channel** | The only way to learn about Claude's internal state changes is through hook events. |
| **rdev workers: 1 per host** | Each rdev VM runs exactly one orchestrator worker. Other Claude processes are unlikely but possible (manual SSH). |
| **Local workers: N per machine** | Multiple workers share the same machine. Must distinguish between them. |
| **Screen survives SSH disconnects** | GNU Screen on rdev is the resilience layer. Reconnect must find and reattach to the right screen. |
| **Hooks are baked at deploy time** | `update-status.sh` and `lib.sh` have session_id substituted once during setup. They are static files on disk. |
| **`SessionStart` hook fires on `/clear`** | With `source: "clear"` and the **new** `session_id` in the JSON payload. This is our observation point. |
| **`SessionStart` hook fires on `/compact`** | With `source: "compact"` and the **same** `session_id` (compaction preserves session identity). |
| **`claude -c` resumes most recent conversation** | In the current working directory. Non-deterministic if multiple conversations exist. |
| **`claude -r {id}` resumes by exact ID or name** | Deterministic but requires knowing the right ID. |
| **Tunnel and task routing use orchestrator ID** | `PATCH /api/sessions/{orch_id}` is used everywhere. Changing this ID mid-session would break all deployed scripts. |

## 4. Approaches

---

### Approach A: Replace `/clear` with `/compact` (behavioral change)

**Idea**: Instead of clearing context, workers use `/compact` which summarizes the
conversation without changing the session ID. Enforce this via a `PreToolUse` or
`UserPromptSubmit` hook that blocks `/clear`.

**Implementation**:
- No code changes to the orchestrator.
- Add a hook or CLAUDE.md instruction telling workers to use `/compact` instead
  of `/clear`.
- Optionally: add a `SessionEnd` hook with matcher `clear` that warns when
  `/clear` is used.

**What it solves**: Session ID never diverges. All existing code works as-is.

| Pros | Cons |
|------|------|
| Zero code changes | `/compact` retains a summary of old context — may pollute new tasks |
| Simple to implement | Can't actually block `/clear` from Claude's own decisions (it's a slash command, not a tool call — hooks can't intercept it) |
| Preserves full determinism | Auto-compaction already handles context size; manual `/compact` may be redundant |
| | Workers switching to completely unrelated tasks genuinely need a clean slate, not a summary |

**Verdict**: Partial solution. Good for context-size management but doesn't address
the legitimate need for a clean slate between unrelated tasks.

---

### Approach B: Drop session ID tracking for rdev (the original plan)

**Idea**: Stop passing `--session-id` to Claude for rdev workers. Use `claude -c`
for reconnect. Simplify health checks to detect any Claude process. Keep
session ID tracking for local workers only.

**Implementation**: As described in the original plan document
(`cc-session-id-tracking-df6e91.md`).

| Pros | Cons |
|------|------|
| Simple — removes code rather than adding it | `claude -c` is non-deterministic: resumes "most recent" conversation in the directory, which may not be the worker's |
| Embraces the reality that session IDs diverge | Can't distinguish between orchestrator-managed Claude and a manually-started Claude on the same rdev |
| No hook changes needed | Generic `ps aux` grep may match stale processes |
| Fewer moving parts | Screen name `claude-worker` collides if stale DB entries point two sessions at the same host |
| | No migration plan for in-flight workers (screen name changes from `claude-{id}` to `claude-worker`) |
| | Asymmetric design: rdev and local paths diverge further, increasing maintenance burden |

**Verdict**: Pragmatic simplification but trades precision for simplicity. The
non-determinism risk is real if anyone else runs Claude on the rdev.

---

### Approach C: Hook-based session ID tracking (recommended)

**Idea**: Use the `SessionStart` hook to capture Claude's **actual** internal
session ID whenever it changes (on `/clear`, `/compact`, or `resume`), and
report it back to the orchestrator. The orchestrator maintains a
`claude_session_id` field that always reflects reality.

**Key insight from the hooks documentation**: Every hook event receives
`session_id` as a common input field — this is Claude's current internal session
ID. When `/clear` fires `SessionStart` with `source: "clear"`, the new session
ID is in the payload.

**Implementation**:

1. **Modify `update-status.sh`** to report Claude's session ID on SessionStart:

```bash
case "$EVENT" in
    SessionStart)
        # Extract Claude's actual session ID from hook input
        CLAUDE_SID=$(echo "$INPUT" | jq -r '.session_id // empty')
        SOURCE=$(echo "$INPUT" | jq -r '.source // empty')

        if [ "$SOURCE" = "startup" ]; then
            STATUS="idle"
        fi

        # Always report Claude's current session ID on any SessionStart
        # (startup, resume, clear, compact)
        if [ -n "$CLAUDE_SID" ]; then
            curl -s -X PATCH "$API_BASE/api/sessions/$SESSION_ID" \
                -H 'Content-Type: application/json' \
                -d "{\"claude_session_id\": \"$CLAUDE_SID\"${STATUS:+, \"status\": \"$STATUS\"}}" \
                > /dev/null 2>&1
            exit 0
        fi
        ;;
```

2. **Add `claude_session_id` field** to the Session model and DB schema.

3. **Update reconnect logic** to use `claude_session_id` for resume:
   - `claude -r {session.claude_session_id}` instead of `claude -r {session.id}`
   - Fall back to `claude -c` if `claude_session_id` is null (first launch)

4. **Keep orchestrator session ID unchanged** for all infrastructure:
   - API routing (`PATCH /api/sessions/{orch_id}`)
   - Screen naming (`claude-{orch_id}`)
   - CLI tools (`ORCH_SESSION_ID`)
   - Health check process grep (`grep {orch_id}` in ps — still matches the
     original launch command)

| Pros | Cons |
|------|------|
| **Deterministic reconnect**: always resumes the correct conversation | Adds a DB field and migration |
| **No behavioral restrictions**: workers can `/clear` freely | Small race window: if Claude crashes between `/clear` and the hook reporting back, the old `claude_session_id` is stale |
| **Minimal code changes**: only modify hook, add DB field, update reconnect | Hook delivery is fire-and-forget; if the API call fails, the ID isn't updated |
| **Infrastructure unchanged**: all baked-in scripts, screen names, and health checks continue working | |
| **Symmetric**: same approach works for both rdev and local workers | |
| **Observable**: the orchestrator knows exactly which Claude conversation is active | |
| **Forward-compatible**: if Claude Code ever changes how `/clear` works, the hook still captures the new state | |

**Risk mitigation for the race condition**: On reconnect, if
`claude -r {claude_session_id}` fails (session file not found), fall back to
`claude -c`. This covers the rare case where the ID is stale.

**Verdict**: Best balance of correctness and simplicity. Preserves deterministic
reconnect while accommodating `/clear` naturally.

---

### Approach D: Decouple identities completely + `claude -c` with safeguards

**Idea**: A hybrid of B and C. Stop passing `--session-id` to Claude entirely
(let it manage its own IDs). Use `claude -c` for all reconnects. But add
safeguards to mitigate `-c`'s non-determinism.

**Safeguards**:
- Set a unique working directory per worker (e.g., `/tmp/claude-workers/{orch_id}/`)
  so `claude -c` is scoped to that directory's conversation history.
- Use `SessionStart` hook to confirm reconnect succeeded (verify the session
  is the expected one by checking context/task alignment).

**Implementation**:
1. Each worker gets a unique `work_dir` or a unique `--add-dir` path.
2. `claude -c` in that directory resumes the most recent conversation there.
3. Since each worker has its own directory, `-c` is effectively deterministic.
4. `SessionStart` hook on `resume` verifies correct session via additional context.

| Pros | Cons |
|------|------|
| Completely decouples orchestrator from Claude's session management | Requires dedicated working directories per worker (may not match the actual repo they're working on) |
| `claude -c` scoped to unique directory is effectively deterministic | More complex directory management |
| No need to track `claude_session_id` | Can't verify which conversation was resumed without reading Claude's internal state |
| Simpler reconnect logic | If the worker's work_dir is the actual repo, other workers in the same repo would conflict |

**Verdict**: Clever but the working directory constraint is a significant
limitation. Workers typically need to operate in the actual repository, not an
artificial scoped directory.

---

### Approach E: Wrapper process as the identity anchor

**Idea**: Instead of launching `claude` directly in screen, launch a wrapper
script that:
1. Starts Claude
2. Monitors for Claude exit
3. If Claude exits cleanly (user quit), stays alive and reports status
4. If reconnect is needed, the wrapper relaunches Claude
5. The wrapper is the long-lived process that maintains identity

**Implementation**:
- `claude-wrapper.sh` runs in screen, manages Claude lifecycle
- Wrapper captures the current Claude session ID from `SessionStart` hooks
  written to a local file
- Reconnect reattaches to the wrapper's screen, tells it to relaunch Claude

| Pros | Cons |
|------|------|
| Single long-lived process per worker | Significant new complexity (process management in bash) |
| Can handle any Claude lifecycle event | Wrapper itself can fail, adding another failure mode |
| Natural place to maintain state | Hard to debug (nested process management) |
| | Over-engineered for the actual problem scope |

**Verdict**: Too complex. The problem doesn't warrant a process supervisor.

---

## 5. Comparison Matrix

| Criteria | A: /compact | B: Drop ID (original plan) | C: Hook-based tracking | D: Scoped dirs + -c | E: Wrapper |
|----------|:-----------:|:--------------------------:|:---------------------:|:-------------------:|:----------:|
| Reconnect correctness | High (ID never changes) | Low (`-c` is non-deterministic) | **High** (tracks real ID) | Medium (dir-scoped) | High |
| Allows /clear | No | Yes | **Yes** | Yes | Yes |
| Code complexity | None | Medium (remove + modify) | **Low** (add field + modify hook) | High (dir management) | Very High |
| Infrastructure changes | None | Health checks, screen names | **DB migration only** | Working dirs, launch | New scripts |
| Migration risk | None | High (breaks in-flight workers) | **Low** (additive change) | Medium | High |
| Works for local workers | Yes | No (local keeps old approach) | **Yes** (symmetric) | Complicated | Yes |
| Observable | N/A | Lost (no ID tracking) | **Yes** (knows active conversation) | Lost | Yes |
| Fragility | Low | Medium (generic grep) | **Low** (deterministic + fallback) | Medium | High |

## 6. Recommendation

**Approach C (Hook-based session ID tracking)** is recommended because:

1. **It solves the actual problem**: reconnect targets the correct conversation
   even after `/clear`.
2. **It's minimally invasive**: one new DB field, a small hook modification,
   and an update to the reconnect logic. No changes to health checks, screen
   naming, CLI tools, or API routing.
3. **It's symmetric**: same approach for rdev and local workers, reducing the
   codebase's rdev/local divergence.
4. **It preserves observability**: the orchestrator always knows which Claude
   conversation is active.
5. **It has a natural fallback**: if the tracked ID is stale, fall back to
   `claude -c`.
6. **It's forward-compatible**: works regardless of future Claude Code changes.

### Suggested implementation order

1. Add `claude_session_id` column to sessions table (nullable, migration).
2. Update `update-status.sh` to report `session_id` from hook input on all
   `SessionStart` events.
3. Add `PATCH` support for `claude_session_id` in the sessions API.
4. Update `_get_claude_session_arg()` to use `session.claude_session_id` when
   available, falling back to `session.id`.
5. Update `_check_claude_session_exists_remote/local()` to check for
   `claude_session_id` when available.
6. Fix the existing local launch bug: add `--session-id {id}` to initial local
   worker launch (the bug identified in the original plan).
7. Add tests for the new flow.

### What NOT to change

- Screen session naming: keep `claude-{orch_id}` (infrastructure identity).
- Health check grep: keep `grep {orch_id}` (matches original launch command in ps).
- Hook/CLI `SESSION_ID`: keep as orchestrator ID (API routing identity).
- API endpoint paths: keep `/sessions/{orch_id}`.
