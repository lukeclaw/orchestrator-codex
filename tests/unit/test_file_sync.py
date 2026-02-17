"""Unit tests for terminal/file_sync.py."""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from orchestrator.terminal.file_sync import (
    get_worker_tmp_dir,
    sync_file_to_remote,
    sync_file_from_remote,
    sync_dir_to_remote,
)


class TestGetWorkerTmpDir:
    def test_returns_expected_path(self):
        assert get_worker_tmp_dir("w1") == "/tmp/orchestrator/workers/w1/tmp"

    def test_different_names(self):
        assert get_worker_tmp_dir("brain") == "/tmp/orchestrator/workers/brain/tmp"
        assert get_worker_tmp_dir("my-worker") == "/tmp/orchestrator/workers/my-worker/tmp"


class TestSyncFileToRemote:
    @patch("orchestrator.terminal.file_sync.subprocess.run")
    def test_success(self, mock_run, tmp_path):
        local_file = tmp_path / "test.png"
        local_file.write_bytes(b"image-data")

        mock_run.return_value = MagicMock(returncode=0, stderr=b"")

        result = sync_file_to_remote(
            str(local_file), "user/rdev-vm", "/tmp/orchestrator/workers/w1/tmp/test.png"
        )
        assert result is True
        assert mock_run.call_count == 2  # mkdir + ssh cat

        # First call: ssh mkdir
        mkdir_call = mock_run.call_args_list[0]
        cmd = mkdir_call[0][0]
        assert cmd[0] == "ssh"
        assert "mkdir -p" in " ".join(cmd)

        # Second call: ssh cat (not scp — scp breaks with / in hostname)
        cat_call = mock_run.call_args_list[1]
        cmd = cat_call[0][0]
        assert cmd[0] == "ssh"
        assert "cat >" in " ".join(cmd)

    @patch("orchestrator.terminal.file_sync.subprocess.run")
    def test_never_uses_scp_with_rdev_host(self, mock_run, tmp_path):
        """scp misparses hostnames with '/' (e.g. user/rdev-vm) — we must use ssh cat."""
        local_file = tmp_path / "img.png"
        local_file.write_bytes(b"data")
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")

        sync_file_to_remote(
            str(local_file), "user/rdev-vm", "/tmp/orchestrator/workers/w1/tmp/img.png"
        )

        for call in mock_run.call_args_list:
            cmd = call[0][0]
            assert cmd[0] != "scp", (
                f"scp must not be used — it misparses hosts containing '/': {cmd}"
            )

    @patch("orchestrator.terminal.file_sync.subprocess.run")
    def test_mkdir_fails(self, mock_run, tmp_path):
        local_file = tmp_path / "test.png"
        local_file.write_bytes(b"data")

        mock_run.return_value = MagicMock(returncode=1, stderr="Permission denied")

        result = sync_file_to_remote(str(local_file), "host", "/remote/path/file.png")
        assert result is False
        assert mock_run.call_count == 1  # only mkdir, stops on failure

    @patch("orchestrator.terminal.file_sync.subprocess.run")
    def test_ssh_cat_fails(self, mock_run, tmp_path):
        local_file = tmp_path / "test.png"
        local_file.write_bytes(b"data")

        # mkdir succeeds, ssh cat fails
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=1, stderr=b"Connection refused"),
        ]

        result = sync_file_to_remote(str(local_file), "host", "/remote/path/file.png")
        assert result is False

    @patch("orchestrator.terminal.file_sync.subprocess.run")
    def test_timeout(self, mock_run, tmp_path):
        local_file = tmp_path / "test.png"
        local_file.write_bytes(b"data")

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=30)

        result = sync_file_to_remote(str(local_file), "host", "/remote/path/file.png")
        assert result is False


class TestSyncFileFromRemote:
    @patch("orchestrator.terminal.file_sync.subprocess.run")
    def test_success(self, mock_run, tmp_path):
        dest = tmp_path / "subdir" / "file.png"
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")

        result = sync_file_from_remote("host", "/remote/file.png", str(dest))
        assert result is True
        # local dir was created
        assert dest.parent.exists()

    @patch("orchestrator.terminal.file_sync.subprocess.run")
    def test_never_uses_scp_with_rdev_host(self, mock_run, tmp_path):
        """scp misparses hostnames with '/' (e.g. user/rdev-vm) — we must use ssh cat."""
        dest = tmp_path / "subdir2" / "img.png"
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")

        sync_file_from_remote(
            "user/rdev-vm", "/tmp/orchestrator/workers/w1/tmp/img.png", str(dest)
        )

        for call in mock_run.call_args_list:
            cmd = call[0][0]
            assert cmd[0] != "scp", (
                f"scp must not be used — it misparses hosts containing '/': {cmd}"
            )

    @patch("orchestrator.terminal.file_sync.subprocess.run")
    def test_ssh_cat_fails(self, mock_run, tmp_path):
        dest = tmp_path / "file.png"
        mock_run.return_value = MagicMock(returncode=1, stderr=b"No such file")

        result = sync_file_from_remote("host", "/remote/file.png", str(dest))
        assert result is False
        # Partial file cleaned up
        assert not dest.exists()

    @patch("orchestrator.terminal.file_sync.subprocess.run")
    def test_timeout(self, mock_run, tmp_path):
        dest = tmp_path / "file.png"
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=60)

        result = sync_file_from_remote("host", "/remote/file.png", str(dest))
        assert result is False


class TestSyncDirToRemote:
    @patch("orchestrator.terminal.session._copy_dir_to_rdev_ssh")
    def test_delegates_to_session(self, mock_copy):
        mock_copy.return_value = True
        result = sync_dir_to_remote("/local/dir", "host", "/remote/dir")
        assert result is True
        mock_copy.assert_called_once_with("/local/dir", "host", "/remote/dir")
