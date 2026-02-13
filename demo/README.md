# Orchestrator Demo

A self-contained demo that reuses the orchestrator UI and API with an isolated database pre-loaded with realistic sample data. Zero changes to the main `orchestrator/` package.

## Quick Start

From the project root (`orchestrator/`):

```bash
# 1. Seed the demo database (optional — auto-seeds on first launch)
python -m demo.seed

# 2. Start the demo server on port 8094
uv run uvicorn demo.app:create_demo_app --factory --port 8094
```

Open http://localhost:8094 to see the demo.

## What's Included

- **3 projects** — API Gateway Migration (active), Frontend Dashboard Redesign (active), Auth Service Hardening (completed)
- **5 sessions** — 4 workers across two projects + 1 brain, with varied statuses
- **15+ tasks** — spread across todo, in_progress, done, and blocked statuses, with subtasks and dependencies
- **2 decisions** — one pending, one resolved
- **4 context items** — global and project-scoped guidelines
- **2 notifications** — PR comment and CI build alerts

## Resetting the Demo

Delete the database and re-seed:

```bash
rm demo/data/demo.db
python -m demo.seed
```

Or just delete the DB — it auto-seeds on next launch.

## Isolation

- Demo database: `demo/data/demo.db`
- Production database: `data/orchestrator.db`
- The demo server runs on port **8094** (production uses 8093)
- No files in `orchestrator/` are modified
