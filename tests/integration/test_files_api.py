"""Integration tests for file explorer API endpoints."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from orchestrator.api.app import create_app
from orchestrator.state.db import get_memory_connection
from orchestrator.state.migrations.runner import apply_migrations
from orchestrator.state.repositories import sessions as repo

pytestmark = pytest.mark.allow_subprocess


@pytest.fixture
def client_with_session(tmp_path):
    """Create a test client with a session whose work_dir is tmp_path."""
    conn = get_memory_connection()
    apply_migrations(conn)
    app = create_app(db=conn)

    # Create a session with work_dir pointing to tmp_path
    session = repo.create_session(
        conn,
        name="test-worker",
        host="localhost",
        work_dir=str(tmp_path),
    )

    # Clear git cache and rate limits to avoid cross-test contamination
    from orchestrator.api.routes.files import _git_cache, _rate_limits

    _git_cache.clear()
    _rate_limits.clear()

    with TestClient(app) as c:
        yield c, session.id, tmp_path


@pytest.fixture
def client_with_remote_session(tmp_path):
    """Create a test client with a remote session.

    Uses host="testhost" so is_remote_host returns True.
    SSH subprocess calls are mocked to execute the remote scripts locally.
    """
    conn = get_memory_connection()
    apply_migrations(conn)
    app = create_app(db=conn)

    session = repo.create_session(
        conn,
        name="remote-worker",
        host="testhost",
        work_dir=str(tmp_path),
    )

    from orchestrator.api.routes.files import (
        _git_cache,
        _rate_limits,
        _remote_content_cache,
        _remote_dir_cache,
    )

    _git_cache.clear()
    _rate_limits.clear()
    _remote_dir_cache.clear()
    _remote_content_cache.clear()

    def fake_run_ssh(host, script, args):
        """Execute the remote script locally instead of via SSH (text mode)."""
        import sys

        result = subprocess.run(
            [sys.executable, "-"] + args,
            input=script,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result

    def fake_run_ssh_bytes(host, script, args):
        """Execute the remote script locally instead of via SSH (bytes mode)."""
        import sys

        result = subprocess.run(
            [sys.executable, "-"] + args,
            input=script,
            capture_output=True,
            timeout=15,
        )
        return result

    with (
        patch(
            "orchestrator.api.routes.files.get_remote_file_server",
            side_effect=RuntimeError("no persistent server in tests"),
        ),
        patch("orchestrator.api.routes.files.ensure_server_starting"),
        patch("orchestrator.api.routes.files._run_ssh", side_effect=fake_run_ssh),
        patch("orchestrator.api.routes.files._run_ssh_bytes", side_effect=fake_run_ssh_bytes),
    ):
        with TestClient(app) as c:
            yield c, session.id, tmp_path


class TestListFiles:
    def test_list_root(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        # Create files in tmp_path
        (tmp_path / "hello.py").write_text("print('hi')")
        (tmp_path / "readme.md").write_text("# Readme")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "inner.txt").write_text("nested")

        resp = client.get(f"/api/sessions/{session_id}/files")
        assert resp.status_code == 200
        data = resp.json()
        assert data["work_dir"] == str(tmp_path)
        assert data["path"] == "."

        names = [e["name"] for e in data["entries"]]
        # Dirs first
        assert names[0] == "subdir"
        assert "hello.py" in names
        assert "readme.md" in names

    def test_list_subdir(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("# app")

        resp = client.get(f"/api/sessions/{session_id}/files?path=src")
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["name"] == "app.py"

    def test_reject_path_traversal(self, client_with_session):
        client, session_id, _ = client_with_session
        resp = client.get(f"/api/sessions/{session_id}/files?path=../../../etc")
        assert resp.status_code == 400

    def test_reject_absolute_path(self, client_with_session):
        client, session_id, _ = client_with_session
        resp = client.get(f"/api/sessions/{session_id}/files?path=/etc")
        assert resp.status_code == 400

    def test_nonexistent_session(self, client_with_session):
        client, _, _ = client_with_session
        resp = client.get("/api/sessions/nonexistent-id/files")
        assert resp.status_code == 404

    def test_nonexistent_directory(self, client_with_session):
        client, session_id, _ = client_with_session
        resp = client.get(f"/api/sessions/{session_id}/files?path=no_such_dir")
        assert resp.status_code == 404

    def test_hidden_files_filtered(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        (tmp_path / ".hidden").write_text("secret")
        (tmp_path / "visible.txt").write_text("hi")

        resp = client.get(f"/api/sessions/{session_id}/files?show_hidden=false")
        names = [e["name"] for e in resp.json()["entries"]]
        assert "visible.txt" in names
        assert ".hidden" not in names

    def test_show_hidden(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        (tmp_path / ".hidden").write_text("secret")

        resp = client.get(f"/api/sessions/{session_id}/files?show_hidden=true")
        names = [e["name"] for e in resp.json()["entries"]]
        assert ".hidden" in names

    def test_dir_has_children_count(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        d = tmp_path / "mydir"
        d.mkdir()
        (d / "a.txt").write_text("a")
        (d / "b.txt").write_text("b")

        resp = client.get(f"/api/sessions/{session_id}/files")
        entries = resp.json()["entries"]
        dir_entry = next(e for e in entries if e["name"] == "mydir")
        assert dir_entry["children_count"] == 2
        assert dir_entry["is_dir"] is True

    def test_file_has_size(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        (tmp_path / "sized.txt").write_text("12345")

        resp = client.get(f"/api/sessions/{session_id}/files")
        entry = next(e for e in resp.json()["entries"] if e["name"] == "sized.txt")
        assert entry["size"] == 5
        assert entry["human_size"] == "5B"

    def test_depth_prefetch(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        d = tmp_path / "src"
        d.mkdir()
        (d / "app.py").write_text("# app")
        (d / "util.py").write_text("# util")
        sub = d / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("# deep")

        # depth=1: no children pre-fetched
        resp = client.get(f"/api/sessions/{session_id}/files?depth=1")
        entries = resp.json()["entries"]
        src = next(e for e in entries if e["name"] == "src")
        assert src["children"] is None

        # depth=2: first-level dirs have children
        resp = client.get(f"/api/sessions/{session_id}/files?depth=2")
        entries = resp.json()["entries"]
        src = next(e for e in entries if e["name"] == "src")
        assert src["children"] is not None
        child_names = [c["name"] for c in src["children"]]
        assert "app.py" in child_names
        assert "sub" in child_names
        # sub's children NOT pre-fetched at depth=2
        sub_entry = next(c for c in src["children"] if c["name"] == "sub")
        assert sub_entry["children"] is None

        # depth=3: two levels deep
        resp = client.get(f"/api/sessions/{session_id}/files?depth=3")
        entries = resp.json()["entries"]
        src = next(e for e in entries if e["name"] == "src")
        sub_entry = next(c for c in src["children"] if c["name"] == "sub")
        assert sub_entry["children"] is not None
        assert sub_entry["children"][0]["name"] == "deep.py"


class TestGitStatusIntegration:
    def test_git_status_decorations(self, client_with_session):
        client, session_id, tmp_path = client_with_session

        # Init a git repo
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path,
            capture_output=True,
        )

        # Create and commit a file
        (tmp_path / "committed.py").write_text("# committed")
        subprocess.run(["git", "add", "committed.py"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=tmp_path,
            capture_output=True,
        )

        # Modify and add new file
        (tmp_path / "committed.py").write_text("# modified")
        (tmp_path / "new_file.txt").write_text("new")

        resp = client.get(f"/api/sessions/{session_id}/files")
        assert resp.status_code == 200
        data = resp.json()
        assert data["git_available"] is True

        entries_by_name = {e["name"]: e for e in data["entries"]}

        committed = entries_by_name.get("committed.py")
        assert committed is not None
        assert committed["git_status"] == "modified"

        new_file = entries_by_name.get("new_file.txt")
        assert new_file is not None
        assert new_file["git_status"] == "untracked"


class TestReadFileContent:
    def test_read_python_file(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        (tmp_path / "main.py").write_text("print('hello')\n")

        resp = client.get(f"/api/sessions/{session_id}/files/content?path=main.py")
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "print('hello')\n"
        assert data["language"] == "python"
        assert data["binary"] is False
        assert data["truncated"] is False
        assert data["total_lines"] == 1

    def test_truncation(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        (tmp_path / "big.txt").write_text("\n".join(f"line {i}" for i in range(100)))

        resp = client.get(f"/api/sessions/{session_id}/files/content?path=big.txt&max_lines=10")
        data = resp.json()
        assert data["truncated"] is True
        assert data["total_lines"] == 100
        # Content should only have first 10 lines
        assert data["content"].count("\n") <= 10

    def test_binary_detection(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        (tmp_path / "binary.bin").write_bytes(b"\x00\x01\x02\xff")

        resp = client.get(f"/api/sessions/{session_id}/files/content?path=binary.bin")
        data = resp.json()
        assert data["binary"] is True
        assert data["content"] == ""

    def test_nonexistent_file(self, client_with_session):
        client, session_id, _ = client_with_session
        resp = client.get(f"/api/sessions/{session_id}/files/content?path=no.txt")
        assert resp.status_code == 404

    def test_large_file_rejected(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        # Create a file > 5MB
        large = tmp_path / "huge.bin"
        large.write_bytes(b"x" * (6 * 1024 * 1024))

        resp = client.get(f"/api/sessions/{session_id}/files/content?path=huge.bin")
        assert resp.status_code == 413

    def test_language_detection(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        for ext, lang in [(".ts", "typescript"), (".json", "json"), (".md", "markdown")]:
            (tmp_path / f"test{ext}").write_text("content")
            resp = client.get(f"/api/sessions/{session_id}/files/content?path=test{ext}")
            assert resp.json()["language"] == lang

    def test_path_traversal_rejected(self, client_with_session):
        client, session_id, _ = client_with_session
        resp = client.get(f"/api/sessions/{session_id}/files/content?path=../../etc/passwd")
        assert resp.status_code == 400


class TestRateLimiting:
    def test_rate_limit_exceeded(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        (tmp_path / "file.txt").write_text("hi")

        # Clear any existing rate limit state
        from orchestrator.api.routes.files import _rate_limits

        _rate_limits.pop(session_id, None)

        # Send 60 requests (should all succeed)
        for _ in range(60):
            resp = client.get(f"/api/sessions/{session_id}/files")
            assert resp.status_code == 200

        # 61st should be rate limited
        resp = client.get(f"/api/sessions/{session_id}/files")
        assert resp.status_code == 429


class TestRawFile:
    def test_serve_png(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        # 1x1 red PNG
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
            b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
            b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        (tmp_path / "icon.png").write_bytes(png_bytes)

        resp = client.get(f"/api/sessions/{session_id}/files/raw?path=icon.png")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.content == png_bytes

    def test_serve_text_file(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        (tmp_path / "hello.txt").write_text("hello world")

        resp = client.get(f"/api/sessions/{session_id}/files/raw?path=hello.txt")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        assert resp.content == b"hello world"

    def test_path_traversal_rejected(self, client_with_session):
        client, session_id, _ = client_with_session
        resp = client.get(f"/api/sessions/{session_id}/files/raw?path=../../etc/passwd")
        assert resp.status_code == 400

    def test_file_not_found(self, client_with_session):
        client, session_id, _ = client_with_session
        resp = client.get(f"/api/sessions/{session_id}/files/raw?path=nonexistent.png")
        assert resp.status_code == 404

    def test_large_file_rejected(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        (tmp_path / "big.bin").write_bytes(b"\x00" * (11 * 1024 * 1024))

        resp = client.get(f"/api/sessions/{session_id}/files/raw?path=big.bin")
        assert resp.status_code == 413


# ---------------------------------------------------------------------------
# Phase 2: Remote file explorer integration tests
# ---------------------------------------------------------------------------


class TestRemoteListFiles:
    def test_list_root_directory(self, client_with_remote_session):
        client, session_id, tmp_path = client_with_remote_session
        (tmp_path / "hello.py").write_text("print('hi')")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "inner.txt").write_text("nested")

        resp = client.get(f"/api/sessions/{session_id}/files")
        assert resp.status_code == 200
        data = resp.json()
        names = [e["name"] for e in data["entries"]]
        assert "subdir" in names
        assert "hello.py" in names
        # Dirs should come first
        dir_idx = names.index("subdir")
        file_idx = names.index("hello.py")
        assert dir_idx < file_idx

    def test_list_subdirectory(self, client_with_remote_session):
        client, session_id, tmp_path = client_with_remote_session
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("# app")

        resp = client.get(f"/api/sessions/{session_id}/files?path=src")
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["name"] == "app.py"

    def test_path_traversal_rejected(self, client_with_remote_session):
        client, session_id, _ = client_with_remote_session
        resp = client.get(f"/api/sessions/{session_id}/files?path=../../etc")
        assert resp.status_code == 400

    def test_nonexistent_directory(self, client_with_remote_session):
        client, session_id, _ = client_with_remote_session
        resp = client.get(f"/api/sessions/{session_id}/files?path=no_such_dir")
        assert resp.status_code in (404, 502)

    def test_hidden_files_filtered(self, client_with_remote_session):
        client, session_id, tmp_path = client_with_remote_session
        (tmp_path / ".hidden").write_text("secret")
        (tmp_path / "visible.txt").write_text("hi")

        resp = client.get(f"/api/sessions/{session_id}/files?show_hidden=false")
        names = [e["name"] for e in resp.json()["entries"]]
        assert "visible.txt" in names
        assert ".hidden" not in names


class TestRemoteReadFile:
    def test_read_file(self, client_with_remote_session):
        client, session_id, tmp_path = client_with_remote_session
        (tmp_path / "main.py").write_text("print('hello')\n")

        resp = client.get(f"/api/sessions/{session_id}/files/content?path=main.py")
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "print('hello')\n"
        assert data["language"] == "python"
        assert data["binary"] is False

    def test_binary_detection(self, client_with_remote_session):
        client, session_id, tmp_path = client_with_remote_session
        (tmp_path / "binary.bin").write_bytes(b"\x00\x01\x02\xff")

        resp = client.get(f"/api/sessions/{session_id}/files/content?path=binary.bin")
        data = resp.json()
        assert data["binary"] is True
        assert data["content"] == ""

    def test_truncation(self, client_with_remote_session):
        client, session_id, tmp_path = client_with_remote_session
        (tmp_path / "big.txt").write_text("\n".join(f"line {i}" for i in range(100)))

        resp = client.get(f"/api/sessions/{session_id}/files/content?path=big.txt&max_lines=10")
        data = resp.json()
        assert data["truncated"] is True
        assert data["total_lines"] == 100

    def test_file_not_found(self, client_with_remote_session):
        client, session_id, _ = client_with_remote_session
        resp = client.get(f"/api/sessions/{session_id}/files/content?path=missing.txt")
        assert resp.status_code in (404, 502)


class TestRemoteCaching:
    def test_cache_used_on_second_request(self, client_with_remote_session):
        client, session_id, tmp_path = client_with_remote_session
        (tmp_path / "cached.txt").write_text("content")

        # First request populates cache
        resp1 = client.get(f"/api/sessions/{session_id}/files")
        assert resp1.status_code == 200

        from orchestrator.api.routes.files import _remote_dir_cache

        assert len(_remote_dir_cache) > 0

        # Second request should use cache (entries should be the same)
        resp2 = client.get(f"/api/sessions/{session_id}/files")
        assert resp2.status_code == 200
        assert resp1.json() == resp2.json()

    def test_refresh_bypasses_cache(self, client_with_remote_session):
        client, session_id, tmp_path = client_with_remote_session
        (tmp_path / "a.txt").write_text("v1")

        resp1 = client.get(f"/api/sessions/{session_id}/files")
        assert resp1.status_code == 200

        # Add a new file
        (tmp_path / "b.txt").write_text("v2")

        # Without refresh — still cached
        resp2 = client.get(f"/api/sessions/{session_id}/files")
        assert resp2.json() == resp1.json()

        # With refresh — should see new file
        resp3 = client.get(f"/api/sessions/{session_id}/files?refresh=true")
        assert resp3.status_code == 200
        names3 = [e["name"] for e in resp3.json()["entries"]]
        assert "b.txt" in names3


class TestWriteFile:
    def test_write_and_read_back(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        (tmp_path / "editable.py").write_text("original")

        resp = client.put(
            f"/api/sessions/{session_id}/files/content",
            json={"path": "editable.py", "content": "modified content"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["conflict"] is False
        assert data["size"] == len("modified content")

        # Read back
        resp = client.get(f"/api/sessions/{session_id}/files/content?path=editable.py")
        assert resp.json()["content"] == "modified content"

    def test_write_new_file_and_read_back(self, client_with_session):
        client, session_id, tmp_path = client_with_session

        resp = client.put(
            f"/api/sessions/{session_id}/files/content",
            json={
                "path": "newdir/newfile.txt",
                "content": "hello new file",
                "create": True,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["conflict"] is False

        # Read back
        resp = client.get(f"/api/sessions/{session_id}/files/content?path=newdir/newfile.txt")
        assert resp.status_code == 200
        assert resp.json()["content"] == "hello new file"

    def test_write_conflict_detection(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        (tmp_path / "conflict.py").write_text("original")

        # Use a stale mtime
        resp = client.put(
            f"/api/sessions/{session_id}/files/content",
            json={
                "path": "conflict.py",
                "content": "new content",
                "expected_mtime": 1000000.0,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["conflict"] is True
        # File should be unchanged
        assert (tmp_path / "conflict.py").read_text() == "original"

    def test_write_oversize_rejected(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        (tmp_path / "big.txt").write_text("x")

        large_content = "x" * (3 * 1024 * 1024)  # 3MB > 2MB limit
        resp = client.put(
            f"/api/sessions/{session_id}/files/content",
            json={"path": "big.txt", "content": large_content},
        )
        assert resp.status_code == 413

    def test_write_path_traversal_rejected(self, client_with_session):
        client, session_id, _ = client_with_session
        resp = client.put(
            f"/api/sessions/{session_id}/files/content",
            json={"path": "../../etc/passwd", "content": "hack"},
        )
        assert resp.status_code == 400


class TestRemoteWriteFile:
    def test_write_and_read_back_remote(self, client_with_remote_session):
        client, session_id, tmp_path = client_with_remote_session
        (tmp_path / "remote_edit.py").write_text("original remote")

        resp = client.put(
            f"/api/sessions/{session_id}/files/content",
            json={"path": "remote_edit.py", "content": "updated remote"},
        )
        assert resp.status_code == 200
        assert resp.json()["conflict"] is False

        # Read back (refresh to bypass cache)
        resp = client.get(
            f"/api/sessions/{session_id}/files/content?path=remote_edit.py&refresh=true"
        )
        assert resp.status_code == 200
        assert resp.json()["content"] == "updated remote"

    def test_write_new_file_remote(self, client_with_remote_session):
        client, session_id, tmp_path = client_with_remote_session

        resp = client.put(
            f"/api/sessions/{session_id}/files/content",
            json={
                "path": "new_remote.txt",
                "content": "new remote file",
                "create": True,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["conflict"] is False
        assert (tmp_path / "new_remote.txt").read_text() == "new remote file"

    def test_write_conflict_remote(self, client_with_remote_session):
        client, session_id, tmp_path = client_with_remote_session
        (tmp_path / "conflict_remote.py").write_text("original")

        resp = client.put(
            f"/api/sessions/{session_id}/files/content",
            json={
                "path": "conflict_remote.py",
                "content": "new",
                "expected_mtime": 1000000.0,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["conflict"] is True


class TestRemoteEndToEndFlow:
    """Simulate the full user flow: open file explorer, browse, view files."""

    def test_full_browsing_flow(self, client_with_remote_session):
        """Simulate: user opens file explorer, lists root, expands subdir,
        opens a Python file, opens a markdown file."""
        client, session_id, tmp_path = client_with_remote_session

        # Set up a project-like structure
        (tmp_path / "README.md").write_text("# My Project\n\nHello world\n")
        (tmp_path / "main.py").write_text("def main():\n    print('hello')\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("from fastapi import FastAPI\n\napp = FastAPI()\n")
        (src / "utils.py").write_text("def helper():\n    pass\n")
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_app.py").write_text("def test_main():\n    assert True\n")

        # 1) Verify session is accessible (simulates: session detail page loads)
        resp = client.get(f"/api/sessions/{session_id}")
        assert resp.status_code == 200
        session_data = resp.json()
        assert session_data["work_dir"] is not None
        assert session_data["host"] == "testhost"  # remote host

        # 2) List root directory (simulates: file explorer toggle opened)
        resp = client.get(f"/api/sessions/{session_id}/files")
        assert resp.status_code == 200
        root = resp.json()
        assert root["git_available"] is not None  # bool
        root_names = [e["name"] for e in root["entries"]]
        # Dirs come first, then files
        assert "src" in root_names
        assert "tests" in root_names
        assert "README.md" in root_names
        assert "main.py" in root_names
        # Check dirs have children_count
        src_entry = next(e for e in root["entries"] if e["name"] == "src")
        assert src_entry["is_dir"] is True
        assert src_entry["children_count"] == 2

        # 3) Expand src/ directory (simulates: user clicks on src/ in tree)
        resp = client.get(f"/api/sessions/{session_id}/files?path=src")
        assert resp.status_code == 200
        src_listing = resp.json()
        src_names = [e["name"] for e in src_listing["entries"]]
        assert "app.py" in src_names
        assert "utils.py" in src_names

        # 4) Open a Python file (simulates: user clicks app.py in tree)
        resp = client.get(f"/api/sessions/{session_id}/files/content?path=src/app.py")
        assert resp.status_code == 200
        file_data = resp.json()
        assert "FastAPI" in file_data["content"]
        assert file_data["language"] == "python"
        assert file_data["binary"] is False
        assert file_data["truncated"] is False
        assert file_data["size"] > 0

        # 5) Open a markdown file (simulates: user clicks README.md)
        resp = client.get(f"/api/sessions/{session_id}/files/content?path=README.md")
        assert resp.status_code == 200
        md_data = resp.json()
        assert "# My Project" in md_data["content"]
        assert md_data["language"] == "markdown"

        # 6) Expand tests/ (simulates: user clicks tests/ in tree)
        resp = client.get(f"/api/sessions/{session_id}/files?path=tests")
        assert resp.status_code == 200
        test_names = [e["name"] for e in resp.json()["entries"]]
        assert "test_app.py" in test_names

    def test_session_toggle_visibility_for_remote(self, client_with_remote_session):
        """Verify the session API returns data that enables the toggle."""
        client, session_id, tmp_path = client_with_remote_session
        (tmp_path / "file.txt").write_text("content")

        resp = client.get(f"/api/sessions/{session_id}")
        assert resp.status_code == 200
        session_data = resp.json()

        # The frontend condition is: session.work_dir && (...)
        # For the toggle to appear, work_dir must be non-null
        assert session_data["work_dir"] is not None
        # Host is remote (not localhost)
        assert session_data["host"] != "localhost"

        # And the files endpoint must work for this remote session
        resp = client.get(f"/api/sessions/{session_id}/files")
        assert resp.status_code == 200
        assert len(resp.json()["entries"]) > 0

    def test_local_session_still_works(self, client_with_session):
        """Ensure the local code path is unaffected by Phase 2 changes."""
        client, session_id, tmp_path = client_with_session
        (tmp_path / "local.py").write_text("x = 1\n")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "nested.txt").write_text("nested")

        # List root
        resp = client.get(f"/api/sessions/{session_id}/files")
        assert resp.status_code == 200
        names = [e["name"] for e in resp.json()["entries"]]
        assert "local.py" in names
        assert "sub" in names

        # Read file
        resp = client.get(f"/api/sessions/{session_id}/files/content?path=local.py")
        assert resp.status_code == 200
        assert resp.json()["content"] == "x = 1\n"
        assert resp.json()["language"] == "python"

        # Expand subdir
        resp = client.get(f"/api/sessions/{session_id}/files?path=sub")
        assert resp.status_code == 200
        assert resp.json()["entries"][0]["name"] == "nested.txt"
