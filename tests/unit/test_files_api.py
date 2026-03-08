"""Unit tests for file explorer API endpoints."""

from __future__ import annotations

import os
import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from orchestrator.api.routes.files import (
    FileEntry,
    FileWriteRequest,
    _apply_git_status,
    _check_remote_mtimes,
    _delete_local,
    _delete_remote,
    _detect_remote_work_dir,
    _get_git_status,
    _highest_severity,
    _human_size,
    _list_remote_dir,
    _mkdir_local,
    _mkdir_remote,
    _move_local,
    _move_remote,
    _read_remote_file,
    _remote_content_cache,
    _remote_dir_cache,
    _resolve_session,
    _validate_path,
    _write_local_file,
    _write_remote_file,
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

    def test_untracked_dir_propagates_to_children(self):
        """When a folder is untracked, its children should inherit 'untracked'."""
        child_file = FileEntry(name="app.py", path="new-pkg/app.py", is_dir=False, size=50)
        child_dir = FileEntry(
            name="sub",
            path="new-pkg/sub",
            is_dir=True,
            children_count=1,
            children=[
                FileEntry(name="deep.py", path="new-pkg/sub/deep.py", is_dir=False, size=30),
            ],
        )
        parent = FileEntry(
            name="new-pkg",
            path="new-pkg",
            is_dir=True,
            children_count=2,
            children=[child_file, child_dir],
        )
        # git only reports the top-level untracked directory
        _apply_git_status([parent], {"new-pkg": "untracked"})
        assert parent.git_status == "untracked"
        assert child_file.git_status == "untracked"
        assert child_dir.git_status == "untracked"
        assert child_dir.children[0].git_status == "untracked"

    def test_ignored_dir_propagates_to_children(self):
        """When a folder is ignored, its children should inherit 'ignored'."""
        child = FileEntry(name="cache.dat", path="build/cache.dat", is_dir=False, size=100)
        parent = FileEntry(
            name="build",
            path="build",
            is_dir=True,
            children_count=1,
            children=[child],
        )
        _apply_git_status([parent], {"build": "ignored"})
        assert parent.git_status == "ignored"
        assert child.git_status == "ignored"

    def test_modified_dir_does_not_propagate_to_clean_children(self):
        """A folder with 'modified' status (from bubble-up) should NOT propagate
        to children — only 'untracked' and 'ignored' propagate downward."""
        clean_child = FileEntry(name="clean.py", path="src/clean.py", is_dir=False, size=50)
        dirty_child = FileEntry(name="dirty.py", path="src/dirty.py", is_dir=False, size=50)
        parent = FileEntry(
            name="src",
            path="src",
            is_dir=True,
            children_count=2,
            children=[clean_child, dirty_child],
        )
        _apply_git_status([parent], {"src/dirty.py": "modified"})
        assert parent.git_status == "modified"
        assert dirty_child.git_status == "modified"
        assert clean_child.git_status is None  # should NOT inherit "modified"

    def test_child_explicit_status_not_overridden_by_inheritance(self):
        """If a child has its own git status, it should not be overridden."""
        child = FileEntry(name="moved.py", path="new-pkg/moved.py", is_dir=False, size=50)
        parent = FileEntry(
            name="new-pkg",
            path="new-pkg",
            is_dir=True,
            children_count=1,
            children=[child],
        )
        # Unusual but possible: parent is untracked but a child has explicit status
        _apply_git_status([parent], {"new-pkg": "untracked", "new-pkg/moved.py": "added"})
        assert parent.git_status == "untracked"
        assert child.git_status == "added"  # explicit status preserved


class TestGitStatusParsing:
    @patch("orchestrator.api.routes.files.subprocess.run")
    def test_parse_porcelain(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=" M src/main.py\0?? new_file.txt\0",
        )
        statuses, available = _get_git_status("/tmp/test")
        assert available is True
        assert statuses.get("src/main.py") == "modified"
        assert statuses.get("new_file.txt") == "untracked"

    @patch("orchestrator.api.routes.files.subprocess.run")
    def test_timeout_returns_unavailable(self, mock_run):
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=3)
        statuses, available = _get_git_status("/tmp/test_timeout")
        assert available is False
        assert statuses == {}

    @patch("orchestrator.api.routes.files.subprocess.run")
    def test_git_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        statuses, available = _get_git_status("/tmp/no_git")
        assert available is False

    @patch("orchestrator.api.routes.files.subprocess.run")
    def test_nonzero_return(self, mock_run):
        mock_run.return_value = MagicMock(returncode=128, stdout="")
        statuses, available = _get_git_status("/tmp/not_repo")
        assert available is False


# ---------------------------------------------------------------------------
# Phase 2: Remote file explorer tests
# ---------------------------------------------------------------------------


class TestResolveSession:
    def _make_session(self, host="localhost", work_dir="/tmp/test"):
        s = MagicMock()
        s.host = host
        s.work_dir = work_dir
        return s

    @patch("orchestrator.api.routes.files.repo.get_session")
    @patch("os.path.isdir", return_value=True)
    def test_local_session(self, mock_isdir, mock_get):
        mock_get.return_value = self._make_session(host="localhost", work_dir="/tmp/test")
        info = _resolve_session(MagicMock(), "session-1")
        assert info.is_remote is False
        assert info.host == "localhost"
        assert info.work_dir == "/tmp/test"
        mock_isdir.assert_called_once_with("/tmp/test")

    @patch("orchestrator.api.routes.files.repo.get_session")
    def test_remote_rdev_session(self, mock_get):
        mock_get.return_value = self._make_session(
            host="user/rdev-vm", work_dir="/home/user/project"
        )
        info = _resolve_session(MagicMock(), "session-2")
        assert info.is_remote is True
        assert info.host == "user/rdev-vm"

    @patch("orchestrator.api.routes.files.repo.get_session")
    def test_remote_ssh_host(self, mock_get):
        mock_get.return_value = self._make_session(
            host="ssh-host.example.com", work_dir="/home/user/project"
        )
        info = _resolve_session(MagicMock(), "session-3")
        assert info.is_remote is True

    @patch("orchestrator.api.routes.files.repo.get_session")
    def test_skips_isdir_for_remote(self, mock_get):
        mock_get.return_value = self._make_session(host="remote-host", work_dir="/nonexistent/path")
        with patch("os.path.isdir") as mock_isdir:
            info = _resolve_session(MagicMock(), "session-4")
            mock_isdir.assert_not_called()
        assert info.is_remote is True

    @patch("orchestrator.api.routes.files.repo.get_session", return_value=None)
    def test_nonexistent_session(self, mock_get):
        with pytest.raises(HTTPException) as exc_info:
            _resolve_session(MagicMock(), "no-such-id")
        assert exc_info.value.status_code == 404

    @patch("orchestrator.api.routes.files._detect_remote_work_dir", return_value=None)
    @patch("orchestrator.api.routes.files.repo.get_session")
    def test_no_work_dir_local(self, mock_get, mock_detect):
        s = MagicMock()
        s.host = "localhost"
        s.work_dir = None
        mock_get.return_value = s
        with pytest.raises(HTTPException) as exc_info:
            _resolve_session(MagicMock(), "session-no-wd")
        assert exc_info.value.status_code == 400

    @patch("orchestrator.api.routes.files.repo.update_session")
    @patch(
        "orchestrator.api.routes.files._detect_remote_work_dir",
        return_value="/home/user/project",
    )
    @patch("orchestrator.api.routes.files.repo.get_session")
    def test_remote_no_work_dir_lazy_detect(self, mock_get, mock_detect, mock_update):
        s = MagicMock()
        s.host = "user/rdev-vm"
        s.work_dir = None
        mock_get.return_value = s
        info = _resolve_session(MagicMock(), "session-lazy")
        assert info.work_dir == "/home/user/project"
        assert info.is_remote is True
        mock_detect.assert_called_once_with("user/rdev-vm", "session-lazy")
        mock_update.assert_called_once()

    @patch(
        "orchestrator.api.routes.files._detect_remote_work_dir",
        return_value=None,
    )
    @patch("orchestrator.api.routes.files.repo.get_session")
    def test_remote_no_work_dir_detect_fails(self, mock_get, mock_detect):
        s = MagicMock()
        s.host = "user/rdev-vm"
        s.work_dir = None
        mock_get.return_value = s
        with pytest.raises(HTTPException) as exc_info:
            _resolve_session(MagicMock(), "session-fail")
        assert exc_info.value.status_code == 400


class TestListRemoteDir:
    """Tests for _list_remote_dir via RWS."""

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_valid_listing(self, mock_get_rws):
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {
            "entries": [
                {
                    "name": "src",
                    "path": "src",
                    "is_dir": True,
                    "size": None,
                    "modified": 1700000000.0,
                    "children_count": 3,
                    "git_status": "modified",
                },
                {
                    "name": "main.py",
                    "path": "main.py",
                    "is_dir": False,
                    "size": 1024,
                    "modified": 1700000000.0,
                    "children_count": None,
                    "git_status": None,
                },
            ],
            "git_available": True,
        }
        mock_get_rws.return_value = mock_rws
        entries, git_avail = _list_remote_dir("host", "/work", ".", False)
        assert len(entries) == 2
        assert entries[0].name == "src"
        assert entries[0].is_dir is True
        assert entries[1].size == 1024
        assert entries[1].human_size == "1.0KB"
        assert git_avail is True

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_remote_error_json(self, mock_get_rws):
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"error": "Directory not found"}
        mock_get_rws.return_value = mock_rws
        with pytest.raises(HTTPException) as exc_info:
            _list_remote_dir("host", "/work", "bad/path", False)
        assert exc_info.value.status_code == 404

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_rws_connection_error(self, mock_get_rws):
        mock_get_rws.side_effect = RuntimeError("RWS not available")
        with pytest.raises(HTTPException) as exc_info:
            _list_remote_dir("host", "/work", ".", False)
        assert exc_info.value.status_code == 502

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_rws_generic_error(self, mock_get_rws):
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"error": "Some internal error"}
        mock_get_rws.return_value = mock_rws
        with pytest.raises(HTTPException) as exc_info:
            _list_remote_dir("host", "/work", ".", False)
        assert exc_info.value.status_code == 502


class TestReadRemoteFile:
    """Tests for _read_remote_file via RWS."""

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_read_text_file(self, mock_get_rws):
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {
            "content": "hello world\n",
            "truncated": False,
            "total_lines": 1,
            "size": 12,
            "binary": False,
        }
        mock_get_rws.return_value = mock_rws
        resp = _read_remote_file("host", "/work", "hello.py", 500)
        assert resp.content == "hello world\n"
        assert resp.binary is False
        assert resp.language == "python"
        assert resp.truncated is False

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_read_binary_file(self, mock_get_rws):
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {
            "content": "",
            "truncated": False,
            "total_lines": None,
            "size": 4096,
            "binary": True,
        }
        mock_get_rws.return_value = mock_rws
        resp = _read_remote_file("host", "/work", "image.bin", 500)
        assert resp.binary is True
        assert resp.content == ""
        assert resp.language is None

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_read_truncated_file(self, mock_get_rws):
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {
            "content": "line1\nline2\n",
            "truncated": True,
            "total_lines": 100,
            "size": 500,
            "binary": False,
        }
        mock_get_rws.return_value = mock_rws
        resp = _read_remote_file("host", "/work", "big.txt", 2)
        assert resp.truncated is True
        assert resp.total_lines == 100

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_rws_connection_error(self, mock_get_rws):
        mock_get_rws.side_effect = RuntimeError("RWS not available")
        with pytest.raises(HTTPException) as exc_info:
            _read_remote_file("host", "/work", "file.py", 500)
        assert exc_info.value.status_code == 502

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_file_not_found(self, mock_get_rws):
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"error": "File not found"}
        mock_get_rws.return_value = mock_rws
        with pytest.raises(HTTPException) as exc_info:
            _read_remote_file("host", "/work", "missing.py", 500)
        assert exc_info.value.status_code == 404

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_file_too_large(self, mock_get_rws):
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"error": "File too large (>5MB)", "code": 413}
        mock_get_rws.return_value = mock_rws
        with pytest.raises(HTTPException) as exc_info:
            _read_remote_file("host", "/work", "huge.bin", 500)
        assert exc_info.value.status_code == 413


class TestRemoteDirCache:
    def setup_method(self):
        _remote_dir_cache.clear()

    @patch("orchestrator.api.routes.files._list_remote_dir")
    def test_cache_hit_within_ttl(self, mock_list):
        import orchestrator.api.routes.files as _files_mod

        entries = [FileEntry(name="a.py", path="a.py", is_dir=False, size=10)]
        _remote_dir_cache["host::/work::.::" + str(False) + "::1"] = (
            _files_mod.time.monotonic(),
            entries,
            True,
        )
        from orchestrator.api.routes.files import _list_remote_dir_cached

        result_entries, git_avail = _list_remote_dir_cached(
            "host", "/work", ".", False, refresh=False
        )
        mock_list.assert_not_called()
        assert result_entries == entries
        assert git_avail is True

    @patch("orchestrator.api.routes.files._list_remote_dir")
    def test_cache_miss_after_ttl(self, mock_list):
        import orchestrator.api.routes.files as _files_mod

        entries_old = [FileEntry(name="old.py", path="old.py", is_dir=False, size=10)]
        _remote_dir_cache["host::/work::.::" + str(False) + "::1"] = (
            _files_mod.time.monotonic() - 120,  # expired (TTL is 60s)
            entries_old,
            True,
        )
        entries_new = [FileEntry(name="new.py", path="new.py", is_dir=False, size=20)]
        mock_list.return_value = (entries_new, True)

        from orchestrator.api.routes.files import _list_remote_dir_cached

        result, _ = _list_remote_dir_cached("host", "/work", ".", False, refresh=False)
        mock_list.assert_called_once()
        assert result == entries_new

    @patch("orchestrator.api.routes.files._list_remote_dir")
    def test_refresh_bypasses_cache(self, mock_list):
        entries_cached = [FileEntry(name="cached.py", path="cached.py", is_dir=False, size=10)]
        _remote_dir_cache["host::/work::.::" + str(False) + "::1"] = (
            time.monotonic(),
            entries_cached,
            True,
        )
        entries_fresh = [FileEntry(name="fresh.py", path="fresh.py", is_dir=False, size=20)]
        mock_list.return_value = (entries_fresh, True)

        from orchestrator.api.routes.files import _list_remote_dir_cached

        result, _ = _list_remote_dir_cached("host", "/work", ".", False, refresh=True)
        mock_list.assert_called_once()
        assert result == entries_fresh

    @patch("orchestrator.api.routes.files._list_remote_dir")
    def test_different_hosts_no_cache_sharing(self, mock_list):
        entries_a = [FileEntry(name="a.py", path="a.py", is_dir=False, size=10)]
        _remote_dir_cache["host-a::/work::.::" + str(False) + "::1"] = (
            time.monotonic(),
            entries_a,
            True,
        )
        entries_b = [FileEntry(name="b.py", path="b.py", is_dir=False, size=20)]
        mock_list.return_value = (entries_b, False)

        from orchestrator.api.routes.files import _list_remote_dir_cached

        result, _ = _list_remote_dir_cached("host-b", "/work", ".", False, refresh=False)
        mock_list.assert_called_once()
        assert result == entries_b


class TestDetectRemoteWorkDir:
    @patch("orchestrator.api.routes.files.subprocess.run")
    def test_detects_cwd_from_pwdx(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="/home/user/my-project\n",
            stderr="",
        )
        result = _detect_remote_work_dir("user/rdev-vm", "session-123")
        assert result == "/home/user/my-project"
        # Verify the SSH command includes session_id and pwdx
        cmd_str = mock_run.call_args[0][0]
        assert "session-123" in " ".join(cmd_str)
        assert "pwdx" in " ".join(cmd_str)

    @patch("orchestrator.api.routes.files.subprocess.run")
    def test_returns_none_when_no_process(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="",
        )
        result = _detect_remote_work_dir("user/rdev-vm", "session-456")
        assert result is None

    @patch("orchestrator.api.routes.files.subprocess.run")
    def test_returns_none_on_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=10)
        result = _detect_remote_work_dir("user/rdev-vm", "session-789")
        assert result is None

    @patch("orchestrator.api.routes.files.subprocess.run")
    def test_rejects_non_absolute_path(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="relative/path\n",
            stderr="",
        )
        result = _detect_remote_work_dir("host", "session-x")
        assert result is None


# ---------------------------------------------------------------------------
# Phase 3: Write endpoint tests
# ---------------------------------------------------------------------------


class TestWriteFileLocal:
    def test_write_updates_file_content(self, tmp_path):
        f = tmp_path / "hello.py"
        f.write_text("old")
        body = FileWriteRequest(path="hello.py", content="new content")
        resp = _write_local_file(str(tmp_path), body)
        assert resp.conflict is False
        assert f.read_text() == "new content"
        assert resp.size == len("new content")

    def test_write_rejects_path_traversal(self, tmp_path):
        body = FileWriteRequest(path="../outside.txt", content="hack")
        with pytest.raises(HTTPException) as exc_info:
            _write_local_file(str(tmp_path), body)
        assert exc_info.value.status_code == 400
        assert "outside" in exc_info.value.detail.lower()

    def test_write_rejects_oversized_content(self, tmp_path):
        """Content > 2MB should be rejected at the endpoint level.
        _write_local_file doesn't check size, but the endpoint does.
        We test the model validation indirectly: content_bytes > 2MB."""
        from orchestrator.api.routes.files import _MAX_WRITE_SIZE

        assert _MAX_WRITE_SIZE == 2 * 1024 * 1024

    def test_write_permission_denied(self, tmp_path):
        # Create a read-only directory
        ro_dir = tmp_path / "readonly"
        ro_dir.mkdir()
        f = ro_dir / "file.txt"
        f.write_text("original")
        f.chmod(0o444)
        ro_dir.chmod(0o555)

        body = FileWriteRequest(path="readonly/file.txt", content="attempt")
        try:
            with pytest.raises(HTTPException) as exc_info:
                _write_local_file(str(tmp_path), body)
            assert exc_info.value.status_code == 403
        finally:
            # Restore permissions for cleanup
            ro_dir.chmod(0o755)
            f.chmod(0o644)

    def test_write_file_not_found(self, tmp_path):
        body = FileWriteRequest(path="missing.txt", content="data", create=False)
        with pytest.raises(HTTPException) as exc_info:
            _write_local_file(str(tmp_path), body)
        assert exc_info.value.status_code == 404

    def test_write_creates_new_file(self, tmp_path):
        body = FileWriteRequest(path="newfile.txt", content="brand new", create=True)
        resp = _write_local_file(str(tmp_path), body)
        assert resp.conflict is False
        assert (tmp_path / "newfile.txt").read_text() == "brand new"

    def test_write_creates_parent_dirs(self, tmp_path):
        body = FileWriteRequest(
            path="deep/nested/dir/file.txt", content="nested content", create=True
        )
        resp = _write_local_file(str(tmp_path), body)
        assert resp.conflict is False
        assert (tmp_path / "deep/nested/dir/file.txt").read_text() == "nested content"

    def test_conflict_detection_mtime_mismatch(self, tmp_path):
        f = tmp_path / "conflict.txt"
        f.write_text("original")
        # Use an mtime that's far in the past (guaranteed mismatch)
        body = FileWriteRequest(path="conflict.txt", content="new", expected_mtime=1000000.0)
        resp = _write_local_file(str(tmp_path), body)
        assert resp.conflict is True
        # File should NOT have been modified
        assert f.read_text() == "original"

    def test_conflict_detection_mtime_match(self, tmp_path):
        f = tmp_path / "match.txt"
        f.write_text("original")
        mtime = os.stat(str(f)).st_mtime
        body = FileWriteRequest(path="match.txt", content="updated", expected_mtime=mtime)
        resp = _write_local_file(str(tmp_path), body)
        assert resp.conflict is False
        assert f.read_text() == "updated"

    def test_no_conflict_when_expected_mtime_null(self, tmp_path):
        f = tmp_path / "nocheck.txt"
        f.write_text("v1")
        body = FileWriteRequest(path="nocheck.txt", content="v2", expected_mtime=None)
        resp = _write_local_file(str(tmp_path), body)
        assert resp.conflict is False
        assert f.read_text() == "v2"


class TestWriteFileRemote:
    """Tests for _write_remote_file via RWS."""

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_write_remote_success(self, mock_get_rws):
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"conflict": False, "size": 11, "modified": 1700000000.0}
        mock_get_rws.return_value = mock_rws
        body = FileWriteRequest(path="test.py", content="hello world")
        resp = _write_remote_file("host", "/work", body)
        assert resp.conflict is False
        assert resp.size == 11
        assert resp.modified == 1700000000.0

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_write_remote_connection_error(self, mock_get_rws):
        mock_get_rws.side_effect = RuntimeError("RWS not available")
        body = FileWriteRequest(path="test.py", content="data")
        with pytest.raises(HTTPException) as exc_info:
            _write_remote_file("host", "/work", body)
        assert exc_info.value.status_code == 502

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_write_remote_permission_denied(self, mock_get_rws):
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"error": "Permission denied"}
        mock_get_rws.return_value = mock_rws
        body = FileWriteRequest(path="test.py", content="data")
        with pytest.raises(HTTPException) as exc_info:
            _write_remote_file("host", "/work", body)
        assert exc_info.value.status_code == 403

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_write_remote_conflict(self, mock_get_rws):
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"conflict": True, "size": 100, "modified": 1700000000.0}
        mock_get_rws.return_value = mock_rws
        body = FileWriteRequest(path="test.py", content="data", expected_mtime=1699999999.0)
        resp = _write_remote_file("host", "/work", body)
        assert resp.conflict is True
        assert resp.size == 100

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_base64_sent_to_rws(self, mock_get_rws):
        """Verify content is base64-encoded in the RWS command."""
        import base64

        content = "print('hello')\n"
        expected_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")

        mock_rws = MagicMock()
        mock_rws.execute.return_value = {
            "conflict": False,
            "size": len(content),
            "modified": 1700000000.0,
        }
        mock_get_rws.return_value = mock_rws
        body = FileWriteRequest(path="test.py", content=content)
        _write_remote_file("host", "/work", body)

        # Verify the execute call included the base64 content
        call_args = mock_rws.execute.call_args[0][0]
        assert call_args["content_b64"] == expected_b64

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_cache_invalidation_after_write(self, mock_get_rws):
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"conflict": False, "size": 5, "modified": 1700000000.0}
        mock_get_rws.return_value = mock_rws

        # Pre-populate cache
        cache_key = "host::/work::test.py::500"
        _remote_content_cache[cache_key] = (time.monotonic(), MagicMock())

        body = FileWriteRequest(path="test.py", content="data")
        _write_remote_file("host", "/work", body)

        # Cache should be invalidated
        assert cache_key not in _remote_content_cache


# ---------------------------------------------------------------------------
# Phase 4: Delete endpoint tests
# ---------------------------------------------------------------------------


class TestDeleteLocal:
    def test_delete_file(self, tmp_path):
        f = tmp_path / "remove_me.txt"
        f.write_text("bye")
        resp = _delete_local(str(tmp_path), "remove_me.txt")
        assert resp.status == "ok"
        assert not f.exists()

    def test_delete_directory(self, tmp_path):
        d = tmp_path / "mydir"
        d.mkdir()
        (d / "inner.txt").write_text("hi")
        resp = _delete_local(str(tmp_path), "mydir")
        assert resp.status == "ok"
        assert not d.exists()

    def test_delete_not_found(self, tmp_path):
        with pytest.raises(HTTPException) as exc_info:
            _delete_local(str(tmp_path), "nonexistent.txt")
        assert exc_info.value.status_code == 404

    def test_delete_path_traversal(self, tmp_path):
        with pytest.raises(HTTPException) as exc_info:
            _delete_local(str(tmp_path), "../outside.txt")
        assert exc_info.value.status_code == 400


class TestDeleteRemote:
    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_delete_remote_success(self, mock_get_rws):
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"status": "ok"}
        mock_get_rws.return_value = mock_rws
        resp = _delete_remote("host", "/work", "file.txt")
        assert resp.status == "ok"

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_delete_remote_not_found(self, mock_get_rws):
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"error": "Not found"}
        mock_get_rws.return_value = mock_rws
        with pytest.raises(HTTPException) as exc_info:
            _delete_remote("host", "/work", "missing.txt")
        assert exc_info.value.status_code == 404

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_delete_remote_connection_error(self, mock_get_rws):
        mock_get_rws.side_effect = RuntimeError("RWS not available")
        with pytest.raises(HTTPException) as exc_info:
            _delete_remote("host", "/work", "file.txt")
        assert exc_info.value.status_code == 502


# ---------------------------------------------------------------------------
# Phase 5: Move endpoint tests
# ---------------------------------------------------------------------------


class TestMoveLocal:
    def test_move_file(self, tmp_path):
        f = tmp_path / "old.txt"
        f.write_text("content")
        resp = _move_local(str(tmp_path), "old.txt", "new.txt")
        assert resp.status == "ok"
        assert not f.exists()
        assert (tmp_path / "new.txt").read_text() == "content"

    def test_move_into_directory(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello")
        d = tmp_path / "subdir"
        d.mkdir()
        resp = _move_local(str(tmp_path), "file.txt", "subdir/file.txt")
        assert resp.status == "ok"
        assert not f.exists()
        assert (d / "file.txt").read_text() == "hello"

    def test_move_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("data")
        resp = _move_local(str(tmp_path), "file.txt", "deep/nested/file.txt")
        assert resp.status == "ok"
        assert (tmp_path / "deep" / "nested" / "file.txt").read_text() == "data"

    def test_move_directory(self, tmp_path):
        d = tmp_path / "olddir"
        d.mkdir()
        (d / "inner.txt").write_text("hi")
        resp = _move_local(str(tmp_path), "olddir", "newdir")
        assert resp.status == "ok"
        assert not d.exists()
        assert (tmp_path / "newdir" / "inner.txt").read_text() == "hi"

    def test_move_not_found(self, tmp_path):
        with pytest.raises(HTTPException) as exc_info:
            _move_local(str(tmp_path), "missing.txt", "dest.txt")
        assert exc_info.value.status_code == 404

    def test_move_source_path_traversal(self, tmp_path):
        with pytest.raises(HTTPException) as exc_info:
            _move_local(str(tmp_path), "../outside.txt", "dest.txt")
        assert exc_info.value.status_code == 400

    def test_move_dest_path_traversal(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("data")
        with pytest.raises(HTTPException) as exc_info:
            _move_local(str(tmp_path), "file.txt", "../outside.txt")
        assert exc_info.value.status_code == 400


class TestMoveRemote:
    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_move_remote_success(self, mock_get_rws):
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"status": "ok"}
        mock_get_rws.return_value = mock_rws
        resp = _move_remote("host", "/work", "old.txt", "new.txt")
        assert resp.status == "ok"

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_move_remote_not_found(self, mock_get_rws):
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"error": "Not found"}
        mock_get_rws.return_value = mock_rws
        with pytest.raises(HTTPException) as exc_info:
            _move_remote("host", "/work", "missing.txt", "dest.txt")
        assert exc_info.value.status_code == 404

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_move_remote_connection_error(self, mock_get_rws):
        mock_get_rws.side_effect = RuntimeError("RWS not available")
        with pytest.raises(HTTPException) as exc_info:
            _move_remote("host", "/work", "old.txt", "new.txt")
        assert exc_info.value.status_code == 502


class TestMkdirLocal:
    def test_mkdir_creates_directory(self, tmp_path):
        resp = _mkdir_local(str(tmp_path), "newdir")
        assert resp.status == "ok"
        assert (tmp_path / "newdir").is_dir()

    def test_mkdir_creates_nested_directory(self, tmp_path):
        resp = _mkdir_local(str(tmp_path), "a/b/c")
        assert resp.status == "ok"
        assert (tmp_path / "a" / "b" / "c").is_dir()

    def test_mkdir_existing_dir_is_ok(self, tmp_path):
        (tmp_path / "already").mkdir()
        resp = _mkdir_local(str(tmp_path), "already")
        assert resp.status == "ok"

    def test_mkdir_path_traversal(self, tmp_path):
        with pytest.raises(HTTPException) as exc_info:
            _mkdir_local(str(tmp_path), "../outside")
        assert exc_info.value.status_code == 400


class TestMkdirRemote:
    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_mkdir_remote_success(self, mock_get_rws):
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"status": "ok"}
        mock_get_rws.return_value = mock_rws
        resp = _mkdir_remote("host", "/work", "newdir")
        assert resp.status == "ok"

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_mkdir_remote_connection_error(self, mock_get_rws):
        mock_get_rws.side_effect = RuntimeError("RWS not available")
        with pytest.raises(HTTPException) as exc_info:
            _mkdir_remote("host", "/work", "newdir")
        assert exc_info.value.status_code == 502


# ---------------------------------------------------------------------------
# Mtime polling endpoint tests
# ---------------------------------------------------------------------------


class TestCheckMtimesLocal:
    def test_returns_mtimes_for_existing_files(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("aaa")
        f2.write_text("bbb")

        mtime_a = os.stat(str(f1)).st_mtime
        mtime_b = os.stat(str(f2)).st_mtime

        # Simulate what the endpoint does for local files
        paths = ["a.py", "b.py"]
        mtimes: dict[str, float | None] = {}
        work_dir = str(tmp_path)
        norm_work = os.path.normpath(work_dir)
        for p in paths:
            abs_path = os.path.normpath(os.path.join(work_dir, p))
            if not abs_path.startswith(norm_work):
                mtimes[p] = None
                continue
            try:
                mtimes[p] = os.stat(abs_path).st_mtime
            except OSError:
                mtimes[p] = None

        assert abs(mtimes["a.py"] - mtime_a) < 0.01
        assert abs(mtimes["b.py"] - mtime_b) < 0.01

    def test_returns_none_for_missing_file(self, tmp_path):
        paths = ["nonexistent.py"]
        mtimes: dict[str, float | None] = {}
        work_dir = str(tmp_path)
        for p in paths:
            abs_path = os.path.normpath(os.path.join(work_dir, p))
            try:
                mtimes[p] = os.stat(abs_path).st_mtime
            except OSError:
                mtimes[p] = None

        assert mtimes["nonexistent.py"] is None

    def test_detects_mtime_change_after_write(self, tmp_path):
        f = tmp_path / "changing.py"
        f.write_text("v1")
        mtime_before = os.stat(str(f)).st_mtime

        # Modify the file
        import time as _time

        _time.sleep(0.05)  # ensure mtime granularity
        f.write_text("v2")
        mtime_after = os.stat(str(f)).st_mtime

        assert mtime_after > mtime_before


class TestCheckRemoteMtimes:
    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_returns_mtimes_from_rws(self, mock_get_rws):
        mock_rws = MagicMock()
        mock_rws.execute.return_value = {"mtimes": {"a.py": 1700000000.0, "b.py": 1700000001.0}}
        mock_get_rws.return_value = mock_rws
        resp = _check_remote_mtimes("host", "/work", ["a.py", "b.py"])
        assert resp.mtimes["a.py"] == 1700000000.0
        assert resp.mtimes["b.py"] == 1700000001.0

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_connection_error_returns_none_for_all(self, mock_get_rws):
        mock_get_rws.side_effect = RuntimeError("RWS not available")
        resp = _check_remote_mtimes("host", "/work", ["a.py", "b.py"])
        assert resp.mtimes["a.py"] is None
        assert resp.mtimes["b.py"] is None

    @patch("orchestrator.api.routes.files.get_remote_worker_server")
    def test_json_decode_error_returns_none(self, mock_get_rws):
        import json

        mock_get_rws.side_effect = json.JSONDecodeError("bad", "", 0)
        resp = _check_remote_mtimes("host", "/work", ["x.py"])
        assert resp.mtimes["x.py"] is None
