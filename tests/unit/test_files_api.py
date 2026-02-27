"""Unit tests for file explorer API endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from orchestrator.api.routes.files import (
    FileEntry,
    _apply_git_status,
    _get_git_status,
    _highest_severity,
    _human_size,
    _validate_path,
)


class TestPathValidation:
    def test_rejects_null_bytes(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _validate_path("foo\x00bar")
        assert exc_info.value.status_code == 400
        assert "null bytes" in exc_info.value.detail

    def test_rejects_absolute_path(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _validate_path("/etc/passwd")
        assert exc_info.value.status_code == 400
        assert "Absolute" in exc_info.value.detail

    def test_rejects_path_traversal(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _validate_path("foo/../../etc/passwd")
        assert exc_info.value.status_code == 400
        assert "traversal" in exc_info.value.detail

    def test_allows_normal_path(self):
        _validate_path("src/main.py")

    def test_allows_dot(self):
        _validate_path(".")

    def test_allows_nested_path(self):
        _validate_path("a/b/c/d.txt")


class TestHumanSize:
    def test_bytes(self):
        assert _human_size(0) == "0B"
        assert _human_size(500) == "500B"

    def test_kilobytes(self):
        result = _human_size(2048)
        assert "KB" in result

    def test_megabytes(self):
        result = _human_size(5 * 1024 * 1024)
        assert "MB" in result


class TestHighestSeverity:
    def test_conflicting_wins(self):
        assert _highest_severity(["modified", "conflicting", "added"]) == "conflicting"

    def test_deleted_over_modified(self):
        assert _highest_severity(["modified", "deleted"]) == "deleted"

    def test_single(self):
        assert _highest_severity(["untracked"]) == "untracked"

    def test_fallback(self):
        assert _highest_severity(["something_unknown"]) == "something_unknown"


class TestApplyGitStatus:
    def test_direct_match(self):
        entries = [
            FileEntry(name="main.py", path="main.py", is_dir=False, size=100),
        ]
        statuses = {"main.py": "modified"}
        _apply_git_status(entries, statuses)
        assert entries[0].git_status == "modified"

    def test_directory_propagation(self):
        entries = [
            FileEntry(name="src", path="src", is_dir=True, children_count=3),
        ]
        statuses = {"src/a.py": "modified", "src/b.py": "added"}
        _apply_git_status(entries, statuses)
        assert entries[0].git_status == "modified"  # higher severity

    def test_no_match(self):
        entries = [
            FileEntry(name="clean.py", path="clean.py", is_dir=False, size=50),
        ]
        _apply_git_status(entries, {"other.py": "modified"})
        assert entries[0].git_status is None


class TestGitStatusParsing:
    @patch("orchestrator.api.routes.files.subprocess.run")
    def test_parse_porcelain(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=" M src/main.py\0?? new_file.txt\0",
        )
        statuses, available = _get_git_status("/tmp/test", False)
        assert available is True
        assert statuses.get("src/main.py") == "modified"
        assert statuses.get("new_file.txt") == "untracked"

    @patch("orchestrator.api.routes.files.subprocess.run")
    def test_timeout_returns_unavailable(self, mock_run):
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=3)
        statuses, available = _get_git_status("/tmp/test_timeout", False)
        assert available is False
        assert statuses == {}

    @patch("orchestrator.api.routes.files.subprocess.run")
    def test_git_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        statuses, available = _get_git_status("/tmp/no_git", False)
        assert available is False

    @patch("orchestrator.api.routes.files.subprocess.run")
    def test_nonzero_return(self, mock_run):
        mock_run.return_value = MagicMock(returncode=128, stdout="")
        statuses, available = _get_git_status("/tmp/not_repo", False)
        assert available is False
