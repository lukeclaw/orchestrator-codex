"""PyInstaller entry point for the packaged sidecar binary.

This module boots the FastAPI server when running inside a Tauri app.
It ensures the data directory exists, checks prerequisites, and starts uvicorn.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import sys

import uvicorn


def setup_path():
    """Add bundled binaries and common Homebrew paths to PATH.

    In packaged mode, the tmux binary is bundled in sys._MEIPASS/tmux-bundle/.
    We also add standard Homebrew paths so system-installed tools are found.
    """
    extra_paths = []

    # Bundled tmux (inside PyInstaller extraction directory)
    if hasattr(sys, "_MEIPASS"):
        bundled_tmux = os.path.join(sys._MEIPASS, "tmux-bundle")
        if os.path.isdir(bundled_tmux):
            extra_paths.append(bundled_tmux)

    # Common Homebrew paths (ARM and Intel Macs)
    extra_paths.extend([
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ])

    current_path = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join(extra_paths) + os.pathsep + current_path


def check_prerequisites():
    """Verify that required external tools are available."""
    if not shutil.which("tmux"):
        print(
            "ERROR: tmux is not installed or not on PATH.\n"
            "Install it with: brew install tmux",
            file=sys.stderr,
        )
        sys.exit(1)


def main():
    from orchestrator import paths
    from orchestrator.main import load_config, setup_logging

    # 1. Set up PATH (bundled tmux + Homebrew paths)
    setup_path()

    # 2. Create data directory, copy default config on first launch
    paths.ensure_data_dir()

    # 3. Check prerequisites
    check_prerequisites()

    # 4. Load config and set up logging
    config = load_config()
    setup_logging(config)

    logger = logging.getLogger("orchestrator.launcher")
    logger.info("Starting Claude Orchestrator sidecar (packaged=%s)", paths.is_packaged())
    logger.info("Data directory: %s", paths.data_dir())
    logger.info("tmux: %s", shutil.which("tmux"))

    # 5. Handle SIGTERM for clean shutdown (Tauri sends this on app close)
    def handle_sigterm(signum, frame):
        logger.info("Received SIGTERM, shutting down")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)

    # 6. Start uvicorn
    host = config.get("server", {}).get("host", "127.0.0.1")
    port = config.get("server", {}).get("port", 8093)

    logger.info("Starting uvicorn on %s:%d", host, port)
    uvicorn.run(
        "orchestrator.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
