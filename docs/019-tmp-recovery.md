# /tmp Recovery: SOT Refactor + Manifest-Based Health Check

## Problem

The orchestrator stores ephemeral configs, scripts, hooks, and skills under `/tmp/orchestrator/`. This directory can be wiped at any time (macOS cleanup, reboot, user action). When this happens while Claude is running, hooks and CLI tools silently break — the health check sees "Claude alive" and reports all-good, but the worker is crippled.

Additionally, the worker tmp dir contents were assembled in **three separate code paths** (`setup_local_worker`, `setup_remote_worker`, `_ensure_local_configs_exist`), making it easy for them to diverge.

## Solution

### Single Source of Truth (SOT) Functions

Two new functions in `orchestrator/agents/deploy.py` serve as the canonical definition of what each tmp directory should contain:

- **`deploy_worker_tmp_contents()`** — creates: bin scripts, hooks/settings, built-in skills, custom skills, prompt.md, and `.manifest.json`
- **`deploy_brain_tmp_contents()`** — creates: CLAUDE.md, hooks/settings, bin scripts, built-in skills, custom skills, and `.manifest.json`

All callers (initial launch, reconnect, health check recovery) delegate to these functions.

### Manifest-Based Health Checks

Each SOT function writes a `.manifest.json` listing all deployed files (relative paths). The health check:

1. Reads the manifest
2. Checks all listed paths exist
3. If manifest is missing or any file is absent → full regeneration via SOT

Functions:
- **`ensure_tmp_dir_health()`** — for workers (in `health.py`)
- **`ensure_brain_tmp_health()`** — for brain (in `health.py`)

### RWS check_path Command

A new `check_path` command in the Remote Worker Server allows the health loop to verify files exist on the remote host without data exfiltration risk (only returns booleans).

## Architecture

```
                    Initial Launch          Reconnect           Health Check
                    ──────────────          ─────────           ────────────
                         │                      │                    │
                         ▼                      ▼                    ▼
                    ┌─────────────────────────────────────────────────────┐
                    │        deploy_worker_tmp_contents() [SOT]          │
                    │        deploy_brain_tmp_contents()  [SOT]          │
                    └─────────────────────────────────────────────────────┘
                                          │
                                          ▼
                                   .manifest.json
                                          │
                                          ▼
                              ┌─────────────────────┐
                              │   ensure_*_health()  │
                              │  (reads manifest,    │
                              │   checks paths,      │
                              │   regenerates if      │
                              │   needed)             │
                              └─────────────────────┘
```

## Extracted Helpers

- `_get_custom_skills_from_db(conn, target)` — reads enabled custom skills from DB
- `_get_disabled_builtins_from_db(conn, target)` — reads disabled built-in skill names
- `_deploy_builtin_skills(src, dest, disabled)` — copies .md files with filtering

## Files Modified

| File | Changes |
|---|---|
| `orchestrator/agents/deploy.py` | +7 new functions (SOT, manifest, DB helpers) |
| `orchestrator/session/health.py` | +2 health check functions, wired into health loop |
| `orchestrator/session/reconnect.py` | Delegated to SOT, added conn param |
| `orchestrator/terminal/session.py` | Replaced inline deployment with SOT calls |
| `orchestrator/api/routes/brain.py` | Replaced inline deployment with SOT call |
| `orchestrator/api/routes/sessions.py` | Added brain health check to health-check-all |
| `orchestrator/terminal/remote_worker_server.py` | Added check_path handler |
| `tests/unit/test_tmp_recovery.py` | 28 tests covering SOT, manifest, health |
| `tests/unit/test_rws_check_path.py` | 5 tests for RWS check_path |
