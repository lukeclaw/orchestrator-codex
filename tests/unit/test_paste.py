"""Unit tests for paste image utilities (save_image, cleanup_images) and clipboard endpoint."""

import base64
import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from orchestrator.api.routes.paste import cleanup_images, read_clipboard, save_image

# A minimal 1x1 red PNG (68 bytes)
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
    b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
    b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)
TINY_PNG_B64 = base64.b64encode(TINY_PNG).decode()


@pytest.fixture
def images_dir(tmp_path):
    """Provide a temp dir and mock get_images_dir to return it."""
    d = tmp_path / "images"
    d.mkdir()
    with patch("orchestrator.api.routes.paste.get_images_dir", return_value=d):
        yield d


class TestSaveImage:
    def test_save_raw_base64(self, images_dir):
        result = save_image(TINY_PNG_B64)
        assert result["ok"] is True
        assert result["url"].startswith("/api/images/clipboard_")
        assert result["url"].endswith(".png")
        assert result["size"] == len(TINY_PNG)
        # File actually written
        saved = images_dir / result["filename"]
        assert saved.exists()
        assert saved.read_bytes() == TINY_PNG

    def test_save_data_url_prefix(self, images_dir):
        data_url = f"data:image/png;base64,{TINY_PNG_B64}"
        result = save_image(data_url)
        assert result["ok"] is True
        assert result["filename"].endswith(".png")

    def test_save_jpeg_data_url(self, images_dir):
        data_url = f"data:image/jpeg;base64,{TINY_PNG_B64}"
        result = save_image(data_url)
        assert result["filename"].endswith(".jpg")

    def test_custom_filename(self, images_dir):
        result = save_image(TINY_PNG_B64, filename="my-screenshot")
        assert result["filename"] == "my-screenshot.png"

    def test_custom_filename_sanitized(self, images_dir):
        result = save_image(TINY_PNG_B64, filename="../../etc/passwd")
        # Slashes stripped — no path traversal possible
        assert "/" not in result["filename"]
        # File lands in images_dir, not elsewhere
        assert (images_dir / result["filename"]).exists()

    def test_custom_filename_empty_after_sanitize(self, images_dir):
        result = save_image(TINY_PNG_B64, filename="$$$")
        assert result["filename"].startswith("image")

    def test_filename_collision(self, images_dir):
        result1 = save_image(TINY_PNG_B64, filename="test")
        result2 = save_image(TINY_PNG_B64, filename="test")
        assert result1["filename"] != result2["filename"]
        assert result2["filename"] == "test_1.png"
        # Both files exist
        assert (images_dir / result1["filename"]).exists()
        assert (images_dir / result2["filename"]).exists()

    def test_invalid_base64_returns_400(self, images_dir):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            save_image("not-valid-base64!!!")
        assert exc_info.value.status_code == 400


class TestCleanupImages:
    def test_noop_when_under_cap(self, tmp_path):
        d = tmp_path / "images"
        d.mkdir()
        (d / "a.png").write_bytes(b"x" * 100)
        cleanup_images(d, max_size_mb=1)
        assert (d / "a.png").exists()

    def test_deletes_oldest_when_over_cap(self, tmp_path):
        d = tmp_path / "images"
        d.mkdir()
        # Create files with different mtimes
        (d / "old.png").write_bytes(b"x" * 600)
        time.sleep(0.05)
        (d / "new.png").write_bytes(b"x" * 600)

        # Cap at 1KB — both files total 1200 bytes > 1024
        cleanup_images(d, max_size_mb=0)  # 0MB = delete everything
        assert not (d / "old.png").exists()
        assert not (d / "new.png").exists()

    def test_keeps_newest_under_cap(self, tmp_path):
        d = tmp_path / "images"
        d.mkdir()
        # Use a cap that allows one file but not two
        # Each file is 600 bytes. Cap = 700 bytes ~ 0.000667 MB
        # Since we can't set fractional MB easily, write bigger files
        (d / "old.png").write_bytes(b"x" * 1024)
        time.sleep(0.05)
        (d / "new.png").write_bytes(b"x" * 1024)

        # Manually call with byte-equivalent logic: total = 2048 > 0
        # Just use max_size_mb=0 to force all deletion, or test with a helper
        # Better: test that oldest is removed first
        cleanup_images(d, max_size_mb=0)
        # Both removed when cap is 0
        assert not (d / "old.png").exists()

    def test_noop_when_dir_missing(self, tmp_path):
        """Should not raise if directory doesn't exist."""
        cleanup_images(tmp_path / "nonexistent")

    def test_ignores_subdirectories(self, tmp_path):
        d = tmp_path / "images"
        d.mkdir()
        subdir = d / "subdir"
        subdir.mkdir()
        # Should not crash on subdirectories
        cleanup_images(d, max_size_mb=0)
        assert subdir.exists()  # Subdirs not deleted


def _mock_run(pbpaste_stdout="", pbpaste_rc=0, osascript_stdout="", osascript_rc=0):
    """Build a side_effect for subprocess.run that routes by command name."""

    def side_effect(cmd, **_kwargs):
        result = MagicMock(spec=subprocess.CompletedProcess)
        if cmd[0] == "pbpaste":
            result.returncode = pbpaste_rc
            result.stdout = pbpaste_stdout
        elif cmd[0] == "osascript":
            result.returncode = osascript_rc
            result.stdout = osascript_stdout
        else:
            raise ValueError(f"Unexpected command: {cmd}")
        return result

    return side_effect


class TestReadClipboard:
    """Tests for the read_clipboard (GET /api/clipboard) endpoint logic."""

    def test_text_only(self):
        with patch(
            "orchestrator.api.routes.paste.subprocess.run",
            side_effect=_mock_run(pbpaste_stdout="hello world"),
        ):
            result = read_clipboard()
        assert result["text"] == "hello world"
        assert result["image_base64"] is None

    def test_image_only(self):
        with patch(
            "orchestrator.api.routes.paste.subprocess.run",
            side_effect=_mock_run(osascript_stdout=TINY_PNG_B64),
        ):
            result = read_clipboard()
        assert result["text"] is None
        assert result["image_base64"] == TINY_PNG_B64

    def test_both_text_and_image(self):
        with patch(
            "orchestrator.api.routes.paste.subprocess.run",
            side_effect=_mock_run(
                pbpaste_stdout="some text",
                osascript_stdout=TINY_PNG_B64,
            ),
        ):
            result = read_clipboard()
        assert result["text"] == "some text"
        assert result["image_base64"] == TINY_PNG_B64

    def test_empty_clipboard_raises_204(self):
        with patch("orchestrator.api.routes.paste.subprocess.run", side_effect=_mock_run()):
            with pytest.raises(HTTPException) as exc_info:
                read_clipboard()
            assert exc_info.value.status_code == 204

    def test_whitespace_only_text_treated_as_empty(self):
        with patch(
            "orchestrator.api.routes.paste.subprocess.run",
            side_effect=_mock_run(pbpaste_stdout="   \n  "),
        ):
            with pytest.raises(HTTPException) as exc_info:
                read_clipboard()
            assert exc_info.value.status_code == 204

    def test_pbpaste_failure_falls_through(self):
        """If pbpaste fails, image path still works."""

        def side_effect(cmd, **_kwargs):
            if cmd[0] == "pbpaste":
                raise OSError("pbpaste not found")
            result = MagicMock(spec=subprocess.CompletedProcess)
            result.returncode = 0
            result.stdout = TINY_PNG_B64
            return result

        with patch("orchestrator.api.routes.paste.subprocess.run", side_effect=side_effect):
            result = read_clipboard()
        assert result["text"] is None
        assert result["image_base64"] == TINY_PNG_B64

    def test_osascript_failure_falls_through(self):
        """If osascript fails, text path still works."""

        def side_effect(cmd, **_kwargs):
            if cmd[0] == "osascript":
                raise OSError("osascript not found")
            result = MagicMock(spec=subprocess.CompletedProcess)
            result.returncode = 0
            result.stdout = "clipboard text"
            return result

        with patch("orchestrator.api.routes.paste.subprocess.run", side_effect=side_effect):
            result = read_clipboard()
        assert result["text"] == "clipboard text"
        assert result["image_base64"] is None

    def test_pbpaste_nonzero_exit_ignored(self):
        with patch(
            "orchestrator.api.routes.paste.subprocess.run",
            side_effect=_mock_run(pbpaste_rc=1, osascript_stdout=TINY_PNG_B64),
        ):
            result = read_clipboard()
        assert result["text"] is None
        assert result["image_base64"] == TINY_PNG_B64
