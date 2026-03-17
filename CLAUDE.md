# Claude Code Project Instructions

## Temporary Files

Save all temporary files (screenshots, test outputs, scratch files) to `tmp/` — never the repo root. The `tmp/` directory is gitignored.

## Security

This app has a larger attack surface than a typical CLI tool — it runs an HTTP server reachable by any local process and executes shell commands on local and remote hosts. See `development.md` for the full threat model. Key rules:

- **Shell injection**: Always `shlex.quote()` user-controlled values in shell commands (`subprocess.run()`, `tmux.send_keys()`, SSH). No exceptions.
- **Input validation**: Sanitize names/paths at the API boundary with allowlist regex. See `_sanitize_worker_name()` in `api/routes/sessions.py`.
- **URL validation**: Use `urlparse()`, never string prefix checks. Validate scheme + netloc.
- **CORS**: Origins are locked to specific localhost/Tauri values in `api/app.py`. Do not widen to `"*"`.
- **CSP**: No `unsafe-eval` in `src-tauri/tauri.conf.json`. Do not add it.
- **Secrets**: Use macOS Keychain (pattern in `api/routes/backup.py`), not plaintext DB storage.

## Code Quality

Before committing Python changes, run `uv run ruff check . --fix && uv run ruff format .` to lint and format the code.

After completing a change, review your own work as a senior software architect. Check for:
- Unnecessary complexity or over-engineering
- Missing error handling or edge cases
- Code duplication that should be refactored
- Naming clarity and consistent conventions
- Performance concerns (e.g., N+1 queries, redundant I/O)
- **Security: shell injection, unsanitized input, credential exposure** (see Security section above)

Point out any areas that can be improved before considering the task done.

## Testing

Always update and verify tests for any code change. Aim for full coverage of new and modified code paths. Tests should:
- Run fast — mock external dependencies (subprocess, network, file I/O) instead of hitting real services
- Cover happy path, error cases, and edge cases
- Use `uv run pytest <test_file>` to run specific test files
- The full test suite must complete within **30 seconds** (uses `-n auto` parallelism and 10s per-test timeout from `pyproject.toml`). Any test that causes the suite to exceed this must be fixed (reduce scope, add mocks, or split).

## Pre-Commit Checks

Always run the linter, formatter, type checker, and tests before committing:
1. `uv run ruff check . --fix && uv run ruff format .` — lint and format Python
2. `cd frontend && npx tsc --noEmit` — type-check frontend (if frontend files changed)
3. `uv run pytest` — run the full test suite

Do not commit if there are failures caused by your changes.

## Frontend — Tauri Compatibility

Never use `window.confirm()`, `window.alert()`, `window.prompt()`, or any other native browser dialogs. They do not work in the Tauri webview. Instead, use the `<ConfirmPopover>` component (`components/common/ConfirmPopover.tsx`) for destructive action confirmations, which supports danger/warning/default variants and smart positioning.

## Server Ports

- **Backend API**: `http://localhost:8093` (uvicorn, configured in `orchestrator/launcher.py`)
- **Frontend dev server**: `http://localhost:5173` (Vite, do not start a new one)

## Frontend — UI Inspection with Playwright

Use Playwright MCP tools to verify frontend changes visually. The dev server runs at `http://localhost:5173` (do not start a new one). Always snapshot first to get `ref=` handles before clicking or screenshotting elements. Save screenshots to `tmp/`. Prefer read-only interaction — avoid typing or mutating app state when just exploring.

## Frontend — Design Conventions

- **Dropdowns/selects**: Always use the global `.form-group select` styling from `styles/global.css` (custom chevron via `appearance: none` + background SVG, consistent padding, border, and focus ring). Never use bare `<select>` without the `.form-group` wrapper.
- **Buttons**: Use `.btn`, `.btn-primary`, `.btn-secondary`, `.btn-danger`, `.btn-sm` from `global.css`.
- **Panels**: Use `.panel` / `.panel-header` / `.panel-body` from `global.css` for card containers.
- **Tabs**: Use the pill-style tab bar pattern (`.settings-tabs` / `.settings-tab` in SettingsPage, or `.np-tabs` / `.np-tab` in NotificationsPage).
- **Confirmations**: Use `<ConfirmPopover>` — never `window.confirm()`.
- **Design principles**: See `docs/design_logs/022-ui-design-principles.md` for the full visual design language (color system, elevation, spacing, component patterns, anti-patterns). Read it when making UI changes to ensure consistency.

## Frontend — Timezone Handling

When working with dates and times in the frontend, always be aware of timezone implications:

- **Date-only strings** (e.g. `"2026-03-09"` from `target_date`): `new Date("2026-03-09")` parses as **UTC midnight**, which shifts to the previous day in negative-offset timezones (e.g. US Pacific). Always use `parseLocalDate()` from `components/common/TimeAgo.tsx` to parse these as local midnight.
- **Datetime strings** (e.g. `created_at`, `updated_at`): These are UTC timestamps. Use `parseDate()` from `TimeAgo.tsx` which appends `Z` to timezone-naive datetime strings.
- **`<input type="date">`**: Returns `YYYY-MM-DD` strings in local timezone — no conversion needed for form values.
- **General rule**: Whenever you display or compare a date/time, verify whether it should be local or UTC and use the appropriate parser.

## Design Docs

The design documents are at `docs/` folder. `docs/features.md` and `docs/architecture.md` are the aggregated references. Detailed design logs are in `docs/design_logs/`. Keep them updated for relevant topics, and add new design logs for major changes or feature additions.

## Development Learnings

Past mistakes, root causes, and rules are documented in `docs/learnings/`. Consult these before working on related areas -- especially health checks, reconnect logic, migrations, and socket/PTY streaming. Add new learning files when you discover a bug pattern or design flaw worth remembering. See `docs/learnings/README.md` for the full index.
