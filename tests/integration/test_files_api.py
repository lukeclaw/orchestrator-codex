"""Integration tests for file explorer API endpoints."""

from __future__ import annotations

import subprocess

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

        resp = client.get(f"/api/sessions/{session_id}/files")
        names = [e["name"] for e in resp.json()["entries"]]
        assert "visible.txt" in names
        assert ".hidden" not in names

    def test_show_ignored(self, client_with_session):
        client, session_id, tmp_path = client_with_session
        (tmp_path / ".hidden").write_text("secret")

        resp = client.get(f"/api/sessions/{session_id}/files?show_ignored=true")
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

        # Send 20 requests (should all succeed)
        for _ in range(20):
            resp = client.get(f"/api/sessions/{session_id}/files")
            assert resp.status_code == 200

        # 21st should be rate limited
        resp = client.get(f"/api/sessions/{session_id}/files")
        assert resp.status_code == 429
