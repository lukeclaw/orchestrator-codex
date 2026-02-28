"""Unit tests for orchestrator.backup — snapshot, encrypt, retention, list, restore."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import pyzipper

from orchestrator.backup import (
    _BACKUP_PATTERN,
    _prune_old_backups,
    create_db_snapshot,
    decrypt_from_zip,
    encrypt_to_zip,
    list_backups,
    restore_backup,
    run_backup,
    validate_sqlite_db,
)


@pytest.fixture
def sample_db(tmp_path: Path) -> Path:
    """Create a small SQLite database for testing."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO items (name) VALUES ('alpha')")
    conn.execute("INSERT INTO items (name) VALUES ('beta')")
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def backup_dir(tmp_path: Path) -> Path:
    d = tmp_path / "backups"
    d.mkdir()
    return d


class TestCreateDbSnapshot:
    def test_snapshot_is_valid_sqlite(self, sample_db: Path):
        snapshot = create_db_snapshot(sample_db)
        try:
            conn = sqlite3.connect(str(snapshot))
            rows = conn.execute("SELECT name FROM items ORDER BY name").fetchall()
            assert [r[0] for r in rows] == ["alpha", "beta"]
            conn.close()
        finally:
            snapshot.unlink(missing_ok=True)

    def test_snapshot_missing_db_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            create_db_snapshot(tmp_path / "nonexistent.db")

    def test_snapshot_is_independent_copy(self, sample_db: Path):
        snapshot = create_db_snapshot(sample_db)
        try:
            # Modify original
            conn = sqlite3.connect(str(sample_db))
            conn.execute("INSERT INTO items (name) VALUES ('gamma')")
            conn.commit()
            conn.close()

            # Snapshot should NOT have the new row
            conn2 = sqlite3.connect(str(snapshot))
            rows = conn2.execute("SELECT name FROM items ORDER BY name").fetchall()
            assert [r[0] for r in rows] == ["alpha", "beta"]
            conn2.close()
        finally:
            snapshot.unlink(missing_ok=True)


class TestEncryptToZip:
    def test_creates_encrypted_zip(self, sample_db: Path, tmp_path: Path):
        dest = tmp_path / "backup.zip"
        encrypt_to_zip(sample_db, dest, password="secret123")

        assert dest.exists()
        assert dest.stat().st_size > 0

    def test_zip_decryptable_with_correct_password(self, sample_db: Path, tmp_path: Path):
        dest = tmp_path / "backup.zip"
        encrypt_to_zip(sample_db, dest, password="mypass")

        with pyzipper.AESZipFile(str(dest), "r") as zf:
            zf.setpassword(b"mypass")
            names = zf.namelist()
            assert "orchestrator.db" in names

            # Extract and verify contents
            extracted = tmp_path / "extracted.db"
            extracted.write_bytes(zf.read("orchestrator.db"))
            conn = sqlite3.connect(str(extracted))
            rows = conn.execute("SELECT name FROM items ORDER BY name").fetchall()
            assert [r[0] for r in rows] == ["alpha", "beta"]
            conn.close()

    def test_zip_wrong_password_fails(self, sample_db: Path, tmp_path: Path):
        dest = tmp_path / "backup.zip"
        encrypt_to_zip(sample_db, dest, password="correct")

        with pyzipper.AESZipFile(str(dest), "r") as zf:
            zf.setpassword(b"wrong")
            with pytest.raises(RuntimeError):
                zf.read("orchestrator.db")


class TestPruneOldBackups:
    def _create_fake_backups(self, backup_dir: Path, count: int) -> list[Path]:
        files = []
        for i in range(count):
            name = f"orchestrator-backup-2026-02-{i + 1:02d}T12-00-00Z.zip"
            f = backup_dir / name
            f.write_text("fake")
            files.append(f)
        return files

    def test_prune_removes_oldest(self, backup_dir: Path):
        self._create_fake_backups(backup_dir, 7)
        deleted = _prune_old_backups(backup_dir, retention=5)
        assert len(deleted) == 2
        remaining = list(backup_dir.iterdir())
        assert len(remaining) == 5

    def test_prune_keeps_newest(self, backup_dir: Path):
        self._create_fake_backups(backup_dir, 7)
        _prune_old_backups(backup_dir, retention=5)
        remaining_names = sorted(f.name for f in backup_dir.iterdir())
        # Days 3-7 should survive (newest 5)
        for name in remaining_names:
            m = _BACKUP_PATTERN.match(name)
            assert m is not None
            day = int(m.group(1).split("T")[0].split("-")[-1])
            assert day >= 3

    def test_prune_noop_under_retention(self, backup_dir: Path):
        self._create_fake_backups(backup_dir, 3)
        deleted = _prune_old_backups(backup_dir, retention=5)
        assert deleted == []
        assert len(list(backup_dir.iterdir())) == 3

    def test_prune_zero_retention_is_noop(self, backup_dir: Path):
        self._create_fake_backups(backup_dir, 3)
        deleted = _prune_old_backups(backup_dir, retention=0)
        assert deleted == []

    def test_prune_ignores_non_backup_files(self, backup_dir: Path):
        self._create_fake_backups(backup_dir, 3)
        (backup_dir / "notes.txt").write_text("keep me")
        _prune_old_backups(backup_dir, retention=2)
        assert (backup_dir / "notes.txt").exists()


class TestRunBackup:
    def test_full_backup_cycle(self, sample_db: Path, backup_dir: Path):
        result = run_backup(sample_db, password="test", backup_dir=backup_dir, retention=5)

        assert result["ok"] is True
        assert result["error"] is None
        assert result["size_bytes"] > 0
        assert (backup_dir / result["filename"]).exists()

    def test_backup_creates_directory(self, sample_db: Path, tmp_path: Path):
        new_dir = tmp_path / "new" / "nested" / "backups"
        result = run_backup(sample_db, password="test", backup_dir=new_dir, retention=5)

        assert result["ok"] is True
        assert new_dir.exists()

    def test_backup_with_retention_prunes(self, sample_db: Path, backup_dir: Path):
        # Create 5 existing backups
        for i in range(5):
            name = f"orchestrator-backup-2026-01-{i + 1:02d}T12-00-00Z.zip"
            (backup_dir / name).write_text("fake")

        result = run_backup(sample_db, password="pw", backup_dir=backup_dir, retention=3)
        assert result["ok"] is True
        # 5 old + 1 new = 6, retain 3 → pruned 3
        assert len(result["pruned"]) == 3
        remaining = list(backup_dir.iterdir())
        assert len(remaining) == 3

    def test_backup_bad_db_path(self, tmp_path: Path, backup_dir: Path):
        result = run_backup(tmp_path / "no.db", password="pw", backup_dir=backup_dir)
        assert result["ok"] is False
        assert "not found" in result["error"].lower()


class TestListBackups:
    def test_list_empty_dir(self, backup_dir: Path):
        assert list_backups(backup_dir) == []

    def test_list_nonexistent_dir(self, tmp_path: Path):
        assert list_backups(tmp_path / "nope") == []

    def test_list_returns_newest_first(self, backup_dir: Path):
        for day in [3, 1, 2]:
            name = f"orchestrator-backup-2026-02-0{day}T12-00-00Z.zip"
            (backup_dir / name).write_bytes(b"x" * (day * 100))

        backups = list_backups(backup_dir)
        assert len(backups) == 3
        assert backups[0]["timestamp"] > backups[1]["timestamp"] > backups[2]["timestamp"]

    def test_list_ignores_non_backup_files(self, backup_dir: Path):
        (backup_dir / "random.zip").write_text("not a backup")
        (backup_dir / "orchestrator-backup-2026-02-01T12-00-00Z.zip").write_text("ok")

        backups = list_backups(backup_dir)
        assert len(backups) == 1


class TestBackupPattern:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("orchestrator-backup-2026-02-19T17-00-00Z.zip", True),
            ("orchestrator-backup-2026-01-01T00-00-00Z.zip", True),
            ("random-file.zip", False),
            ("orchestrator-backup-2026-02-19T17:00:00Z.zip", False),  # colons
            ("orchestrator-backup-.zip", False),
        ],
    )
    def test_pattern_matching(self, name: str, expected: bool):
        assert bool(_BACKUP_PATTERN.match(name)) is expected


# ---------------------------------------------------------------------------
# New tests: decrypt, validate, restore
# ---------------------------------------------------------------------------


class TestDecryptFromZip:
    def test_roundtrip_encrypt_decrypt(self, sample_db: Path, tmp_path: Path):
        """Encrypt a DB and decrypt it back — contents should match."""
        zip_path = tmp_path / "backup.zip"
        encrypt_to_zip(sample_db, zip_path, password="roundtrip")

        extracted = decrypt_from_zip(zip_path, password="roundtrip")
        try:
            conn = sqlite3.connect(str(extracted))
            rows = conn.execute("SELECT name FROM items ORDER BY name").fetchall()
            assert [r[0] for r in rows] == ["alpha", "beta"]
            conn.close()
        finally:
            extracted.unlink(missing_ok=True)

    def test_wrong_password_raises(self, sample_db: Path, tmp_path: Path):
        zip_path = tmp_path / "backup.zip"
        encrypt_to_zip(sample_db, zip_path, password="correct")

        with pytest.raises(RuntimeError):
            decrypt_from_zip(zip_path, password="wrong")

    def test_nonexistent_zip_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            decrypt_from_zip(tmp_path / "missing.zip", password="any")


class TestValidateSqliteDb:
    def test_valid_db(self, sample_db: Path):
        assert validate_sqlite_db(sample_db) is True

    def test_invalid_file(self, tmp_path: Path):
        bad = tmp_path / "bad.db"
        bad.write_text("this is not a database")
        assert validate_sqlite_db(bad) is False

    def test_nonexistent_path(self, tmp_path: Path):
        assert validate_sqlite_db(tmp_path / "nope.db") is False


class TestRestoreBackup:
    def _make_backup(self, db_path: Path, backup_dir: Path, password: str) -> str:
        """Helper: run a backup and return the filename."""
        result = run_backup(db_path, password=password, backup_dir=backup_dir, retention=10)
        assert result["ok"] is True
        return result["filename"]

    def test_full_restore_cycle(self, sample_db: Path, backup_dir: Path):
        """Backup → modify original → restore → verify original data returns."""
        password = "restore-test"
        filename = self._make_backup(sample_db, backup_dir, password)

        # Modify the original database
        conn = sqlite3.connect(str(sample_db))
        conn.execute("DELETE FROM items")
        conn.execute("INSERT INTO items (name) VALUES ('modified')")
        conn.commit()
        conn.close()

        # Verify modification took effect
        conn = sqlite3.connect(str(sample_db))
        rows = conn.execute("SELECT name FROM items ORDER BY name").fetchall()
        assert [r[0] for r in rows] == ["modified"]
        conn.close()

        # Restore from backup
        result = restore_backup(
            filename=filename,
            backup_dir=backup_dir,
            password=password,
            target_db_path=sample_db,
        )
        assert result["ok"] is True
        assert result["error"] is None
        assert result["pre_restore_backup"] is not None

        # Verify data is restored
        conn = sqlite3.connect(str(sample_db))
        rows = conn.execute("SELECT name FROM items ORDER BY name").fetchall()
        assert [r[0] for r in rows] == ["alpha", "beta"]
        conn.close()

    def test_invalid_filename_rejected(self, sample_db: Path, backup_dir: Path):
        result = restore_backup(
            filename="../../etc/passwd",
            backup_dir=backup_dir,
            password="any",
            target_db_path=sample_db,
        )
        assert result["ok"] is False
        assert "Invalid" in result["error"]

    def test_nonexistent_file(self, sample_db: Path, backup_dir: Path):
        result = restore_backup(
            filename="orchestrator-backup-2099-01-01T00-00-00Z.zip",
            backup_dir=backup_dir,
            password="any",
            target_db_path=sample_db,
        )
        assert result["ok"] is False
        assert "not found" in result["error"].lower()

    def test_wrong_password(self, sample_db: Path, backup_dir: Path):
        filename = self._make_backup(sample_db, backup_dir, password="correct")

        result = restore_backup(
            filename=filename,
            backup_dir=backup_dir,
            password="wrong",
            target_db_path=sample_db,
        )
        assert result["ok"] is False

    def test_pre_restore_backup_created(self, sample_db: Path, backup_dir: Path):
        password = "pre-restore"
        filename = self._make_backup(sample_db, backup_dir, password)

        result = restore_backup(
            filename=filename,
            backup_dir=backup_dir,
            password=password,
            target_db_path=sample_db,
        )
        assert result["ok"] is True
        pre_backup_name = result["pre_restore_backup"]
        assert pre_backup_name is not None
        pre_backup_path = sample_db.parent / pre_backup_name
        assert pre_backup_path.exists()

    def test_wal_shm_files_cleaned(self, sample_db: Path, backup_dir: Path):
        password = "wal-test"
        filename = self._make_backup(sample_db, backup_dir, password)

        # Create fake WAL and SHM files
        wal_path = sample_db.parent / (sample_db.name + "-wal")
        shm_path = sample_db.parent / (sample_db.name + "-shm")
        wal_path.write_text("fake wal")
        shm_path.write_text("fake shm")

        result = restore_backup(
            filename=filename,
            backup_dir=backup_dir,
            password=password,
            target_db_path=sample_db,
        )
        assert result["ok"] is True
        assert not wal_path.exists()
        assert not shm_path.exists()
