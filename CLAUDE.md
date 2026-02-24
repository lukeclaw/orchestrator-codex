# Claude Code Project Instructions

## Temporary Files

Save all temporary files (screenshots, test outputs, scratch files) to `tmp/` — never the repo root. The `tmp/` directory is gitignored.

## Code Quality

Before committing Python changes, run `uv run ruff check . --fix && uv run ruff format .` to lint and format the code.

## Design Docs

The UI design document is at `docs/005-ui-design.md`. Keep it updated when making UI changes.
