"""Unit tests for upload_utils — sanitize_filename, is_supported_file, save_uploaded_file."""

import os

import pytest

from orchestrator.api.upload_utils import (
    is_supported_file,
    sanitize_filename,
    save_uploaded_file,
)


class TestSanitizeFilename:
    def test_safe_name_unchanged(self):
        assert sanitize_filename("main.py") == "main.py"

    def test_path_traversal_stripped(self):
        assert sanitize_filename("../../etc/passwd.py") == "passwd.py"

    def test_shell_metacharacters(self):
        result = sanitize_filename(";rm -rf.sh")
        assert ";" not in result
        assert " " not in result
        assert result.endswith(".sh")

    def test_shell_metacharacters_with_slash(self):
        # os.path.basename splits on /, so ";rm -rf /.sh" → ".sh"
        # Then leading dot is stripped, leaving "sh"
        result = sanitize_filename(";rm -rf /.sh")
        assert ";" not in result
        assert "/" not in result

    def test_empty_after_sanitize(self):
        assert sanitize_filename("$$$") == "uploaded_file"

    def test_leading_dots_stripped(self):
        # Leading dots removed to prevent hidden files
        result = sanitize_filename("...test.txt")
        assert not result.startswith(".")
        assert "test" in result

    def test_leading_underscores_stripped(self):
        result = sanitize_filename("___test.txt")
        assert result == "test.txt"

    def test_safe_chars_preserved(self):
        assert sanitize_filename("my-file_v2.txt") == "my-file_v2.txt"

    def test_collapse_underscores(self):
        result = sanitize_filename("a   b   c.txt")
        assert "___" not in result
        assert result == "a_b_c.txt"

    def test_absolute_path(self):
        assert sanitize_filename("/tmp/secret/data.csv") == "data.csv"

    def test_windows_path(self):
        result = sanitize_filename("C:\\Users\\evil\\payload.py")
        # os.path.basename on Unix doesn't split backslashes, so they become underscores
        assert ".." not in result
        assert result.endswith(".py")


class TestIsSupportedFile:
    @pytest.mark.parametrize(
        "name",
        ["main.py", "index.ts", "data.json", "config.yaml", "style.css", "query.sql"],
    )
    def test_supported_extensions(self, name):
        assert is_supported_file(name) is True

    @pytest.mark.parametrize("name", ["DATA.JSON", "Main.PY", "INDEX.TS"])
    def test_case_insensitive(self, name):
        assert is_supported_file(name) is True

    @pytest.mark.parametrize("name", ["Dockerfile", "Makefile", "LICENSE"])
    def test_known_extensionless(self, name):
        assert is_supported_file(name) is True

    @pytest.mark.parametrize("name", [".gitignore", ".editorconfig", ".env"])
    def test_known_dotfiles(self, name):
        assert is_supported_file(name) is True

    @pytest.mark.parametrize(
        "name",
        ["photo.png", "image.jpg", "video.mp4", "app.exe", "archive.zip"],
    )
    def test_rejected_types(self, name):
        assert is_supported_file(name) is False

    def test_unknown_extensionless_rejected(self):
        assert is_supported_file("randomcommand") is False

    def test_unknown_dotfile_rejected(self):
        assert is_supported_file(".DS_Store") is False

    def test_compound_extension(self):
        # test.D.TS → last dot gives .ts (case-insensitive)
        assert is_supported_file("test.D.TS") is True


class TestSaveUploadedFile:
    def test_basic_save(self, tmp_path):
        content = b"hello world"
        dest = str(tmp_path / "upload_basic")
        path = save_uploaded_file(content, "test.py", dest)
        from pathlib import Path

        assert Path(path).exists()
        assert Path(path).read_bytes() == content
        assert os.path.basename(path) == "test.py"

    def test_creates_directory(self, tmp_path):
        dest = str(tmp_path / "sub" / "dir")
        path = save_uploaded_file(b"data", "file.txt", dest)
        from pathlib import Path

        assert Path(path).exists()

    def test_collision_handling(self, tmp_path):
        dest = str(tmp_path / "upload_coll")
        path1 = save_uploaded_file(b"first", "test.py", dest)
        path2 = save_uploaded_file(b"second", "test.py", dest)
        from pathlib import Path

        assert path1 != path2
        assert os.path.basename(path2) == "test_1.py"
        assert Path(path1).read_bytes() == b"first"
        assert Path(path2).read_bytes() == b"second"

    def test_collision_without_extension(self, tmp_path):
        dest = str(tmp_path / "upload_noext")
        path1 = save_uploaded_file(b"a", "Makefile", dest)
        path2 = save_uploaded_file(b"b", "Makefile", dest)
        assert os.path.basename(path1) == "Makefile"
        assert os.path.basename(path2) == "Makefile_1"

    def test_sanitized_filenames(self, tmp_path):
        dest = str(tmp_path / "upload_sanitize")
        path = save_uploaded_file(b"data", "../../etc/passwd.py", dest)
        assert os.path.basename(path) == "passwd.py"
        assert dest in path
