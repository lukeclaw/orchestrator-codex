"""FastAPI application factory."""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from orchestrator.state.db import get_connection, ConnectionFactory
from orchestrator.state.migrations.runner import apply_migrations

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    from orchestrator.core.lifecycle import startup_check, shutdown
    from orchestrator.core.orchestrator import Orchestrator
    from orchestrator.core.state_manager import StateManager

    logger.info("Orchestrator API starting up")

    conn = app.state.conn
    db_path = app.state.db_path

    # Load config for the orchestrator engine
    try:
        from orchestrator.main import load_config
        config = load_config()
    except Exception:
        config = {}

    app.state.config = config

    # Reconcile DB with tmux state
    try:
        startup_check(conn)
    except Exception:
        logger.exception("Startup check failed (non-fatal)")

    # Seed default auto-approve rules
    try:
        from orchestrator.automation.auto_approve import seed_auto_approve_defaults
        seed_auto_approve_defaults(conn)
    except Exception:
        logger.exception("Failed to seed auto-approve defaults (non-fatal)")

    # Start the StateManager (handles event-driven DB writes)
    state_manager = None
    if db_path:
        state_manager = StateManager(db_path)
        app.state.state_manager = state_manager
        await state_manager.start()

    # Start the orchestrator engine (monitor, events, recovery)
    orch = Orchestrator(conn, config, db_path=db_path)
    app.state.orchestrator = orch
    await orch.start()

    yield

    # Shutdown: stop monitor, state manager, save snapshots
    logger.info("Orchestrator API shutting down")
    await orch.stop()
    if state_manager:
        await state_manager.stop()
    try:
        shutdown(conn)
    except Exception:
        logger.exception("Shutdown error")
    if conn:
        conn.close()


def create_app(
    db: sqlite3.Connection | None = None,
    db_path: str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Claude Orchestrator",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Database
    # IMPORTANT: Production database is at `data/orchestrator.db` (configured in config.yaml).
    # Do NOT create additional database files. Migrations run automatically on startup.
    resolved_db_path = None
    if db is not None:
        app.state.conn = db
    elif db_path:
        resolved_db_path = db_path
        conn = get_connection(db_path)
        apply_migrations(conn)
        app.state.conn = conn
    else:
        # Check env var first (used by E2E tests), then fall back to config
        env_db_path = os.environ.get("ORCHESTRATOR_DB_PATH")
        if env_db_path:
            resolved_db_path = env_db_path
            conn = get_connection(env_db_path)
        else:
            from orchestrator.main import PROJECT_ROOT, load_config
            config = load_config()
            # Default: data/orchestrator.db (relative to project root)
            resolved_db_path = str(PROJECT_ROOT / config["database"]["path"])
            conn = get_connection(resolved_db_path)
        apply_migrations(conn)
        app.state.conn = conn
    app.state.db_path = resolved_db_path

    # Static files — React build assets
    dist_assets = WEB_DIR / "dist" / "assets"
    if dist_assets.exists():
        app.mount("/assets", StaticFiles(directory=str(dist_assets)), name="assets")

    # Register routes
    from orchestrator.api.routes import (
        brain,
        context,
        health,
        projects,
        reporting,
        sessions,
        settings,
        tasks,
    )

    app.include_router(sessions.router, prefix="/api", tags=["sessions"])
    app.include_router(projects.router, prefix="/api", tags=["projects"])
    app.include_router(tasks.router, prefix="/api", tags=["tasks"])
    app.include_router(reporting.router, prefix="/api", tags=["reporting"])
    app.include_router(context.router, prefix="/api", tags=["context"])
    app.include_router(health.router, prefix="/api", tags=["health"])
    app.include_router(settings.router, prefix="/api", tags=["settings"])
    app.include_router(brain.router, prefix="/api", tags=["brain"])

    # WebSocket
    from orchestrator.api.websocket import websocket_endpoint
    app.add_api_websocket_route("/ws", websocket_endpoint)

    # Terminal WebSocket
    from orchestrator.api.ws_terminal import terminal_websocket
    app.add_api_websocket_route("/ws/terminal/{session_id}", terminal_websocket)

    # Dashboard route
    from orchestrator.api.routes.dashboard import router as dashboard_router
    app.include_router(dashboard_router)

    return app
