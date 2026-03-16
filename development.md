# Development Guide

Practices, conventions, and security requirements for developing on the Orchestrator codebase.

For architecture and code map, see [architecture.md](architecture.md). For feature descriptions, see [features.md](features.md).

---

## 1. Security Model

### Why this app requires extra vigilance

The Orchestrator is not a simple localhost tool. It introduces attack surface that does not exist when running CLI tools directly in a terminal:

- **Network-exposed API.** An HTTP/WebSocket server on `127.0.0.1:8093` is reachable by any process on the machine — browser tabs, VS Code extensions, npm packages, and malware. A malicious website can `fetch("http://localhost:8093/api/sessions")` to enumerate sessions or inject commands. Localhost service attacks are a [well-documented class of browser-based exploits](https://portswigger.net/research/cracking-the-lens-targeting-https-hidden-attack-surface).

- **Shell commands from HTTP parameters.** When the API receives session names, file paths, or remote directories and passes them to `subprocess.run()` or `tmux.send_keys()`, unquoted values become shell injection vectors. A crafted session name like `; rm -rf /` could execute arbitrary commands.

- **Multiplied blast radius.** A single vulnerability exposes all concurrent sessions simultaneously. The SQLite database containing session metadata, task history, and configuration is also readable by any local process.

- **Remote host propagation.** The app manages SSH tunnels and executes commands on remote hosts. A shell injection in an SSH command could propagate to remote infrastructure — turning a local vulnerability into remote code execution.

- **Weakened permission model.** All Claude Code instances run with `--dangerously-skip-permissions`, disabling built-in safety prompts. The human-in-the-loop safeguard that exists in direct terminal usage is absent.

- **Web-context attack classes.** The web UI introduces CORS misconfiguration, XSS via rendered content, and CSP bypasses — none of which exist for a terminal application.

### Security rules for contributors

Every change should be evaluated against these rules:

1. **Quote all shell interpolations.** Use `shlex.quote()` on every user-controlled or externally-sourced value that appears in a shell command string. This includes `subprocess.run()` calls, `tmux.send_keys()` commands, and SSH remote commands. Even values that "should" be safe (UUIDs, known-format IDs) must be quoted as defense-in-depth.

2. **Validate at the API boundary.** User-provided names, paths, and identifiers must be sanitized when they enter the system — not downstream. Use allowlist regex patterns, enforce length limits, and strip dangerous characters. See `_sanitize_worker_name()` in `api/routes/sessions.py` for the canonical pattern.

3. **Use `urlparse()` for URL validation.** Never validate URLs with string prefix checks (`url.startswith("http")`). Always parse with `urllib.parse.urlparse()` and validate scheme + netloc. This prevents `javascript:`, `file://`, `data:`, and other dangerous scheme injection.

4. **Keep CORS origins locked.** The `allow_origins` list in `api/app.py` is restricted to specific localhost and Tauri origins. Do not widen to `"*"`. This is a deliberate security boundary that blocks cross-origin requests from malicious browser tabs.

5. **Maintain the CSP.** The Tauri Content Security Policy in `src-tauri/tauri.conf.json` does not include `unsafe-eval`. Do not add it. No frontend code should use `eval()`, `new Function()`, or other dynamic code execution.

6. **Store secrets in the OS keychain.** Backup encryption passwords use macOS Keychain (via the `security` CLI) with a SQLite DB fallback for non-macOS or CI environments. Any new secret storage must follow this same pattern — never store passwords or tokens in plaintext in the database.

7. **Use `re.escape()` for dynamic regex patterns.** When user-controlled values are interpolated into regular expressions (e.g., `grep -E` patterns), escape them with `re.escape()` to prevent regex injection.

### Security test coverage

Security-related tests live in `tests/unit/test_security.py`. When adding new input handling, URL processing, shell command construction, or credential management, add corresponding test cases covering:
- Normal/happy-path input
- Shell metacharacter injection attempts
- Boundary values (empty string, max length, special characters)
- Scheme injection for URLs
- Credential storage and retrieval round-trips

---

## 2. Development Setup

### Prerequisites

- Python 3.13+ (managed via `uv`)
- Node.js 24+ (managed via `volta`)
- tmux (installed via Homebrew or system package manager)

### Running locally

```bash
# Backend
uv run uvicorn orchestrator.api.app:create_app --factory --host 127.0.0.1 --port 8093

# Frontend (separate terminal)
cd frontend && npm run dev
```

The backend runs at `http://localhost:8093`, the frontend dev server at `http://localhost:5173`.

---

## 3. Code Quality

### Linting and formatting

```bash
uv run ruff check . --fix && uv run ruff format .
```

Run before every commit. The ruff configuration in `pyproject.toml` enforces consistent style.

### Type checking

```bash
cd frontend && npx tsc --noEmit
```

Run when frontend files are modified.

### Testing

```bash
uv run pytest                    # full suite (must complete in <30s)
uv run pytest tests/unit/test_security.py  # security tests only
uv run pytest <test_file> -v     # specific file with verbose output
```

Tests must:
- Run fast — mock external dependencies (subprocess, network, file I/O)
- Cover happy path, error cases, and edge cases
- Include security-relevant test cases for any input handling or command construction

### Pre-commit checklist

1. `uv run ruff check . --fix && uv run ruff format .`
2. `cd frontend && npx tsc --noEmit` (if frontend files changed)
3. `uv run pytest`
4. Review your own diff for security issues: unquoted shell interpolations, unsanitized input, credential exposure

---

## 4. Code Review Guidelines

When reviewing changes, pay special attention to:

### Security (highest priority)

- [ ] All user-controlled values in shell commands are `shlex.quote()`'d
- [ ] New API inputs are validated/sanitized at the boundary
- [ ] No new `allow_origins=["*"]` or CORS widening
- [ ] No `unsafe-eval` added to CSP
- [ ] URLs validated with `urlparse()`, not string prefix
- [ ] No plaintext secrets in the database
- [ ] New `subprocess.run()` calls don't use `shell=True` with unsanitized input
- [ ] Dynamic regex patterns use `re.escape()` on user input

### Correctness

- [ ] Error cases handled gracefully
- [ ] No N+1 queries or redundant I/O
- [ ] Tests cover the change adequately
- [ ] State transitions follow the session state machine

### Conventions

- [ ] Frontend uses `<ConfirmPopover>`, never `window.confirm()`
- [ ] Dates use the correct parser (`parseLocalDate` vs `parseDate`)
- [ ] CSS follows the design system (`.btn`, `.panel`, `.form-group select`)
- [ ] Temporary files go to `tmp/`, not the repo root

---

## 5. Architecture Quick Reference

| Layer | Key files | Notes |
|-------|-----------|-------|
| API boundary (input validation) | `api/routes/sessions.py`, `api/app.py` | Sanitize here, not downstream |
| Shell command construction | `terminal/session.py`, `terminal/file_sync.py`, `session/reconnect.py` | Always `shlex.quote()` |
| Credential storage | `api/routes/backup.py` | Keychain helpers pattern |
| CORS configuration | `api/app.py` | Locked origin list |
| CSP configuration | `src-tauri/tauri.conf.json` | No `unsafe-eval` |
| Security tests | `tests/unit/test_security.py` | Sanitization, URL validation, keychain |

For the full code map and architectural deep-dive, see [architecture.md](architecture.md).
