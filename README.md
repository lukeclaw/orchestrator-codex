# Orchestrator

A meta-agent that manages multiple concurrent Claude Code sessions from a single dashboard.



https://github.com/user-attachments/assets/ebb2bb47-c4e2-4e01-81e5-f0b1c8b1bede


Download the latest dmg (Mac M-series processor): https://github.com/yudongqiu/orchestrator/releases/latest/download/Orchestrator_aarch64.dmg


## Try the Demo

The fastest way to see the orchestrator in action — no tmux, no API keys, no setup beyond `uv`:

```bash
uv sync
uv run uvicorn demo.app:create_demo_app --factory --port 8094
```

Open http://localhost:8094 — you'll see 3 projects, 5 workers, and ~15 tasks pre-loaded with realistic data. See [`demo/README.md`](demo/README.md) for details.

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 20+
- tmux 3.x (`brew install tmux`)
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Rust toolchain + Tauri CLI (only for macOS app builds — see [Installing Rust](#installing-rust))

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

## macOS App

The orchestrator is packaged as a native macOS app using Tauri. The `.app` bundle is fully self-contained — it includes Python, all dependencies, tmux, and the built frontend.

### Installing Rust

Install the Rust toolchain via [rustup](https://rustup.rs/):

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

After installation, restart your terminal (or run `source "$HOME/.cargo/env"`), then install the Tauri CLI:

```bash
cargo install tauri-cli
```

### Building the App

```bash
# Full build (frontend + PyInstaller sidecar + Tauri app)
./scripts/build_app.sh

# Skip frontend rebuild if unchanged
./scripts/build_app.sh --skip-frontend
```

Output:
- `src-tauri/target/release/bundle/macos/Orchestrator.app`
- `src-tauri/target/release/bundle/dmg/Orchestrator_*.dmg`

### App Behavior

- **Close button / Cmd+W** — Hides the window. The app stays in the dock and the server keeps running.
- **Click dock icon** — Restores the window.
- **Cmd+Q / Quit from dock** — Fully quits the app and stops the server.

## Development

### Browser-Only (no Tauri)

**Terminal 1 — Backend with auto-reload:**
```bash
uv run uvicorn orchestrator.api.app:create_app --factory --reload --port 8093
```

**Terminal 2 — Frontend with HMR:**
```bash
cd frontend && npm run dev
```

Open http://localhost:5173. Vite proxies API/WS calls to the backend on :8093.

### With Tauri Window

To develop inside the native Tauri window (for testing dock behavior, window events, etc.):

**Terminal 1 — Backend with auto-reload:**
```bash
uv run uvicorn orchestrator.api.app:create_app --factory --reload --port 8093
```

**Terminal 2 — Tauri + Vite:**
```bash
cd src-tauri
cargo tauri dev
```

In dev mode, Tauri opens the Vite dev server directly and does **not** spawn the bundled sidecar. The production app and dev workflow are fully isolated.

### CLI Mode

```bash
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
├── src-tauri/               # Tauri shell (Rust)
│   ├── src/lib.rs           # App setup, sidecar lifecycle, window events
│   ├── tauri.conf.json      # Bundle config, CSP, dev/prod URLs
│   └── loading.html         # Splash screen during server startup
├── scripts/
│   ├── build_sidecar.py     # Builds PyInstaller onedir bundle
│   └── build_app.sh         # Full production build script
├── orchestrator.spec        # PyInstaller spec (onedir mode)
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
