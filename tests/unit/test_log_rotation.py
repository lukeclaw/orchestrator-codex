"""Tests for log file rotation in setup_logging().

Verifies that the log handler uses RotatingFileHandler with the correct
size cap, and that existing oversized log files are handled gracefully
(rotated on next write rather than lost).
"""

import logging
from logging.handlers import RotatingFileHandler
from unittest.mock import patch

from orchestrator.main import setup_logging


def _get_file_handler(caplog_handler=None):
    """Call setup_logging() and return the RotatingFileHandler it installs."""
    # Prevent basicConfig from being a no-op by clearing existing handlers
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    root.handlers.clear()
    try:
        with patch("orchestrator.paths.is_packaged", return_value=True):
            setup_logging({"logging": {"level": "INFO"}})
        handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert handlers, "setup_logging() must install a RotatingFileHandler"
        return handlers[0]
    finally:
        # Restore original handlers to avoid polluting other tests
        for h in root.handlers[:]:
            root.handlers.remove(h)
        root.handlers.extend(original_handlers)


class TestLogRotationConfig:
    """Verify the RotatingFileHandler is configured correctly."""

    def test_handler_is_rotating(self, tmp_path):
        with patch("orchestrator.paths.log_path", return_value=tmp_path / "test.log"):
            handler = _get_file_handler()
        assert isinstance(handler, RotatingFileHandler)

    def test_max_bytes(self, tmp_path):
        with patch("orchestrator.paths.log_path", return_value=tmp_path / "test.log"):
            handler = _get_file_handler()
        assert handler.maxBytes == 15_000_000

    def test_backup_count(self, tmp_path):
        with patch("orchestrator.paths.log_path", return_value=tmp_path / "test.log"):
            handler = _get_file_handler()
        assert handler.backupCount == 1

    def test_total_cap_is_30mb(self, tmp_path):
        """maxBytes * (1 + backupCount) == 30 MB total cap."""
        with patch("orchestrator.paths.log_path", return_value=tmp_path / "test.log"):
            handler = _get_file_handler()
        total = handler.maxBytes * (1 + handler.backupCount)
        assert total == 30_000_000


class TestLogRotationBehavior:
    """Verify rotation actually works at the file level."""

    def test_rotation_creates_backup(self, tmp_path):
        """When the log exceeds maxBytes, a .1 backup is created."""
        log_file = tmp_path / "app.log"
        handler = RotatingFileHandler(str(log_file), maxBytes=1000, backupCount=1)
        handler.setFormatter(logging.Formatter("%(message)s"))

        # Write enough to trigger rotation
        record = logging.LogRecord("test", logging.INFO, "", 0, "x" * 600, (), None)
        handler.emit(record)  # ~600 bytes
        handler.emit(record)  # crosses 1000 → triggers rotation
        handler.close()

        assert log_file.exists()
        backup = tmp_path / "app.log.1"
        assert backup.exists(), "backup file should be created on rotation"

    def test_only_one_backup_kept(self, tmp_path):
        """With backupCount=1, at most one .1 file exists; no .2."""
        log_file = tmp_path / "app.log"
        handler = RotatingFileHandler(str(log_file), maxBytes=500, backupCount=1)
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord("test", logging.INFO, "", 0, "x" * 400, (), None)
        # Trigger multiple rotations
        for _ in range(10):
            handler.emit(record)
        handler.close()

        assert log_file.exists()
        assert (tmp_path / "app.log.1").exists()
        assert not (tmp_path / "app.log.2").exists()

    def test_oversized_legacy_file_rotated_on_first_write(self, tmp_path):
        """An existing log file larger than maxBytes gets rotated
        (renamed to .1) on the very first write — no data is lost."""
        log_file = tmp_path / "app.log"
        # Simulate a legacy 50 MB file
        legacy_content = b"old log line\n" * 100_000  # ~1.3 MB stand-in
        log_file.write_bytes(legacy_content)

        handler = RotatingFileHandler(str(log_file), maxBytes=1000, backupCount=1)
        handler.setFormatter(logging.Formatter("%(message)s"))

        # First write should trigger rotation of the oversized file
        record = logging.LogRecord("test", logging.INFO, "", 0, "new entry", (), None)
        handler.emit(record)
        handler.close()

        backup = tmp_path / "app.log.1"
        assert backup.exists(), "oversized file should be rotated to .1"
        assert backup.stat().st_size == len(legacy_content)
        # Active log now only contains the new entry
        assert log_file.stat().st_size < 1000

    def test_oversized_backup_replaced_on_next_rotation(self, tmp_path):
        """After an oversized legacy file becomes .1, the next rotation
        replaces it with a normally-sized backup."""
        log_file = tmp_path / "app.log"
        legacy_content = b"old log line\n" * 100_000
        log_file.write_bytes(legacy_content)

        handler = RotatingFileHandler(str(log_file), maxBytes=500, backupCount=1)
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord("test", logging.INFO, "", 0, "x" * 400, (), None)
        # First write rotates the oversized file; subsequent writes fill
        # the new log and trigger another rotation that replaces the
        # oversized .1 with a normal-sized one.
        for _ in range(5):
            handler.emit(record)
        handler.close()

        backup = tmp_path / "app.log.1"
        assert backup.exists()
        # The oversized legacy backup should now be replaced
        assert backup.stat().st_size < len(legacy_content)
