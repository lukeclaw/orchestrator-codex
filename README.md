# Claude Orchestrator

A meta-agent that manages multiple concurrent Claude Code sessions from a single dashboard.

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 20+
- tmux 3.x
- uv (Python package manager)

### 1. Backend

```bash
cd orchestrator

# Install dependencies
uv sync

# Start the API server (runs on http://localhost:8093)
uv run uvicorn orchestrator.api.app:create_app --factory --reload --port 8093
```

### 2. Frontend

```bash
cd orchestrator/frontend

# Install dependencies
npm install
# or
yarn install

# Start dev server (runs on http://localhost:5173, proxies API to :8093)
npm run dev
# or
yarn dev
```

### 3. Open Dashboard

- **Frontend Dev Server**: http://localhost:5173 (hot reload, proxies to backend)
- **Backend API**: http://localhost:8093/api

## Development

### Running Both Servers

**Terminal 1 — Backend:**
```bash
cd orchestrator
uv run uvicorn orchestrator.api.app:create_app --factory --reload --port 8093
```

**Terminal 2 — Frontend:**
```bash
cd orchestrator/frontend
yarn dev
```

### CLI Mode

```bash
cd orchestrator
uv run orchestrator
```

This starts an interactive CLI shell with commands like `/help`, `/status`, `/add`, etc.

### Project Structure

```
orchestrator/
├── config.yaml              # Bootstrap config (server, DB, tmux settings)
├── orchestrator/            # Python backend
│   ├── api/                 # FastAPI routes & WebSocket
│   ├── automation/          # Auto-approve engine
│   ├── core/                # Orchestrator engine & event bus
│   ├── llm/                 # LLM brain, context selector
│   ├── recovery/            # Snapshot & re-brief
│   ├── scheduler/           # Task assignment
│   ├── state/               # DB, models, repositories
│   └── terminal/            # tmux management, monitor, output parser
├── frontend/                # React + TypeScript dashboard
│   └── src/
│       ├── pages/           # Dashboard, Workers, Projects, etc.
│       ├── components/      # Reusable UI components
│       └── context/         # AppContext (state management)
├── prompts/                 # CLAUDE.md templates for brain & workers
└── data/                    # SQLite DB & logs (gitignored)
```

### API Docs

When the backend is running, OpenAPI docs are available at:
- http://localhost:8093/docs (Swagger UI)
- http://localhost:8093/redoc (ReDoc)

## Database

**IMPORTANT:** The orchestrator uses a single SQLite database. Do NOT create additional database files.

| File | Purpose |
|------|---------|
| `data/orchestrator.db` | **Production database** — used by the server |

The database path is configured in `config.yaml`:
```yaml
database:
  path: "data/orchestrator.db"    # Relative to project root
```

### Applying Migrations

Migrations run automatically on server startup. To manually apply migrations:

```bash
cd orchestrator
.venv/bin/python -c "
from orchestrator.state.db import get_connection
from orchestrator.state.migrations.runner import apply_migrations
conn = get_connection('data/orchestrator.db')  # ALWAYS use this path
apply_migrations(conn)
conn.close()
"
```

### Checking Database Schema

```bash
.venv/bin/python -c "
from orchestrator.state.db import get_connection
conn = get_connection('data/orchestrator.db')
cursor = conn.execute('PRAGMA table_info(sessions)')
print([row[1] for row in cursor.fetchall()])
conn.close()
"
```

## Configuration

Edit `config.yaml` for:
- Server port (`server.port`)
- Database path (`database.path`) — **do not change unless you know what you're doing**
- tmux session name (`tmux.session_name`)
- Monitoring intervals
- Logging level

## License

Private / Internal Use
