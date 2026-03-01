"""File explorer API — directory listing + file content reading."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import sessions as repo
from orchestrator.terminal.file_sync import _SSH_OPTS
from orchestrator.terminal.remote_worker_server import (
    ensure_rws_starting,
    get_remote_worker_server,
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
# Remote error helpers
# ---------------------------------------------------------------------------
def _raise_remote_error(exc: Exception) -> None:
    """Raise an appropriate HTTPException for a remote connection error.

    Returns 503 when the remote worker server is still starting up (transient),
    502 for all other connection failures.
    """
    msg = str(exc)
    status = 503 if "Connecting to remote host" in msg else 502
    raise HTTPException(status_code=status, detail=f"Remote connection error: {exc}")


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

    # Eagerly start the RWS so it's ready by the time
    # the first file operation completes and the user clicks something else.
    if remote:
        ensure_rws_starting(session.host)

    return SessionInfo(work_dir=work_dir, host=session.host, is_remote=remote)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------
def _get_git_status(work_dir: str) -> tuple[dict[str, str], bool]:
    """Return {relative_path: status_string} and git_available flag.

    Always includes --ignored so ignored files can be shown greyed out.
    Uses a 5 s in-memory cache.  Subprocess has a 3 s timeout.
    """
    cache_key = f"{work_dir}::git_status"
    now = time.monotonic()
    cached = _git_cache.get(cache_key)
    if cached and now - cached[0] < _GIT_CACHE_TTL:
        return cached[1], True

    cmd = ["git", "status", "--porcelain=v1", "-z", "--ignored"]

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
        path_str = entry[3:].rstrip("/")
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


def _list_remote_dir(
    host: str, work_dir: str, path: str, show_hidden: bool, depth: int = 1
) -> tuple[list[FileEntry], bool]:
    """List a directory on a remote host via RWS."""
    t0 = time.monotonic()
    try:
        rws = get_remote_worker_server(host)
        data = rws.execute(
            {
                "action": "list_dir",
                "work_dir": work_dir,
                "path": path,
                "show_hidden": show_hidden,
                "depth": depth,
            }
        )
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        _raise_remote_error(exc)

    if "error" in data:
        detail = data["error"]
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=502, detail=detail)

    entries = _parse_remote_entries(data["entries"])
    elapsed = time.monotonic() - t0
    logger.info("list_remote_dir %s:%s took %.3fs", host, path, elapsed)
    return entries, data.get("git_available", False)


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
    """Read file content from a remote host via RWS."""
    t0 = time.monotonic()
    read_cmd = {
        "action": "read_file",
        "work_dir": work_dir,
        "path": path,
        "max_lines": max_lines,
    }

    try:
        rws = get_remote_worker_server(host)
        data = rws.execute(read_cmd)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        _raise_remote_error(exc)

    if "error" in data:
        detail = data["error"]
        code = data.get("code", 502)
        if "not found" in detail.lower():
            code = 404
        elif "too large" in detail.lower():
            code = 413
        raise HTTPException(status_code=code, detail=detail)

    elapsed = time.monotonic() - t0
    logger.info("read_remote_file %s:%s took %.3fs", host, path, elapsed)
    return _build_file_content_response(path, data)


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
    show_hidden: bool,
    refresh: bool,
    depth: int = 1,
) -> tuple[list[FileEntry], bool]:
    """Cached wrapper around _list_remote_dir."""
    global _cache_request_counter
    _cache_request_counter += 1
    if _cache_request_counter % 10 == 0:
        _cleanup_remote_caches()

    cache_key = f"{host}::{work_dir}::{path}::{show_hidden}::{depth}"
    if not refresh:
        cached = _remote_dir_cache.get(cache_key)
        if cached:
            ts, entries, git_avail = cached
            if time.monotonic() - ts < _REMOTE_DIR_CACHE_TTL:
                logger.info("list_remote_dir %s:%s cache=hit", host, path)
                return entries, git_avail

    entries, git_available = _list_remote_dir(host, work_dir, path, show_hidden, depth)
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
    show_hidden: bool = Query(default=True),
    refresh: bool = Query(default=False),
    db=Depends(get_db),
) -> DirectoryResponse:
    """List directory contents with optional git status decorations."""
    _check_rate_limit(session_id)
    _validate_path(path)
    info = _resolve_session(db, session_id)

    if info.is_remote:
        entries, git_available = _list_remote_dir_cached(
            info.host, info.work_dir, path, show_hidden, refresh, depth
        )
    else:
        work_dir = info.work_dir
        target = os.path.normpath(os.path.join(work_dir, path))
        # Ensure we stay inside work_dir
        if not target.startswith(os.path.normpath(work_dir)):
            raise HTTPException(status_code=400, detail="Path outside work_dir")

        if not os.path.isdir(target):
            raise HTTPException(status_code=404, detail="Directory not found")

        entries = _scan_dir(target, work_dir, show_hidden, depth, current_depth=1)

        # Git status — always include ignored files so they appear greyed out
        git_statuses, git_available = _get_git_status(work_dir)
        if git_available:
            # If the listed directory itself is untracked/ignored, its children
            # won't appear individually in git status — propagate downward.
            parent_status = git_statuses.get(path) if path != "." else None
            initial_inherit = parent_status if parent_status in ("untracked", "ignored") else None
            _apply_git_status(entries, git_statuses, initial_inherit)

    return DirectoryResponse(
        work_dir=info.work_dir,
        path=path,
        entries=entries,
        git_available=git_available,
    )


def _scan_dir(
    abs_path: str,
    work_dir: str,
    show_hidden: bool,
    max_depth: int,
    current_depth: int,
) -> list[FileEntry]:
    """Scan a directory and return sorted entries (dirs first, alpha)."""
    entries: list[FileEntry] = []
    try:
        with os.scandir(abs_path) as it:
            for entry in it:
                name = entry.name
                # Skip dotfiles unless show_hidden is on
                if not show_hidden and name.startswith(".") and name != ".":
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
                        show_hidden,
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


def _apply_git_status(
    entries: list[FileEntry],
    statuses: dict[str, str],
    inherited_status: str | None = None,
) -> None:
    """Attach git_status to entries that appear in git status output.

    When a directory is "untracked" or "ignored", git only reports the
    directory itself — not its individual children.  Like VS Code, we
    propagate that status **downward** so every descendant inherits it.
    """
    for entry in entries:
        path = entry.path
        if path in statuses:
            entry.git_status = statuses[path]
        elif entry.is_dir:
            # Propagate: if any child has status, bubble up highest severity.
            # But only mark a folder as "ignored" if git explicitly reported it
            # (e.g. "!! dirname/") — not merely because all known children are
            # ignored, since tracked clean files don't appear in git status.
            prefix = path + "/"
            child_statuses = [s for p, s in statuses.items() if p.startswith(prefix)]
            if child_statuses:
                non_ignored = [s for s in child_statuses if s != "ignored"]
                if non_ignored:
                    entry.git_status = _highest_severity(non_ignored)
                # else: all children are ignored, but we don't propagate "ignored"
                # to the folder — only git's direct "!! dir/" marking does that.
            elif inherited_status:
                # No direct match and no children in git status — inherit from parent
                entry.git_status = inherited_status
        elif inherited_status:
            # File with no direct match — inherit from parent
            entry.git_status = inherited_status

        # Determine what to propagate downward: only "untracked" and "ignored"
        # warrant downward propagation (git collapses these into a single
        # directory entry and doesn't list individual children).
        propagate = None
        if entry.git_status in ("untracked", "ignored"):
            propagate = entry.git_status

        # Recurse into pre-fetched children
        if entry.children:
            _apply_git_status(entry.children, statuses, propagate)


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
    """Write a file on a remote host via RWS."""
    t0 = time.monotonic()
    content_b64 = base64.b64encode(body.content.encode("utf-8")).decode("ascii")
    write_cmd = {
        "action": "write_file",
        "work_dir": work_dir,
        "path": body.path,
        "content_b64": content_b64,
        "expected_mtime": body.expected_mtime,
        "create": body.create,
    }

    try:
        rws = get_remote_worker_server(host)
        data = rws.execute(write_cmd)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        _raise_remote_error(exc)

    if "error" in data:
        detail = data["error"]
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        if "permission denied" in detail.lower():
            raise HTTPException(status_code=403, detail=detail)
        raise HTTPException(status_code=502, detail=detail)

    resp = _build_write_response(body.path, data)
    elapsed = time.monotonic() - t0
    logger.info("write_remote_file %s:%s took %.3fs", host, body.path, elapsed)

    # Invalidate content cache for this file
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
    """Read raw file bytes from a remote host via RWS."""
    try:
        rws = get_remote_worker_server(host)
        data = rws.execute(
            {
                "action": "read_file_raw",
                "work_dir": work_dir,
                "path": path,
                "max_size": _MAX_RAW_SIZE,
            }
        )
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        _raise_remote_error(exc)

    if "error" in data:
        detail = data["error"]
        code = data.get("code", 502)
        if "not found" in detail.lower():
            code = 404
        elif "too large" in detail.lower():
            code = 413
        raise HTTPException(status_code=code, detail=detail)

    raw_bytes = base64.b64decode(data["content_b64"])
    return Response(content=raw_bytes, media_type=content_type)


# ---------------------------------------------------------------------------
# Delete file/directory
# ---------------------------------------------------------------------------
class DeleteResponse(BaseModel):
    status: str


def _validate_not_root(path: str) -> None:
    """Reject attempts to delete/move the work_dir root itself."""
    normalized = os.path.normpath(path)
    if normalized in (".", ""):
        raise HTTPException(status_code=400, detail="Cannot operate on work_dir root")


@router.delete("/sessions/{session_id}/files")
def delete_file(
    session_id: str,
    path: str = Query(..., description="Relative path to delete"),
    db=Depends(get_db),
) -> DeleteResponse:
    """Delete a file or directory."""
    _check_rate_limit(session_id)
    _validate_path(path)
    _validate_not_root(path)
    info = _resolve_session(db, session_id)

    if info.is_remote:
        return _delete_remote(info.host, info.work_dir, path)
    return _delete_local(info.work_dir, path)


def _delete_local(work_dir: str, path: str) -> DeleteResponse:
    abs_path = os.path.normpath(os.path.join(work_dir, path))
    if not abs_path.startswith(os.path.normpath(work_dir) + os.sep):
        raise HTTPException(status_code=400, detail="Path outside work_dir")
    if os.path.isdir(abs_path):
        shutil.rmtree(abs_path)
    elif os.path.isfile(abs_path) or os.path.islink(abs_path):
        os.remove(abs_path)
    else:
        raise HTTPException(status_code=404, detail="Not found")
    # Invalidate caches
    for key in list(_remote_content_cache.keys()):
        if f"::{path}::" in key:
            _remote_content_cache.pop(key, None)
    return DeleteResponse(status="ok")


def _delete_remote(host: str, work_dir: str, path: str) -> DeleteResponse:
    """Delete a file or directory on a remote host via RWS."""
    delete_cmd = {"action": "delete", "work_dir": work_dir, "path": path}

    try:
        rws = get_remote_worker_server(host)
        data = rws.execute(delete_cmd)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        _raise_remote_error(exc)

    if "error" in data:
        detail = data["error"]
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=502, detail=detail)

    return DeleteResponse(status="ok")


# ---------------------------------------------------------------------------
# Move / Rename file or directory
# ---------------------------------------------------------------------------
class MoveRequest(BaseModel):
    from_path: str
    to_path: str


class MoveResponse(BaseModel):
    status: str


@router.post("/sessions/{session_id}/files/move")
def move_file(
    session_id: str,
    body: MoveRequest,
    db=Depends(get_db),
) -> MoveResponse:
    """Move or rename a file/directory."""
    _check_rate_limit(session_id)
    _validate_path(body.from_path)
    _validate_path(body.to_path)
    _validate_not_root(body.from_path)
    info = _resolve_session(db, session_id)

    if info.is_remote:
        return _move_remote(info.host, info.work_dir, body.from_path, body.to_path)
    return _move_local(info.work_dir, body.from_path, body.to_path)


def _move_local(work_dir: str, from_path: str, to_path: str) -> MoveResponse:
    norm_work = os.path.normpath(work_dir)
    src = os.path.normpath(os.path.join(work_dir, from_path))
    dst = os.path.normpath(os.path.join(work_dir, to_path))
    if not src.startswith(norm_work + os.sep):
        raise HTTPException(status_code=400, detail="Source path outside work_dir")
    if not dst.startswith(norm_work + os.sep):
        raise HTTPException(status_code=400, detail="Destination path outside work_dir")
    if not os.path.exists(src):
        raise HTTPException(status_code=404, detail="Not found")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    # Invalidate caches
    for key in list(_remote_content_cache.keys()):
        if f"::{from_path}::" in key:
            _remote_content_cache.pop(key, None)
    return MoveResponse(status="ok")


def _move_remote(
    host: str,
    work_dir: str,
    from_path: str,
    to_path: str,
) -> MoveResponse:
    """Move or rename a file/directory on a remote host via RWS."""
    move_cmd = {
        "action": "move",
        "work_dir": work_dir,
        "from_path": from_path,
        "to_path": to_path,
    }

    try:
        rws = get_remote_worker_server(host)
        data = rws.execute(move_cmd)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        _raise_remote_error(exc)

    if "error" in data:
        detail = data["error"]
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=502, detail=detail)

    return MoveResponse(status="ok")


# ---------------------------------------------------------------------------
# Create directory (mkdir)
# ---------------------------------------------------------------------------
class MkdirRequest(BaseModel):
    path: str


class MkdirResponse(BaseModel):
    status: str


@router.post("/sessions/{session_id}/files/mkdir")
def mkdir(
    session_id: str,
    body: MkdirRequest,
    db=Depends(get_db),
) -> MkdirResponse:
    """Create a directory (including intermediate parents)."""
    _check_rate_limit(session_id)
    _validate_path(body.path)
    _validate_not_root(body.path)
    info = _resolve_session(db, session_id)

    if info.is_remote:
        return _mkdir_remote(info.host, info.work_dir, body.path)
    return _mkdir_local(info.work_dir, body.path)


def _mkdir_local(work_dir: str, path: str) -> MkdirResponse:
    abs_path = os.path.normpath(os.path.join(work_dir, path))
    if not abs_path.startswith(os.path.normpath(work_dir) + os.sep):
        raise HTTPException(status_code=400, detail="Path outside work_dir")
    os.makedirs(abs_path, exist_ok=True)
    return MkdirResponse(status="ok")


def _mkdir_remote(host: str, work_dir: str, path: str) -> MkdirResponse:
    """Create a directory on a remote host via RWS."""
    mkdir_cmd = {"action": "mkdir", "work_dir": work_dir, "path": path}

    try:
        rws = get_remote_worker_server(host)
        data = rws.execute(mkdir_cmd)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        _raise_remote_error(exc)

    if "error" in data:
        detail = data["error"]
        raise HTTPException(status_code=502, detail=detail)

    return MkdirResponse(status="ok")
