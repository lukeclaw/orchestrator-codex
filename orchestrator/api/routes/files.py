"""File explorer API — directory listing + file content reading."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.state.repositories import sessions as repo

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Rate limiting (simple in-memory counter per session)
# ---------------------------------------------------------------------------
_rate_limits: dict[str, list[float]] = {}
_RATE_LIMIT = 20
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


def _resolve_work_dir(db, session_id: str) -> str:
    """Look up session and return its work_dir (must be local)."""
    session = repo.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.work_dir:
        raise HTTPException(status_code=400, detail="Session has no work_dir")
    work_dir = session.work_dir
    if not os.path.isdir(work_dir):
        raise HTTPException(status_code=400, detail="work_dir does not exist on disk")
    return work_dir


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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/sessions/{session_id}/files")
def list_files(
    session_id: str,
    path: str = Query(default=".", description="Relative path within work_dir"),
    depth: int = Query(default=1, ge=1, le=3),
    show_ignored: bool = Query(default=False),
    db=Depends(get_db),
) -> DirectoryResponse:
    """List directory contents with optional git status decorations."""
    _check_rate_limit(session_id)
    _validate_path(path)
    work_dir = _resolve_work_dir(db, session_id)

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
        work_dir=work_dir,
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

                fe = FileEntry(
                    name=name,
                    path=rel_path,
                    is_dir=is_dir,
                    size=size,
                    modified=modified,
                    children_count=children_count,
                    human_size=_human_size(size) if size is not None else None,
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
    db=Depends(get_db),
) -> FileContentResponse:
    """Read file contents with truncation and binary detection."""
    _check_rate_limit(session_id)
    _validate_path(path)
    work_dir = _resolve_work_dir(db, session_id)

    abs_path = os.path.normpath(os.path.join(work_dir, path))
    if not abs_path.startswith(os.path.normpath(work_dir)):
        raise HTTPException(status_code=400, detail="Path outside work_dir")

    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="File not found")

    file_size = os.path.getsize(abs_path)
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
    )
