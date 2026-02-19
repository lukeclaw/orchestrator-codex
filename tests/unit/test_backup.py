"""Unit tests for orchestrator.backup — snapshot, encrypt, retention, list."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pyzipper
import pytest

from orchestrator.backup import (
    _BACKUP_PATTERN,
    _prune_old_backups,
    create_db_snapshot,
    encrypt_to_zip,
    list_backups,
    run_backup,
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
    @pytest.mark.parametrize("name,expected", [
        ("orchestrator-backup-2026-02-19T17-00-00Z.zip", True),
        ("orchestrator-backup-2026-01-01T00-00-00Z.zip", True),
        ("random-file.zip", False),
        ("orchestrator-backup-2026-02-19T17:00:00Z.zip", False),  # colons
        ("orchestrator-backup-.zip", False),
    ])
    def test_pattern_matching(self, name: str, expected: bool):
        assert bool(_BACKUP_PATTERN.match(name)) is expected
