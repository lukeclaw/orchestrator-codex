# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Claude Orchestrator sidecar binary.

Produces a single-file executable that Tauri launches as a sidecar.

Build with:
    pyinstaller orchestrator.spec
"""

import os
import glob

block_cipher = None

# Collect SQL migration files
migration_dir = os.path.join("orchestrator", "state", "migrations", "versions")
migration_files = [(f, os.path.join("orchestrator", "state", "migrations", "versions"))
                   for f in glob.glob(os.path.join(migration_dir, "*.sql"))]

a = Analysis(
    ["orchestrator/launcher.py"],
    pathex=["."],
    binaries=[
        # Bundled tmux (self-contained with rewritten dylib paths)
        ("src-tauri/tmux-bundle/tmux", "tmux-bundle"),
        ("src-tauri/tmux-bundle/libutf8proc.3.dylib", "tmux-bundle"),
        ("src-tauri/tmux-bundle/libncursesw.6.dylib", "tmux-bundle"),
        ("src-tauri/tmux-bundle/libevent_core-2.1.7.dylib", "tmux-bundle"),
    ],
    datas=[
        # Bundled resources
        ("agents", "agents"),
        ("orchestrator/web/dist", "orchestrator/web/dist"),
        ("config.yaml", "."),
        # SQL migrations (needed at runtime)
        *migration_files,
    ],
    hiddenimports=[
        # Uvicorn internals
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        # FastAPI / Starlette
        "fastapi",
        "starlette",
        "starlette.responses",
        "starlette.staticfiles",
        "starlette.websockets",
        "anyio._backends._asyncio",
        # Orchestrator modules (imported dynamically via create_app)
        "orchestrator",
        "orchestrator.paths",
        "orchestrator.launcher",
        "orchestrator.main",
        "orchestrator.api.app",
        "orchestrator.api.websocket",
        "orchestrator.api.ws_terminal",
        "orchestrator.api.routes.backup",
        "orchestrator.api.routes.brain",
        "orchestrator.api.routes.context",
        "orchestrator.api.routes.dashboard",
        "orchestrator.api.routes.notifications",
        "orchestrator.api.routes.paste",
        "orchestrator.api.routes.projects",
        "orchestrator.api.routes.rdevs",
        "orchestrator.api.routes.sessions",
        "orchestrator.api.routes.settings",
        "orchestrator.api.routes.tasks",
        "orchestrator.core.events",
        "orchestrator.core.lifecycle",
        "orchestrator.core.orchestrator",
        "orchestrator.core.state_manager",
        "orchestrator.state.db",
        "orchestrator.state.models",
        "orchestrator.state.migrations.runner",
        "orchestrator.state.repositories.config",
        "orchestrator.state.repositories.context",
        "orchestrator.state.repositories.notifications",
        "orchestrator.state.repositories.projects",
        "orchestrator.state.repositories.sessions",
        "orchestrator.state.repositories.tasks",
        "orchestrator.session.health",
        "orchestrator.session.reconnect",
        "orchestrator.session.state_machine",
        "orchestrator.session.tunnel",
        "orchestrator.session.tunnel_monitor",
        "orchestrator.terminal.control",
        "orchestrator.terminal.file_sync",
        "orchestrator.terminal.manager",
        "orchestrator.terminal.markers",
        "orchestrator.terminal.monitor",
        "orchestrator.terminal.output_parser",
        "orchestrator.terminal.pty_stream",
        "orchestrator.terminal.session",
        "orchestrator.terminal.ssh",
        "orchestrator.agents.deploy",
        "orchestrator.backup",
        "orchestrator.utils",
        # Dependencies that may not be auto-detected
        "yaml",
        "click",
        "rich",
        "httpx",
        "anthropic",
        "pydantic",
        "python_dateutil",
        "dateutil",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Dev-only dependencies
        "pytest",
        "pytest_asyncio",
        "pytest_xdist",
        "pytest_timeout",
        "ruff",
        "mypy",
        # Unnecessary large packages
        "tkinter",
        "matplotlib",
        "PIL",
        "numpy",
        "scipy",
        "pandas",
        # Playwright (not needed in sidecar — browser automation is dev-only)
        "playwright",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="orchestrator-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
