"""File explorer API — directory listing + file content reading."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import subprocess
import tempfile
import textwrap
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import sessions as repo
from orchestrator.terminal.file_sync import _SSH_OPTS
from orchestrator.terminal.remote_file_server import (
    ensure_server_starting,
    get_remote_file_server,
)
from orchestrator.terminal.ssh import is_remote_host

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Rate limiting (simple in-memory counter per session)
# ---------------------------------------------------------------------------
_rate_limits: dict[str, list[float]] = {}
_RATE_LIMIT = 60
_RATE_WINDOW = 10.0  # seconds


def _check_rate_limit(session_id: str) -> None:
    now = time.monotonic()
    timestamps = _rate_limits.setdefault(session_id, [])
    # Purge old entries
    cutoff = now - _RATE_WINDOW
    timestamps[:] = [t for t in timestamps if t > cutoff]
    if len(timestamps) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    timestamps.append(now)


# ---------------------------------------------------------------------------
# Git status cache (5 s TTL, keyed by work_dir)
# ---------------------------------------------------------------------------
_git_cache: dict[str, tuple[float, dict[str, str]]] = {}
_GIT_CACHE_TTL = 5.0
_GIT_TIMEOUT = 3  # seconds

# Git porcelain code → friendly name
_GIT_STATUS_MAP: dict[str, str] = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "U": "conflicting",
    "?": "untracked",
    "!": "ignored",
}

# Extension → language mapping
_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".sql": "sql",
    ".xml": "xml",
    ".svg": "xml",
    ".r": "r",
    ".lua": "lua",
    ".swift": "swift",
    ".kt": "kotlin",
    ".gradle": "gradle",
    ".dockerfile": "dockerfile",
    ".tf": "hcl",
    ".ini": "ini",
    ".cfg": "ini",
    ".conf": "ini",
    ".env": "ini",
    ".gitignore": "gitignore",
    ".csv": "csv",
}

# Common gitignored dirs to hide by default
_DEFAULT_IGNORED: set[str] = {
    "__pycache__",
    "node_modules",
    ".git",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".egg-info",
    ".venv",
    "venv",
    ".next",
    ".DS_Store",
    "Thumbs.db",
}


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------
def _validate_path(path: str) -> None:
    """Reject dangerous path components."""
    if "\x00" in path:
        raise HTTPException(status_code=400, detail="Path contains null bytes")
    if os.path.isabs(path):
        raise HTTPException(status_code=400, detail="Absolute paths not allowed")
    # Reject any component that is ".."
    parts = Path(path).parts
    if ".." in parts:
        raise HTTPException(status_code=400, detail="Path traversal not allowed")


def _detect_remote_work_dir(host: str, session_id: str) -> str | None:
    """Detect the working directory of the Claude process on a remote host.

    Finds the Claude Code process by *session_id* (passed via ``--session-id``),
    then reads its cwd with ``pwdx <pid>``.
    """
    # Find Claude PID, then get its cwd in one SSH round-trip.
    cmd = (
        f"pid=$(ps aux | grep -v grep "
        f"| grep -E 'claude (-r|--|--settings)' "
        f"| grep '{session_id}' "
        f"| awk '{{print $2}}' | head -1) && "
        f'[ -n "$pid" ] && pwdx "$pid" | awk \'{{print $2}}\''
    )
    try:
        result = subprocess.run(
            ["ssh", *_SSH_OPTS, host, cmd],
            capture_output=True,
            text=True,
            timeout=10,
        )
        path = result.stdout.strip()
        if result.returncode == 0 and path and path.startswith("/"):
            return path
    except Exception:
        logger.debug("Failed to detect work_dir on %s", host, exc_info=True)
    return None


@dataclass(frozen=True)
class SessionInfo:
    work_dir: str
    host: str
    is_remote: bool


def _resolve_session(db, session_id: str) -> SessionInfo:
    """Look up session and return metadata including locality flag."""
    session = repo.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    remote = is_remote_host(session.host)
    work_dir = session.work_dir

    if not work_dir and remote:
        # Detect cwd of the running Claude process via pwdx
        work_dir = _detect_remote_work_dir(session.host, session_id)
        if work_dir:
            repo.update_session(db, session_id, work_dir=work_dir)

    if not work_dir:
        raise HTTPException(status_code=400, detail="Session has no work_dir")
    if not remote and not os.path.isdir(work_dir):
        raise HTTPException(status_code=400, detail="work_dir does not exist on disk")

    # Eagerly start the persistent file server so it's ready by the time
    # the first file operation completes and the user clicks something else.
    if remote:
        ensure_server_starting(session.host)

    return SessionInfo(work_dir=work_dir, host=session.host, is_remote=remote)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------
def _get_git_status(work_dir: str, show_ignored: bool) -> tuple[dict[str, str], bool]:
    """Return {relative_path: status_string} and git_available flag.

    Uses a 5 s in-memory cache.  Subprocess has a 3 s timeout.
    """
    cache_key = f"{work_dir}::{show_ignored}"
    now = time.monotonic()
    cached = _git_cache.get(cache_key)
    if cached and now - cached[0] < _GIT_CACHE_TTL:
        return cached[1], True

    cmd = ["git", "status", "--porcelain=v1", "-z"]
    if show_ignored:
        cmd.append("--ignored")

    try:
        result = subprocess.run(
            cmd,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        logger.warning("git status timed out for %s", work_dir)
        return {}, False
    except FileNotFoundError:
        return {}, False

    if result.returncode != 0:
        return {}, False

    statuses: dict[str, str] = {}
    # Porcelain v1 with -z: entries separated by NUL
    entries = result.stdout.split("\0")
    for entry in entries:
        if len(entry) < 4:
            continue
        # Format: "XY path" where XY are the two status columns
        xy = entry[:2]
        path_str = entry[3:]
        # Use the most significant status code
        code = xy[0] if xy[0] != " " else xy[1]
        status_name = _GIT_STATUS_MAP.get(code, "modified")
        statuses[path_str] = status_name
        # For renamed entries, there's an extra path after the arrow
        # git -z uses NUL separator so the "from" path is the next entry — skip it

    _git_cache[cache_key] = (now, statuses)
    return statuses, True


def _human_size(size: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024  # type: ignore[assignment]
    return f"{size:.1f}TB"


# ---------------------------------------------------------------------------
# Remote (SSH) helpers
# ---------------------------------------------------------------------------
_SSH_TIMEOUT = 15  # seconds

# Per-host semaphore to limit concurrent SSH connections
_host_semaphores: dict[str, threading.Semaphore] = {}
_host_sem_lock = threading.Lock()
_MAX_SSH_CONCURRENT = 8
_SSH_ACQUIRE_TIMEOUT = 10  # seconds


def _get_host_semaphore(host: str) -> threading.Semaphore:
    with _host_sem_lock:
        if host not in _host_semaphores:
            _host_semaphores[host] = threading.Semaphore(_MAX_SSH_CONCURRENT)
        return _host_semaphores[host]


def _acquire_ssh_slot(host: str) -> None:
    sem = _get_host_semaphore(host)
    if not sem.acquire(timeout=_SSH_ACQUIRE_TIMEOUT):
        raise HTTPException(
            status_code=503,
            detail="Too many concurrent SSH connections; try again shortly",
        )


def _release_ssh_slot(host: str) -> None:
    _get_host_semaphore(host).release()


# Self-contained Python script executed on the remote host via SSH stdin.
# Takes: work_dir, relative_path, show_ignored_flag ("1"/"0"), depth
_REMOTE_LIST_SCRIPT = textwrap.dedent("""\
    import json, os, subprocess, sys

    work_dir = sys.argv[1]
    rel_path = sys.argv[2]
    show_ignored = sys.argv[3] == "1"
    max_depth = int(sys.argv[4]) if len(sys.argv) > 4 else 1

    DEFAULT_IGNORED = {
        "__pycache__", "node_modules", ".git", ".tox", ".mypy_cache",
        ".pytest_cache", ".ruff_cache", "dist", "build", ".egg-info",
        ".venv", "venv", ".next", ".DS_Store", "Thumbs.db",
    }
    GIT_STATUS_MAP = {
        "M": "modified", "A": "added", "D": "deleted", "R": "renamed",
        "C": "copied", "U": "conflicting", "?": "untracked", "!": "ignored",
    }
    SEVERITY = ["conflicting","deleted","modified","added","renamed","untracked","ignored"]

    norm_work = os.path.normpath(work_dir)
    target = os.path.normpath(os.path.join(work_dir, rel_path))
    if not target.startswith(norm_work):
        print(json.dumps({"error": "Path outside work_dir"}))
        sys.exit(1)

    if not os.path.isdir(target):
        print(json.dumps({"error": "Directory not found"}))
        sys.exit(1)

    # Git status (once, reused by all depths)
    git_statuses = {}
    git_available = False
    cmd = ["git", "status", "--porcelain=v1", "-z"]
    if show_ignored:
        cmd.append("--ignored")
    try:
        r = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            git_available = True
            for entry in r.stdout.split("\\0"):
                if len(entry) < 4:
                    continue
                xy = entry[:2]
                p = entry[3:]
                code = xy[0] if xy[0] != " " else xy[1]
                git_statuses[p] = GIT_STATUS_MAP.get(code, "modified")
    except Exception:
        pass

    def apply_git(entries):
        if not git_available:
            return
        for ent in entries:
            p = ent["path"]
            if p in git_statuses:
                ent["git_status"] = git_statuses[p]
            elif ent["is_dir"]:
                prefix = p + "/"
                child = [s for k, s in git_statuses.items() if k.startswith(prefix)]
                if child:
                    for sev in SEVERITY:
                        if sev in child:
                            ent["git_status"] = sev
                            break
                    else:
                        ent["git_status"] = child[0]
            if ent.get("children"):
                apply_git(ent["children"])

    def scan_dir(abs_path, current_depth):
        entries = []
        try:
            for e in os.scandir(abs_path):
                name = e.name
                if not show_ignored and name in DEFAULT_IGNORED:
                    continue
                if not show_ignored and name.startswith(".") and name != ".":
                    continue
                rp = os.path.relpath(e.path, work_dir)
                is_dir = e.is_dir(follow_symlinks=False)
                try:
                    st = e.stat(follow_symlinks=False)
                    size = st.st_size if not is_dir else None
                    modified = st.st_mtime
                except OSError:
                    size = None
                    modified = None
                children_count = None
                sub_children = None
                if is_dir:
                    try:
                        children_count = sum(1 for _ in os.scandir(e.path))
                    except OSError:
                        pass
                    if current_depth < max_depth:
                        sub_children = scan_dir(e.path, current_depth + 1)
                entries.append({
                    "name": name, "path": rp, "is_dir": is_dir,
                    "size": size, "modified": modified,
                    "children_count": children_count,
                    "git_status": None,
                    "children": sub_children,
                })
        except PermissionError:
            pass
        entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return entries

    entries = scan_dir(target, 1)
    apply_git(entries)
    print(json.dumps({"entries": entries, "git_available": git_available}))
""")


def _run_ssh(host: str, script: str, args: list[str]) -> subprocess.CompletedProcess:
    """Execute a Python script on a remote host via SSH stdin (text mode)."""
    cmd = ["ssh", *_SSH_OPTS, host, "python3", "-"] + args
    return subprocess.run(
        cmd,
        input=script,
        capture_output=True,
        text=True,
        timeout=_SSH_TIMEOUT,
    )


def _run_ssh_bytes(host: str, script: bytes, args: list[str]) -> subprocess.CompletedProcess:
    """Execute a Python script on a remote host via SSH stdin (bytes mode)."""
    cmd = ["ssh", *_SSH_OPTS, host, "python3", "-"] + args
    return subprocess.run(
        cmd,
        input=script,
        capture_output=True,
        timeout=_SSH_TIMEOUT,
    )


def _list_remote_dir_fallback(
    host: str, work_dir: str, path: str, show_ignored: bool, depth: int = 1
) -> tuple[list[FileEntry], bool]:
    """List a directory on a remote host via SSH (one-shot fallback)."""
    _acquire_ssh_slot(host)
    try:
        result = _run_ssh(
            host,
            _REMOTE_LIST_SCRIPT,
            [work_dir, path, "1" if show_ignored else "0", str(depth)],
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Remote directory listing timed out")
    finally:
        _release_ssh_slot(host)

    if result.returncode != 0:
        # Try to parse error from script output
        try:
            err = json.loads(result.stdout)
            detail = err.get("error", "Remote listing failed")
        except (json.JSONDecodeError, KeyError):
            detail = result.stderr.strip() or "Remote listing failed"
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=502, detail=detail)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="Invalid response from remote host")

    entries = _parse_remote_entries(data["entries"])
    return entries, data.get("git_available", False)


def _list_remote_dir(
    host: str, work_dir: str, path: str, show_ignored: bool, depth: int = 1
) -> tuple[list[FileEntry], bool]:
    """List a directory on a remote host, preferring the persistent server."""
    t0 = time.monotonic()
    try:
        server = get_remote_file_server(host)
        data = server.execute(
            {
                "action": "list_dir",
                "work_dir": work_dir,
                "path": path,
                "show_ignored": show_ignored,
                "depth": depth,
            }
        )
        if "error" in data:
            detail = data["error"]
            if "not found" in detail.lower():
                raise HTTPException(status_code=404, detail=detail)
            raise HTTPException(status_code=502, detail=detail)
        entries = _parse_remote_entries(data["entries"])
        elapsed = time.monotonic() - t0
        logger.info(
            "list_remote_dir %s:%s took %.3fs (server=persistent)",
            host,
            path,
            elapsed,
        )
        return entries, data.get("git_available", False)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Persistent server failed for list_dir on %s, falling back: %s",
            host,
            exc,
        )
        result = _list_remote_dir_fallback(host, work_dir, path, show_ignored, depth)
        elapsed = time.monotonic() - t0
        logger.info(
            "list_remote_dir %s:%s took %.3fs (server=fallback)",
            host,
            path,
            elapsed,
        )
        return result


def _parse_remote_entries(raw_list: list[dict]) -> list[FileEntry]:
    """Recursively parse remote listing JSON into FileEntry objects."""
    entries = []
    for raw in raw_list:
        size = raw.get("size")
        raw_children = raw.get("children")
        entries.append(
            FileEntry(
                name=raw["name"],
                path=raw["path"],
                is_dir=raw["is_dir"],
                size=size,
                modified=raw.get("modified"),
                children_count=raw.get("children_count"),
                git_status=raw.get("git_status"),
                human_size=_human_size(size) if size is not None else None,
                children=_parse_remote_entries(raw_children) if raw_children else None,
            )
        )
    return entries


# Self-contained Python script for reading file content on a remote host.
# Takes: work_dir, relative_path, max_lines
_REMOTE_READ_SCRIPT = textwrap.dedent("""\
    import json, os, sys

    work_dir = sys.argv[1]
    rel_path = sys.argv[2]
    max_lines = int(sys.argv[3])

    target = os.path.normpath(os.path.join(work_dir, rel_path))
    if not target.startswith(os.path.normpath(work_dir)):
        print(json.dumps({"error": "Path outside work_dir"}))
        sys.exit(1)

    if not os.path.isfile(target):
        print(json.dumps({"error": "File not found"}))
        sys.exit(1)

    st = os.stat(target)
    file_size = st.st_size
    file_mtime = st.st_mtime
    if file_size > 5 * 1024 * 1024:
        print(json.dumps({"error": "File too large (>5MB)", "code": 413}))
        sys.exit(1)

    # Binary detection
    try:
        with open(target, "rb") as f:
            chunk = f.read(8192)
            if b"\\x00" in chunk:
                print(json.dumps({
                    "content": "", "truncated": False,
                    "total_lines": None, "size": file_size,
                    "binary": True, "modified": file_mtime,
                }))
                sys.exit(0)
    except OSError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    # Read text
    try:
        with open(target, encoding="utf-8", errors="replace") as f:
            lines = []
            total = 0
            for line in f:
                total += 1
                if total <= max_lines:
                    lines.append(line)
            print(json.dumps({
                "content": "".join(lines),
                "truncated": total > max_lines,
                "total_lines": total,
                "size": file_size,
                "modified": file_mtime,
                "binary": False,
            }))
    except OSError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
""")


def _read_remote_file_fallback(
    host: str, work_dir: str, path: str, max_lines: int
) -> FileContentResponse:
    """Read file content from a remote host via SSH (one-shot fallback)."""
    _acquire_ssh_slot(host)
    try:
        result = _run_ssh(
            host,
            _REMOTE_READ_SCRIPT,
            [work_dir, path, str(max_lines)],
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Remote file read timed out")
    finally:
        _release_ssh_slot(host)

    if result.returncode != 0:
        try:
            err = json.loads(result.stdout)
            detail = err.get("error", "Remote read failed")
            code = err.get("code", 502)
        except (json.JSONDecodeError, KeyError):
            detail = result.stderr.strip() or "Remote read failed"
            code = 502
        if "not found" in detail.lower():
            code = 404
        elif "too large" in detail.lower():
            code = 413
        raise HTTPException(status_code=code, detail=detail)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="Invalid response from remote host")

    return _build_file_content_response(path, data)


def _build_file_content_response(path: str, data: dict) -> FileContentResponse:
    """Build a FileContentResponse from parsed remote data."""
    ext = os.path.splitext(path)[1].lower()
    basename = os.path.basename(path).lower()
    language = _LANGUAGE_MAP.get(ext)
    if language is None:
        if basename == "dockerfile":
            language = "dockerfile"
        elif basename == "makefile":
            language = "makefile"

    return FileContentResponse(
        path=path,
        content=data.get("content", ""),
        truncated=data.get("truncated", False),
        total_lines=data.get("total_lines"),
        size=data.get("size", 0),
        binary=data.get("binary", False),
        language=language if not data.get("binary") else None,
        modified=data.get("modified"),
    )


def _read_remote_file(host: str, work_dir: str, path: str, max_lines: int) -> FileContentResponse:
    """Read file content from a remote host, preferring the persistent server."""
    t0 = time.monotonic()
    try:
        server = get_remote_file_server(host)
        data = server.execute(
            {
                "action": "read_file",
                "work_dir": work_dir,
                "path": path,
                "max_lines": max_lines,
            }
        )
        if "error" in data:
            detail = data["error"]
            code = data.get("code", 502)
            if "not found" in detail.lower():
                code = 404
            elif "too large" in detail.lower():
                code = 413
            raise HTTPException(status_code=code, detail=detail)
        elapsed = time.monotonic() - t0
        logger.info(
            "read_remote_file %s:%s took %.3fs (server=persistent)",
            host,
            path,
            elapsed,
        )
        return _build_file_content_response(path, data)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Persistent server failed for read_file on %s, falling back: %s",
            host,
            exc,
        )
        result = _read_remote_file_fallback(host, work_dir, path, max_lines)
        elapsed = time.monotonic() - t0
        logger.info(
            "read_remote_file %s:%s took %.3fs (server=fallback)",
            host,
            path,
            elapsed,
        )
        return result


# ---------------------------------------------------------------------------
# Remote caching
# ---------------------------------------------------------------------------
_remote_dir_cache: dict[str, tuple[float, list[FileEntry], bool]] = {}
_REMOTE_DIR_CACHE_TTL = 5.0

_remote_content_cache: dict[str, tuple[float, FileContentResponse]] = {}
_REMOTE_CONTENT_CACHE_TTL = 120.0
_REMOTE_CONTENT_CACHE_MAX = 100

_cache_request_counter = 0


def _cleanup_remote_caches() -> None:
    """Purge expired entries from remote caches (called opportunistically)."""
    now = time.monotonic()
    expired = [k for k, (ts, *_) in _remote_dir_cache.items() if now - ts > _REMOTE_DIR_CACHE_TTL]
    for k in expired:
        del _remote_dir_cache[k]
    expired = [
        k for k, (ts, _) in _remote_content_cache.items() if now - ts > _REMOTE_CONTENT_CACHE_TTL
    ]
    for k in expired:
        del _remote_content_cache[k]


def _list_remote_dir_cached(
    host: str,
    work_dir: str,
    path: str,
    show_ignored: bool,
    refresh: bool,
    depth: int = 1,
) -> tuple[list[FileEntry], bool]:
    """Cached wrapper around _list_remote_dir."""
    global _cache_request_counter
    _cache_request_counter += 1
    if _cache_request_counter % 10 == 0:
        _cleanup_remote_caches()

    cache_key = f"{host}::{work_dir}::{path}::{show_ignored}::{depth}"
    if not refresh:
        cached = _remote_dir_cache.get(cache_key)
        if cached:
            ts, entries, git_avail = cached
            if time.monotonic() - ts < _REMOTE_DIR_CACHE_TTL:
                logger.info("list_remote_dir %s:%s cache=hit", host, path)
                return entries, git_avail

    entries, git_available = _list_remote_dir(host, work_dir, path, show_ignored, depth)
    _remote_dir_cache[cache_key] = (time.monotonic(), entries, git_available)
    return entries, git_available


def _read_remote_file_cached(
    host: str, work_dir: str, path: str, max_lines: int, refresh: bool
) -> FileContentResponse:
    """Cached wrapper around _read_remote_file."""
    global _cache_request_counter
    _cache_request_counter += 1
    if _cache_request_counter % 10 == 0:
        _cleanup_remote_caches()

    cache_key = f"{host}::{work_dir}::{path}::{max_lines}"
    if not refresh:
        cached = _remote_content_cache.get(cache_key)
        if cached:
            ts, resp = cached
            if time.monotonic() - ts < _REMOTE_CONTENT_CACHE_TTL:
                logger.info("read_remote_file %s:%s cache=hit", host, path)
                return resp

    resp = _read_remote_file(host, work_dir, path, max_lines)
    # Evict oldest if cache is full
    if len(_remote_content_cache) >= _REMOTE_CONTENT_CACHE_MAX:
        oldest_key = min(_remote_content_cache, key=lambda k: _remote_content_cache[k][0])
        del _remote_content_cache[oldest_key]
    _remote_content_cache[cache_key] = (time.monotonic(), resp)
    return resp


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class FileEntry(BaseModel):
    name: str
    path: str  # relative to work_dir
    is_dir: bool
    size: int | None = None
    modified: float | None = None  # epoch seconds
    children_count: int | None = None  # for dirs
    git_status: str | None = None
    human_size: str | None = None
    children: list[FileEntry] | None = None  # pre-fetched when depth > 1


class DirectoryResponse(BaseModel):
    work_dir: str
    path: str
    entries: list[FileEntry]
    git_available: bool


class FileContentResponse(BaseModel):
    path: str
    content: str
    truncated: bool
    total_lines: int | None
    size: int
    binary: bool
    language: str | None
    modified: float | None = None  # epoch seconds


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/sessions/{session_id}/files")
def list_files(
    session_id: str,
    path: str = Query(default=".", description="Relative path within work_dir"),
    depth: int = Query(default=1, ge=1, le=5),
    show_ignored: bool = Query(default=False),
    refresh: bool = Query(default=False),
    db=Depends(get_db),
) -> DirectoryResponse:
    """List directory contents with optional git status decorations."""
    _check_rate_limit(session_id)
    _validate_path(path)
    info = _resolve_session(db, session_id)

    if info.is_remote:
        entries, git_available = _list_remote_dir_cached(
            info.host, info.work_dir, path, show_ignored, refresh, depth
        )
    else:
        work_dir = info.work_dir
        target = os.path.normpath(os.path.join(work_dir, path))
        # Ensure we stay inside work_dir
        if not target.startswith(os.path.normpath(work_dir)):
            raise HTTPException(status_code=400, detail="Path outside work_dir")

        if not os.path.isdir(target):
            raise HTTPException(status_code=404, detail="Directory not found")

        entries = _scan_dir(target, work_dir, show_ignored, depth, current_depth=1)

        # Git status
        git_statuses, git_available = _get_git_status(work_dir, show_ignored)
        if git_available:
            _apply_git_status(entries, git_statuses)

    return DirectoryResponse(
        work_dir=info.work_dir,
        path=path,
        entries=entries,
        git_available=git_available,
    )


def _scan_dir(
    abs_path: str,
    work_dir: str,
    show_ignored: bool,
    max_depth: int,
    current_depth: int,
) -> list[FileEntry]:
    """Scan a directory and return sorted entries (dirs first, alpha)."""
    entries: list[FileEntry] = []
    try:
        with os.scandir(abs_path) as it:
            for entry in it:
                name = entry.name
                # Hide common ignored dirs unless show_ignored
                if not show_ignored and name in _DEFAULT_IGNORED:
                    continue
                # Skip hidden files by default (unless show_ignored)
                if not show_ignored and name.startswith(".") and name != ".":
                    continue

                rel_path = os.path.relpath(entry.path, work_dir)
                is_dir = entry.is_dir(follow_symlinks=False)

                try:
                    stat = entry.stat(follow_symlinks=False)
                    size = stat.st_size if not is_dir else None
                    modified = stat.st_mtime
                except OSError:
                    size = None
                    modified = None

                children_count = None
                if is_dir:
                    try:
                        children_count = sum(1 for _ in os.scandir(entry.path))
                    except OSError:
                        children_count = None

                sub_children = None
                if is_dir and current_depth < max_depth:
                    sub_children = _scan_dir(
                        entry.path,
                        work_dir,
                        show_ignored,
                        max_depth,
                        current_depth + 1,
                    )

                fe = FileEntry(
                    name=name,
                    path=rel_path,
                    is_dir=is_dir,
                    size=size,
                    modified=modified,
                    children_count=children_count,
                    human_size=_human_size(size) if size is not None else None,
                    children=sub_children,
                )
                entries.append(fe)

    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    # Sort: directories first, then alphabetically case-insensitive
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return entries


def _apply_git_status(entries: list[FileEntry], statuses: dict[str, str]) -> None:
    """Attach git_status to entries that appear in git status output."""
    for entry in entries:
        path = entry.path
        if path in statuses:
            entry.git_status = statuses[path]
        elif entry.is_dir:
            # Propagate: if any child has status, bubble up highest severity
            prefix = path + "/"
            child_statuses = [s for p, s in statuses.items() if p.startswith(prefix)]
            if child_statuses:
                entry.git_status = _highest_severity(child_statuses)
        # Recurse into pre-fetched children
        if entry.children:
            _apply_git_status(entry.children, statuses)


_SEVERITY_ORDER = [
    "conflicting",
    "deleted",
    "modified",
    "added",
    "renamed",
    "untracked",
    "ignored",
]


def _highest_severity(statuses: list[str]) -> str:
    for s in _SEVERITY_ORDER:
        if s in statuses:
            return s
    return statuses[0] if statuses else "modified"


# ---------------------------------------------------------------------------
# File content
# ---------------------------------------------------------------------------
_MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


@router.get("/sessions/{session_id}/files/content")
def read_file_content(
    session_id: str,
    path: str = Query(..., description="Relative path to the file"),
    max_lines: int = Query(default=500, ge=1, le=10000),
    refresh: bool = Query(default=False),
    db=Depends(get_db),
) -> FileContentResponse:
    """Read file contents with truncation and binary detection."""
    _check_rate_limit(session_id)
    _validate_path(path)
    info = _resolve_session(db, session_id)

    if info.is_remote:
        return _read_remote_file_cached(info.host, info.work_dir, path, max_lines, refresh)

    # --- local path (unchanged) ---
    work_dir = info.work_dir
    abs_path = os.path.normpath(os.path.join(work_dir, path))
    if not abs_path.startswith(os.path.normpath(work_dir)):
        raise HTTPException(status_code=400, detail="Path outside work_dir")

    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="File not found")

    stat = os.stat(abs_path)
    file_size = stat.st_size
    file_modified = stat.st_mtime
    if file_size > _MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large (>5MB)")

    # Binary detection: check first 8KB for null bytes
    is_binary = False
    try:
        with open(abs_path, "rb") as f:
            chunk = f.read(8192)
            if b"\x00" in chunk:
                is_binary = True
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Cannot read file: {e}")

    if is_binary:
        return FileContentResponse(
            path=path,
            content="",
            truncated=False,
            total_lines=None,
            size=file_size,
            binary=True,
            language=None,
            modified=file_modified,
        )

    # Read text content with truncation
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            lines: list[str] = []
            total_lines = 0
            for line in f:
                total_lines += 1
                if total_lines <= max_lines:
                    lines.append(line)
            content = "".join(lines)
            truncated = total_lines > max_lines
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Cannot read file: {e}")

    # Language detection
    ext = os.path.splitext(abs_path)[1].lower()
    # Also check for extensionless files like Dockerfile, Makefile
    basename = os.path.basename(abs_path).lower()
    language = _LANGUAGE_MAP.get(ext)
    if language is None:
        if basename == "dockerfile":
            language = "dockerfile"
        elif basename == "makefile":
            language = "makefile"

    return FileContentResponse(
        path=path,
        content=content,
        truncated=truncated,
        total_lines=total_lines,
        size=file_size,
        binary=False,
        language=language,
        modified=file_modified,
    )


# ---------------------------------------------------------------------------
# File writing
# ---------------------------------------------------------------------------
_MAX_WRITE_SIZE = 2 * 1024 * 1024  # 2 MB


class FileWriteRequest(BaseModel):
    path: str
    content: str
    expected_mtime: float | None = None  # conflict detection
    create: bool = False  # allow creating new files


class FileWriteResponse(BaseModel):
    path: str
    size: int
    modified: float
    conflict: bool = False


@router.put("/sessions/{session_id}/files/content")
def write_file_content(
    session_id: str,
    body: FileWriteRequest,
    db=Depends(get_db),
) -> FileWriteResponse:
    """Write file contents with optional conflict detection via mtime."""
    _check_rate_limit(session_id)
    _validate_path(body.path)
    info = _resolve_session(db, session_id)

    content_bytes = body.content.encode("utf-8")
    if len(content_bytes) > _MAX_WRITE_SIZE:
        raise HTTPException(status_code=413, detail="Content too large (>2MB)")

    if info.is_remote:
        return _write_remote_file(info.host, info.work_dir, body)
    return _write_local_file(info.work_dir, body)


def _write_local_file(work_dir: str, body: FileWriteRequest) -> FileWriteResponse:
    """Write a file on the local filesystem with atomic rename."""
    abs_path = os.path.normpath(os.path.join(work_dir, body.path))
    if not abs_path.startswith(os.path.normpath(work_dir)):
        raise HTTPException(status_code=400, detail="Path outside work_dir")

    if not body.create and not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="File not found")

    if body.create:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    # Conflict detection
    if body.expected_mtime is not None and os.path.isfile(abs_path):
        current_mtime = os.stat(abs_path).st_mtime
        if abs(current_mtime - body.expected_mtime) > 0.5:
            return FileWriteResponse(
                path=body.path,
                size=os.path.getsize(abs_path),
                modified=current_mtime,
                conflict=True,
            )

    # Atomic write: temp file + rename
    dir_name = os.path.dirname(abs_path)
    try:
        fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(body.content)
        os.replace(tmp, abs_path)  # atomic on same filesystem
    except PermissionError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise HTTPException(status_code=403, detail="Permission denied")
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    stat = os.stat(abs_path)
    # Invalidate remote content cache entry if present
    _remote_content_cache.pop(f"localhost::{work_dir}::{body.path}::500", None)
    return FileWriteResponse(path=body.path, size=stat.st_size, modified=stat.st_mtime)


# Self-contained Python script for writing file content on a remote host.
_REMOTE_WRITE_SCRIPT = textwrap.dedent("""\
    import base64, json, os, sys, tempfile

    work_dir, rel_path, mtime_arg, create_arg = sys.argv[1:5]
    expected_mtime = float(mtime_arg) if mtime_arg != "none" else None
    allow_create = create_arg == "true"

    target = os.path.normpath(os.path.join(work_dir, rel_path))
    if not target.startswith(os.path.normpath(work_dir)):
        print(json.dumps({"error": "Path outside work_dir"}))
        sys.exit(1)

    if not allow_create and not os.path.isfile(target):
        print(json.dumps({"error": "File not found"}))
        sys.exit(1)

    if allow_create:
        os.makedirs(os.path.dirname(target), exist_ok=True)

    # Conflict detection
    if expected_mtime is not None and os.path.isfile(target):
        cur = os.stat(target).st_mtime
        if abs(cur - expected_mtime) > 0.5:
            print(json.dumps({"conflict": True, "size": os.path.getsize(target), "modified": cur}))
            sys.exit(0)

    content = base64.b64decode("__CONTENT_B64__").decode("utf-8")

    # Atomic write
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.replace(tmp, target)
    except PermissionError:
        os.unlink(tmp)
        print(json.dumps({"error": "Permission denied"}))
        sys.exit(1)

    st = os.stat(target)
    print(json.dumps({"conflict": False, "size": st.st_size, "modified": st.st_mtime}))
""")


def _write_remote_file_fallback(
    host: str, work_dir: str, body: FileWriteRequest
) -> FileWriteResponse:
    """Write a file on a remote host via SSH (one-shot fallback)."""
    content_b64 = base64.b64encode(body.content.encode("utf-8")).decode("ascii")
    script = _REMOTE_WRITE_SCRIPT.replace("__CONTENT_B64__", content_b64)

    mtime_arg = str(body.expected_mtime) if body.expected_mtime is not None else "none"
    create_arg = "true" if body.create else "false"

    _acquire_ssh_slot(host)
    try:
        result = _run_ssh_bytes(
            host,
            script.encode(),
            [work_dir, body.path, mtime_arg, create_arg],
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Remote file write timed out")
    finally:
        _release_ssh_slot(host)

    stdout_str = (
        result.stdout if isinstance(result.stdout, str) else result.stdout.decode(errors="replace")
    )
    stderr_str = (
        result.stderr if isinstance(result.stderr, str) else result.stderr.decode(errors="replace")
    )

    if result.returncode != 0:
        try:
            err = json.loads(stdout_str)
            detail = err.get("error", "Remote write failed")
        except (json.JSONDecodeError, KeyError):
            detail = stderr_str.strip() or "Remote write failed"
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        if "permission denied" in detail.lower():
            raise HTTPException(status_code=403, detail=detail)
        raise HTTPException(status_code=502, detail=detail)

    try:
        data = json.loads(stdout_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="Invalid response from remote host")

    return _build_write_response(body.path, data)


def _build_write_response(path: str, data: dict) -> FileWriteResponse:
    """Build a FileWriteResponse from parsed remote data."""
    if data.get("conflict"):
        return FileWriteResponse(
            path=path,
            size=data["size"],
            modified=data["modified"],
            conflict=True,
        )
    return FileWriteResponse(
        path=path,
        size=data["size"],
        modified=data["modified"],
        conflict=False,
    )


def _write_remote_file(host: str, work_dir: str, body: FileWriteRequest) -> FileWriteResponse:
    """Write a file on a remote host, preferring the persistent server."""
    t0 = time.monotonic()
    try:
        server = get_remote_file_server(host)
        content_b64 = base64.b64encode(body.content.encode("utf-8")).decode("ascii")
        data = server.execute(
            {
                "action": "write_file",
                "work_dir": work_dir,
                "path": body.path,
                "content_b64": content_b64,
                "expected_mtime": body.expected_mtime,
                "create": body.create,
            }
        )
        if "error" in data:
            detail = data["error"]
            if "not found" in detail.lower():
                raise HTTPException(status_code=404, detail=detail)
            if "permission denied" in detail.lower():
                raise HTTPException(status_code=403, detail=detail)
            raise HTTPException(status_code=502, detail=detail)
        elapsed = time.monotonic() - t0
        logger.info(
            "write_remote_file %s:%s took %.3fs (server=persistent)",
            host,
            body.path,
            elapsed,
        )
        resp = _build_write_response(body.path, data)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Persistent server failed for write_file on %s, falling back: %s",
            host,
            exc,
        )
        resp = _write_remote_file_fallback(host, work_dir, body)
        elapsed = time.monotonic() - t0
        logger.info(
            "write_remote_file %s:%s took %.3fs (server=fallback)",
            host,
            body.path,
            elapsed,
        )

    # Invalidate content cache for this file (regardless of method used)
    if not resp.conflict:
        for key in list(_remote_content_cache.keys()):
            if f"::{body.path}::" in key:
                _remote_content_cache.pop(key, None)

    return resp


# ---------------------------------------------------------------------------
# Raw file serving (for images etc.)
# ---------------------------------------------------------------------------
_MAX_RAW_SIZE = 10 * 1024 * 1024  # 10 MB


@router.get("/sessions/{session_id}/files/raw")
def read_file_raw(
    session_id: str,
    path: str = Query(..., description="Relative path to the file"),
    db=Depends(get_db),
) -> Response:
    """Serve raw file bytes with the correct Content-Type (for images, etc.)."""
    _check_rate_limit(session_id)
    _validate_path(path)
    info = _resolve_session(db, session_id)

    content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"

    if info.is_remote:
        return _read_remote_raw(info.host, info.work_dir, path, content_type)

    abs_path = os.path.normpath(os.path.join(info.work_dir, path))
    if not abs_path.startswith(os.path.normpath(info.work_dir)):
        raise HTTPException(status_code=400, detail="Path outside work_dir")
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="File not found")
    if os.path.getsize(abs_path) > _MAX_RAW_SIZE:
        raise HTTPException(status_code=413, detail="File too large (>10MB)")

    with open(abs_path, "rb") as f:
        data = f.read()
    return Response(content=data, media_type=content_type)


def _read_remote_raw(host: str, work_dir: str, path: str, content_type: str) -> Response:
    """Read raw file bytes from a remote host via SSH."""
    # Use a simple cat command piped through SSH
    remote_path = os.path.normpath(os.path.join(work_dir, path))
    if not remote_path.startswith(os.path.normpath(work_dir)):
        raise HTTPException(status_code=400, detail="Path outside work_dir")

    _acquire_ssh_slot(host)
    try:
        result = subprocess.run(
            ["ssh", *_SSH_OPTS, host, "cat", remote_path],
            capture_output=True,
            timeout=_SSH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Remote file read timed out")
    finally:
        _release_ssh_slot(host)

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        if "No such file" in stderr:
            raise HTTPException(status_code=404, detail="File not found")
        raise HTTPException(status_code=502, detail=stderr or "Remote read failed")

    if len(result.stdout) > _MAX_RAW_SIZE:
        raise HTTPException(status_code=413, detail="File too large (>10MB)")

    return Response(content=result.stdout, media_type=content_type)
