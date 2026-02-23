"""FastAPI application factory."""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import mimetypes
# Prevent Python from scanning /etc/ or other system paths
mimetypes.init(files=[])
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from orchestrator import paths
from orchestrator.state.db import get_connection, ConnectionFactory
from orchestrator.state.migrations.runner import apply_migrations

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    from orchestrator.core.lifecycle import startup_check, shutdown, recover_tunnels
    from orchestrator.core.orchestrator import Orchestrator
    from orchestrator.core.state_manager import StateManager
    from orchestrator.session.tunnel import ReverseTunnelManager

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
    api_port = config.get("server", {}).get("port", 8093)

    # Reconcile DB with tmux state
    try:
        startup_check(conn)
    except Exception:
        logger.exception("Startup check failed (non-fatal)")

    # Create the reverse tunnel manager (subprocess-based)
    tunnel_manager = ReverseTunnelManager(api_port=api_port)
    app.state.tunnel_manager = tunnel_manager

    # Recover tunnels from previous orchestrator run
    try:
        recover_tunnels(conn, tunnel_manager)
    except Exception:
        logger.exception("Tunnel recovery failed (non-fatal)")

    # Start the StateManager (handles event-driven DB writes)
    state_manager = None
    if db_path:
        state_manager = StateManager(db_path)
        app.state.state_manager = state_manager
        await state_manager.start()

    # Start the orchestrator engine (monitor, events, tunnel health)
    orch = Orchestrator(conn, config, db_path=db_path, tunnel_manager=tunnel_manager)
    app.state.orchestrator = orch
    await orch.start()

    # Clean up old images if data/images/ exceeds size cap
    try:
        from orchestrator.api.routes.paste import get_images_dir, cleanup_images
        images_dir = get_images_dir()
        cleanup_images(images_dir)
    except Exception:
        logger.exception("Image cleanup failed (non-fatal)")

    # Clean up old status events (180-day retention)
    try:
        from orchestrator.state.repositories.status_events import cleanup_old_events
        cleanup_old_events(conn, retention_days=180)
    except Exception:
        logger.exception("Status events cleanup failed (non-fatal)")

    # Start rdev background refresh task (skip in test mode — no db_path means in-memory DB)
    from orchestrator.api.routes.rdevs import start_background_refresh, stop_background_refresh
    if db_path:
        start_background_refresh()

    # Start scheduled backup task
    from orchestrator.api.routes.backup import start_backup_schedule, stop_backup_schedule
    if db_path:
        start_backup_schedule(db_path)

    yield

    # Stop scheduled backup
    await stop_backup_schedule()

    # Stop rdev background refresh
    await stop_background_refresh()

    # Shutdown: stop monitor, state manager, tunnels
    logger.info("Orchestrator API shutting down")
    await orch.stop()
    if state_manager:
        await state_manager.stop()
    # Note: we do NOT call tunnel_manager.stop_all() here because tunnels
    # use start_new_session=True and should survive orchestrator restarts.
    # They'll be adopted on next startup via recover_tunnels().
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
    from orchestrator import __version__

    app = FastAPI(
        title="Orchestrator",
        version=__version__,
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
        # For test connections, create a factory that returns the same connection
        # (tests may pass in-memory DBs that can't be reopened by path)
        app.state.conn_factory = None
    elif db_path:
        resolved_db_path = db_path
        conn = get_connection(db_path)
        apply_migrations(conn)
        app.state.conn = conn
        app.state.conn_factory = ConnectionFactory(db_path)
    else:
        # Check env var first (used by E2E tests), then fall back to paths module
        env_db_path = os.environ.get("ORCHESTRATOR_DB_PATH")
        if env_db_path:
            resolved_db_path = env_db_path
            conn = get_connection(env_db_path)
        else:
            resolved_db_path = str(paths.db_path())
            Path(resolved_db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = get_connection(resolved_db_path)
        apply_migrations(conn)
        app.state.conn = conn
        app.state.conn_factory = ConnectionFactory(resolved_db_path)
    app.state.db_path = resolved_db_path

    # Static files — React build assets
    dist_assets = paths.web_dist_dir() / "assets"
    if dist_assets.exists():
        app.mount("/assets", StaticFiles(directory=str(dist_assets)), name="assets")

    # Register routes
    from orchestrator.api.routes import (
        backup,
        brain,
        context,
        notifications,
        paste,
        projects,
        rdevs,
        sessions,
        settings,
        skills,
        tasks,
        trends,
        updates,
    )

    app.include_router(backup.router, prefix="/api", tags=["backup"])
    app.include_router(sessions.router, prefix="/api", tags=["sessions"])
    app.include_router(rdevs.router, prefix="/api", tags=["rdevs"])
    app.include_router(projects.router, prefix="/api", tags=["projects"])
    app.include_router(tasks.router, prefix="/api", tags=["tasks"])
    app.include_router(context.router, prefix="/api", tags=["context"])
    app.include_router(notifications.router, prefix="/api", tags=["notifications"])
    app.include_router(settings.router, prefix="/api", tags=["settings"])
    app.include_router(brain.router, prefix="/api", tags=["brain"])
    app.include_router(skills.router, prefix="/api", tags=["skills"])
    app.include_router(paste.router, prefix="/api", tags=["paste"])
    app.include_router(trends.router, prefix="/api", tags=["trends"])
    app.include_router(updates.router, prefix="/api", tags=["updates"])

    # Health check (used by Tauri shell to know when the sidecar is ready)
    @app.get("/api/health", tags=["health"])
    def health():
        from orchestrator import __version__
        return {"status": "ok", "version": __version__}

    # Open URL in system browser (Tauri webview can't do window.open)
    @app.post("/api/open-url", tags=["util"])
    async def open_url(request: dict):
        import platform
        import subprocess
        url = request.get("url", "")
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            return {"status": "error", "message": "Invalid URL"}
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", url])
            elif platform.system() == "Linux":
                subprocess.Popen(["xdg-open", url])
            else:
                import webbrowser
                webbrowser.open(url)
            return {"status": "ok"}
        except Exception as e:
            logger.error("Failed to open URL %s: %s", url, e)
            return {"status": "error", "message": str(e)}

    # WebSocket
    from orchestrator.api.websocket import websocket_endpoint
    app.add_api_websocket_route("/ws", websocket_endpoint)

    # Terminal WebSocket
    from orchestrator.api.ws_terminal import terminal_websocket
    app.add_api_websocket_route("/ws/terminal/{session_id}", terminal_websocket)

    # Static mount for saved images
    try:
        img_dir = paths.images_dir()
        img_dir.mkdir(parents=True, exist_ok=True)
        app.mount("/api/images", StaticFiles(directory=str(img_dir)), name="images")
    except Exception:
        logger.warning("Could not mount /api/images static files")

    # Dashboard route
    from orchestrator.api.routes.dashboard import router as dashboard_router
    app.include_router(dashboard_router)

    return app
