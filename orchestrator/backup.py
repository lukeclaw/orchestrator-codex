"""Database backup: safe SQLite snapshots, AES-256 encrypted zip, local folder storage.

Usage:
    from orchestrator.backup import run_backup, list_backups

    result = run_backup(db_path, password="secret", backup_dir="/path/to/backups", retention=5)
    backups = list_backups("/path/to/backups")
"""

from __future__ import annotations

import logging
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pyzipper

from orchestrator.utils import utc_now_iso

logger = logging.getLogger(__name__)

# Filename pattern: orchestrator-backup-2026-02-19T17-00-00Z.zip
_BACKUP_PREFIX = "orchestrator-backup-"
_BACKUP_SUFFIX = ".zip"
_BACKUP_PATTERN = re.compile(
    r"^orchestrator-backup-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z)\.zip$"
)


def _timestamp_for_filename() -> str:
    """Return a filesystem-safe UTC timestamp like '2026-02-19T17-00-00Z'."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    return now


def create_db_snapshot(db_path: str | Path) -> Path:
    """Create a consistent SQLite snapshot using the backup API.

    Returns the path to a temporary file containing the snapshot.
    The caller is responsible for cleaning up the temp file.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    # Create temp file for the snapshot
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    snapshot_path = Path(tmp.name)

    try:
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(str(snapshot_path))
        src.backup(dst)
        dst.close()
        src.close()
    except Exception:
        snapshot_path.unlink(missing_ok=True)
        raise

    logger.info("Created DB snapshot: %s (%.1f KB)", snapshot_path, snapshot_path.stat().st_size / 1024)
    return snapshot_path


def encrypt_to_zip(src: Path, dest: Path, password: str) -> None:
    """Compress and encrypt a file into an AES-256 zip archive.

    Args:
        src: Path to the file to compress.
        dest: Path for the output .zip file.
        password: Encryption password.
    """
    pwd_bytes = password.encode("utf-8")
    with pyzipper.AESZipFile(
        str(dest),
        "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as zf:
        zf.setpassword(pwd_bytes)
        # Store as "orchestrator.db" inside the zip
        zf.write(str(src), arcname="orchestrator.db")

    logger.info("Encrypted zip created: %s (%.1f KB)", dest, dest.stat().st_size / 1024)


def _prune_old_backups(backup_dir: Path, retention: int) -> list[str]:
    """Delete oldest backup files beyond the retention count.

    Returns list of deleted filenames.
    """
    if retention <= 0:
        return []

    backups = sorted(
        [f for f in backup_dir.iterdir() if _BACKUP_PATTERN.match(f.name)],
        key=lambda f: f.name,
        reverse=True,  # newest first
    )

    deleted = []
    for old_file in backups[retention:]:
        old_file.unlink()
        deleted.append(old_file.name)
        logger.info("Pruned old backup: %s", old_file.name)

    return deleted


def run_backup(
    db_path: str | Path,
    password: str,
    backup_dir: str | Path,
    retention: int = 5,
) -> dict:
    """Run a full backup: snapshot → encrypt → save → prune.

    Args:
        db_path: Path to the live SQLite database.
        password: Encryption password for the zip.
        backup_dir: Directory to store backup files.
        retention: Number of backup files to keep (0 = unlimited).

    Returns:
        Dict with keys: ok, filename, size_bytes, timestamp, pruned, error.
    """
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = _timestamp_for_filename()
    filename = f"{_BACKUP_PREFIX}{timestamp}{_BACKUP_SUFFIX}"
    dest = backup_dir / filename
    snapshot_path = None

    try:
        # 1. Snapshot
        snapshot_path = create_db_snapshot(db_path)

        # 2. Encrypt
        encrypt_to_zip(snapshot_path, dest, password)

        # 3. Prune old backups
        pruned = _prune_old_backups(backup_dir, retention) if retention > 0 else []

        result = {
            "ok": True,
            "filename": filename,
            "size_bytes": dest.stat().st_size,
            "timestamp": utc_now_iso(),
            "pruned": pruned,
            "error": None,
        }
        logger.info("Backup complete: %s", filename)
        return result

    except Exception as e:
        logger.exception("Backup failed")
        return {
            "ok": False,
            "filename": filename,
            "size_bytes": 0,
            "timestamp": utc_now_iso(),
            "pruned": [],
            "error": str(e),
        }
    finally:
        # Clean up temp snapshot
        if snapshot_path and snapshot_path.exists():
            snapshot_path.unlink(missing_ok=True)


def list_backups(backup_dir: str | Path) -> list[dict]:
    """List backup files in the directory, newest first.

    Returns list of dicts with keys: filename, timestamp, size_bytes.
    """
    backup_dir = Path(backup_dir)
    if not backup_dir.exists():
        return []

    results = []
    for f in sorted(backup_dir.iterdir(), key=lambda f: f.name, reverse=True):
        m = _BACKUP_PATTERN.match(f.name)
        if m:
            # Convert filename timestamp back to readable format
            ts = m.group(1).replace("-", ":", 3)  # partial — reconstruct below
            # Actually, just store the raw filename timestamp
            results.append({
                "filename": f.name,
                "timestamp": m.group(1),
                "size_bytes": f.stat().st_size,
            })

    return results
