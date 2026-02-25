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

## Design Docs

The design documents are at `docs/` folder. Keep it updated for relavent topics, and add new topics for major changes or feature additions.
