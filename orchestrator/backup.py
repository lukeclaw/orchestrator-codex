"""Database backup: safe SQLite snapshots, AES-256 encrypted zip, local folder storage.

Usage:
    from orchestrator.backup import run_backup, list_backups

    result = run_backup(db_path, password="secret", backup_dir="/path/to/backups", retention=5)
    backups = list_backups("/path/to/backups")
"""

from __future__ import annotations

import logging
import re
import shutil
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


def decrypt_from_zip(zip_path: Path, password: str) -> Path:
    """Extract the database file from an AES-encrypted zip archive.

    Returns the path to a temporary file containing the extracted DB.
    The caller is responsible for cleaning up the temp file.

    Raises:
        FileNotFoundError: If zip_path does not exist.
        KeyError: If 'orchestrator.db' is not found inside the zip.
        RuntimeError: If the password is incorrect.
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise FileNotFoundError(f"Backup file not found: {zip_path}")

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    tmp_path = Path(tmp.name)

    try:
        with pyzipper.AESZipFile(str(zip_path), "r") as zf:
            zf.setpassword(password.encode("utf-8"))
            if "orchestrator.db" not in zf.namelist():
                raise KeyError("orchestrator.db not found inside backup zip")
            tmp_path.write_bytes(zf.read("orchestrator.db"))
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    logger.info("Decrypted backup: %s → %s", zip_path.name, tmp_path)
    return tmp_path


def validate_sqlite_db(db_path: Path) -> bool:
    """Check whether a file is a valid SQLite database.

    Runs ``PRAGMA integrity_check`` and returns True only if it passes.
    Returns False for non-existent paths, non-SQLite files, or corrupt databases.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(str(db_path))
        result = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        return result is not None and result[0] == "ok"
    except Exception:
        return False


def restore_backup(
    filename: str,
    backup_dir: str | Path,
    password: str,
    target_db_path: str | Path,
) -> dict:
    """Restore a database from an encrypted backup file.

    Steps:
        1. Validate filename against backup pattern (path-traversal protection).
        2. Verify resolved path stays within backup_dir.
        3. Decrypt zip → validate extracted SQLite.
        4. Create a pre-restore safety copy of the current DB.
        5. Remove stale WAL/SHM files.
        6. Replace DB file via shutil.copy2.

    Returns:
        Dict with keys: ok, error, pre_restore_backup.
    """
    backup_dir = Path(backup_dir)
    target_db_path = Path(target_db_path)

    # 1. Validate filename
    if not _BACKUP_PATTERN.match(filename):
        return {"ok": False, "error": "Invalid backup filename", "pre_restore_backup": None}

    zip_path = (backup_dir / filename).resolve()

    # 2. Path-traversal protection
    if not str(zip_path).startswith(str(backup_dir.resolve())):
        return {"ok": False, "error": "Invalid backup path", "pre_restore_backup": None}

    if not zip_path.exists():
        return {"ok": False, "error": f"Backup file not found: {filename}", "pre_restore_backup": None}

    extracted_path = None
    try:
        # 3. Decrypt and validate
        extracted_path = decrypt_from_zip(zip_path, password)
        if not validate_sqlite_db(extracted_path):
            return {"ok": False, "error": "Extracted database failed integrity check", "pre_restore_backup": None}

        # 4. Pre-restore safety copy
        pre_restore_name = f"pre-restore-{_timestamp_for_filename()}.db"
        pre_restore_path = target_db_path.parent / pre_restore_name
        if target_db_path.exists():
            shutil.copy2(str(target_db_path), str(pre_restore_path))
            logger.info("Pre-restore backup saved: %s", pre_restore_path)

        # 5. Remove stale WAL/SHM files
        for suffix in ("-wal", "-shm"):
            wal_path = target_db_path.parent / (target_db_path.name + suffix)
            if wal_path.exists():
                wal_path.unlink()
                logger.info("Removed stale %s file", suffix)

        # 6. Replace DB
        shutil.copy2(str(extracted_path), str(target_db_path))
        logger.info("Database restored from %s", filename)

        return {
            "ok": True,
            "error": None,
            "pre_restore_backup": pre_restore_name,
        }

    except RuntimeError as e:
        return {"ok": False, "error": f"Decryption failed (wrong password?): {e}", "pre_restore_backup": None}
    except Exception as e:
        logger.exception("Restore failed")
        return {"ok": False, "error": str(e), "pre_restore_backup": None}
    finally:
        if extracted_path and Path(extracted_path).exists():
            Path(extracted_path).unlink(missing_ok=True)
