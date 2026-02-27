"""Unit tests for file explorer API endpoints."""

from __future__ import annotations

import json
import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from orchestrator.api.routes.files import (
    FileEntry,
    _apply_git_status,
    _detect_remote_work_dir,
    _get_git_status,
    _highest_severity,
    _human_size,
    _list_remote_dir,
    _read_remote_file,
    _remote_dir_cache,
    _resolve_session,
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
    def _ssh_ok(self, entries, git_available=True):
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"entries": entries, "git_available": git_available}),
            stderr="",
        )

    @patch("orchestrator.api.routes.files._run_ssh")
    def test_valid_listing(self, mock_ssh):
        mock_ssh.return_value = self._ssh_ok(
            [
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
            ]
        )
        entries, git_avail = _list_remote_dir("host", "/work", ".", False)
        assert len(entries) == 2
        assert entries[0].name == "src"
        assert entries[0].is_dir is True
        assert entries[1].size == 1024
        assert entries[1].human_size == "1.0KB"
        assert git_avail is True

    @patch("orchestrator.api.routes.files._run_ssh")
    def test_remote_error_json(self, mock_ssh):
        mock_ssh.return_value = MagicMock(
            returncode=1,
            stdout=json.dumps({"error": "Directory not found"}),
            stderr="",
        )
        with pytest.raises(HTTPException) as exc_info:
            _list_remote_dir("host", "/work", "bad/path", False)
        assert exc_info.value.status_code == 404

    @patch("orchestrator.api.routes.files._run_ssh")
    def test_ssh_timeout(self, mock_ssh):
        mock_ssh.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=15)
        with pytest.raises(HTTPException) as exc_info:
            _list_remote_dir("host", "/work", ".", False)
        assert exc_info.value.status_code == 504

    @patch("orchestrator.api.routes.files._run_ssh")
    def test_nonzero_return_no_json(self, mock_ssh):
        mock_ssh.return_value = MagicMock(
            returncode=1,
            stdout="not json",
            stderr="Connection refused",
        )
        with pytest.raises(HTTPException) as exc_info:
            _list_remote_dir("host", "/work", ".", False)
        assert exc_info.value.status_code == 502

    @patch("orchestrator.api.routes.files._run_ssh")
    def test_invalid_json_stdout(self, mock_ssh):
        mock_ssh.return_value = MagicMock(
            returncode=0,
            stdout="not json at all",
            stderr="",
        )
        with pytest.raises(HTTPException) as exc_info:
            _list_remote_dir("host", "/work", ".", False)
        assert exc_info.value.status_code == 502


class TestReadRemoteFile:
    @patch("orchestrator.api.routes.files._run_ssh")
    def test_read_text_file(self, mock_ssh):
        mock_ssh.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "content": "hello world\n",
                    "truncated": False,
                    "total_lines": 1,
                    "size": 12,
                    "binary": False,
                }
            ),
            stderr="",
        )
        resp = _read_remote_file("host", "/work", "hello.py", 500)
        assert resp.content == "hello world\n"
        assert resp.binary is False
        assert resp.language == "python"
        assert resp.truncated is False

    @patch("orchestrator.api.routes.files._run_ssh")
    def test_read_binary_file(self, mock_ssh):
        mock_ssh.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "content": "",
                    "truncated": False,
                    "total_lines": None,
                    "size": 4096,
                    "binary": True,
                }
            ),
            stderr="",
        )
        resp = _read_remote_file("host", "/work", "image.bin", 500)
        assert resp.binary is True
        assert resp.content == ""
        assert resp.language is None

    @patch("orchestrator.api.routes.files._run_ssh")
    def test_read_truncated_file(self, mock_ssh):
        mock_ssh.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "content": "line1\nline2\n",
                    "truncated": True,
                    "total_lines": 100,
                    "size": 500,
                    "binary": False,
                }
            ),
            stderr="",
        )
        resp = _read_remote_file("host", "/work", "big.txt", 2)
        assert resp.truncated is True
        assert resp.total_lines == 100

    @patch("orchestrator.api.routes.files._run_ssh")
    def test_ssh_timeout(self, mock_ssh):
        mock_ssh.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=15)
        with pytest.raises(HTTPException) as exc_info:
            _read_remote_file("host", "/work", "file.py", 500)
        assert exc_info.value.status_code == 504

    @patch("orchestrator.api.routes.files._run_ssh")
    def test_file_not_found(self, mock_ssh):
        mock_ssh.return_value = MagicMock(
            returncode=1,
            stdout=json.dumps({"error": "File not found"}),
            stderr="",
        )
        with pytest.raises(HTTPException) as exc_info:
            _read_remote_file("host", "/work", "missing.py", 500)
        assert exc_info.value.status_code == 404

    @patch("orchestrator.api.routes.files._run_ssh")
    def test_file_too_large(self, mock_ssh):
        mock_ssh.return_value = MagicMock(
            returncode=1,
            stdout=json.dumps({"error": "File too large (>5MB)", "code": 413}),
            stderr="",
        )
        with pytest.raises(HTTPException) as exc_info:
            _read_remote_file("host", "/work", "huge.bin", 500)
        assert exc_info.value.status_code == 413


class TestRemoteDirCache:
    def setup_method(self):
        _remote_dir_cache.clear()

    @patch("orchestrator.api.routes.files._list_remote_dir")
    def test_cache_hit_within_ttl(self, mock_list):
        entries = [FileEntry(name="a.py", path="a.py", is_dir=False, size=10)]
        _remote_dir_cache["host::/work::.::" + str(False) + "::1"] = (
            time.monotonic(),
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
        entries_old = [FileEntry(name="old.py", path="old.py", is_dir=False, size=10)]
        _remote_dir_cache["host::/work::.::" + str(False) + "::1"] = (
            time.monotonic() - 20,  # expired
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


class TestSshSemaphore:
    @patch("orchestrator.api.routes.files._run_ssh")
    def test_semaphore_limits_concurrency(self, mock_ssh):
        """Verify that the semaphore limits concurrent SSH calls to 3."""
        from orchestrator.api.routes.files import (
            _get_host_semaphore,
            _host_semaphores,
        )

        host = "test-sem-host"
        _host_semaphores.pop(host, None)
        sem = _get_host_semaphore(host)

        # Acquire all 3 slots
        assert sem.acquire(timeout=0)
        assert sem.acquire(timeout=0)
        assert sem.acquire(timeout=0)
        # 4th should fail immediately
        assert not sem.acquire(timeout=0)

        # Release one and try again
        sem.release()
        assert sem.acquire(timeout=0)

        # Clean up
        sem.release()
        sem.release()
        sem.release()
        _host_semaphores.pop(host, None)
