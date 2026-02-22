"""Centralized path resolution for dev vs packaged (PyInstaller) mode.

In dev mode, paths resolve relative to the project root (directory containing pyproject.toml).
In packaged mode (PyInstaller --onefile), bundled resources live in sys._MEIPASS and
user data goes to ~/Library/Application Support/Orchestrator/.
"""

from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path

# Project root: the directory containing pyproject.toml (two levels up from this file)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def is_packaged() -> bool:
    """Return True if running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def project_root() -> Path:
    """Return the project root (only meaningful in dev mode)."""
    return _PROJECT_ROOT


def data_dir() -> Path:
    """Return the directory for persistent user data (DB, config, logs, images).

    Packaged: ~/Library/Application Support/Orchestrator/
    Dev:      <project_root>/data/
    """
    if is_packaged():
        if platform.system() == "Darwin":
            return Path.home() / "Library" / "Application Support" / "Orchestrator"
        # Fallback for other platforms (future-proofing)
        return Path.home() / ".claude-orchestrator"
    return _PROJECT_ROOT / "data"


def resources_dir() -> Path:
    """Return the base directory for bundled resources.

    Packaged: sys._MEIPASS (temporary extraction directory)
    Dev:      <project_root>/
    """
    if is_packaged():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return _PROJECT_ROOT


def agents_dir() -> Path:
    """Return the path to the agents/ directory (bundled templates + scripts)."""
    return resources_dir() / "agents"


def web_dist_dir() -> Path:
    """Return the path to the built React frontend (web/dist/)."""
    return resources_dir() / "orchestrator" / "web" / "dist"


def db_path() -> Path:
    """Return the path to the SQLite database file."""
    return data_dir() / "orchestrator.db"


def config_path() -> Path:
    """Return the path to the user's config.yaml."""
    return data_dir() / "config.yaml"


def log_path() -> Path:
    """Return the path to the log file."""
    return data_dir() / "orchestrator.log"


def images_dir() -> Path:
    """Return the path to the images directory (for pasted images)."""
    return data_dir() / "images"


def backups_dir() -> Path:
    """Return the path to the database backups directory."""
    return data_dir() / "backups"


def default_config_path() -> Path:
    """Return the path to the bundled default config.yaml template.

    Packaged: sys._MEIPASS/config.yaml
    Dev:      <project_root>/config.yaml
    """
    return resources_dir() / "config.yaml"


def ensure_data_dir() -> None:
    """Create the data directory structure and copy default config on first launch."""
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    images_dir().mkdir(parents=True, exist_ok=True)
    backups_dir().mkdir(parents=True, exist_ok=True)

    # Copy default config if user doesn't have one yet
    user_config = config_path()
    if not user_config.exists():
        default = default_config_path()
        if default.exists():
            shutil.copy2(default, user_config)
