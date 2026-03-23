"""App initialization: config loading and logging setup."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import yaml

from orchestrator import paths


def load_config(config_path: Path | None = None) -> dict:
    """Load bootstrap config from YAML."""
    if config_path is None:
        config_path = paths.config_path()
        # Fall back to project-root config.yaml if data-dir copy doesn't exist yet
        if not config_path.exists():
            config_path = paths.default_config_path()
    if not config_path.exists():
        print(f"Error: Config not found: {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path) as f:
        return yaml.safe_load(f)


def setup_logging(config: dict):
    """Configure logging from bootstrap config."""
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)

    handlers: list[logging.Handler] = []

    # Only add StreamHandler in dev mode.  In packaged (sidecar) mode,
    # stderr is a pipe to the Tauri parent.  If the parent stops reading,
    # the pipe buffer fills up and any logging flush() blocks the asyncio
    # event loop, freezing the entire server.
    if not paths.is_packaged():
        handlers.append(logging.StreamHandler())

    lp = paths.log_path()
    lp.parent.mkdir(parents=True, exist_ok=True)
    from logging.handlers import RotatingFileHandler

    # 15 MB active + 1 backup = 30 MB total cap.  When the active file
    # hits 15 MB it is renamed to .log.1 (a cheap rename, not a rewrite)
    # and a fresh file is opened.  This also handles legacy oversized logs
    # gracefully: the big file becomes .log.1 and is replaced on the next
    # rotation cycle.
    handlers.append(RotatingFileHandler(str(lp), maxBytes=15_000_000, backupCount=1))

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )
