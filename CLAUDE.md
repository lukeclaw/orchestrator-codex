# Claude Code Project Instructions

## Temporary Files

Save all temporary files (screenshots, test outputs, scratch files) to `tmp/` — never the repo root. The `tmp/` directory is gitignored.

## Code Quality

Before committing Python changes, run `uv run ruff check . --fix && uv run ruff format .` to lint and format the code.

After completing a change, review your own work as a senior software architect. Check for:
- Unnecessary complexity or over-engineering
- Missing error handling or edge cases
- Code duplication that should be refactored
- Naming clarity and consistent conventions
- Performance concerns (e.g., N+1 queries, redundant I/O)

Point out any areas that can be improved before considering the task done.

## Testing

Verify all changes with tests. Aim for full coverage of new and modified code paths. Tests should:
- Run fast — mock external dependencies (subprocess, network, file I/O) instead of hitting real services
- Cover happy path, error cases, and edge cases
- Use `uv run pytest <test_file> -v -o "addopts="` to run specific test files

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

## Design Docs

The design documents are at `docs/` folder. Keep it updated for relavent topics, and add new topics for major changes or feature additions.
