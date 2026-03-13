"""Utilities for file upload via drag-and-drop (base64 JSON body)."""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

# Server-side hard limit (50 MB).
MAX_FILE_SIZE = 50 * 1024 * 1024

SUPPORTED_EXTENSIONS = {
    # Python / JS / TS
    ".py",
    ".pyi",
    ".pyw",
    ".ipynb",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".cjs",
    ".mjs",
    ".d.ts",
    # Data / config
    ".json",
    ".jsonl",
    ".ndjson",
    ".yaml",
    ".yml",
    ".toml",
    ".cfg",
    ".ini",
    ".conf",
    ".env",
    ".properties",
    # Docs / text
    ".md",
    ".mdx",
    ".txt",
    ".rst",
    ".csv",
    ".tsv",
    ".log",
    ".tex",
    ".bib",
    ".org",
    ".adoc",
    # Web
    ".html",
    ".css",
    ".scss",
    ".less",
    ".styl",
    ".svg",
    ".vue",
    ".svelte",
    ".astro",
    ".ejs",
    ".hbs",
    ".erb",
    ".pug",
    ".j2",
    ".jinja",
    ".jinja2",
    # Shell
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".ps1",
    ".bat",
    ".cmd",
    # Systems / compiled
    ".rs",
    ".go",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".fs",
    ".java",
    ".kt",
    ".scala",
    ".gradle",
    ".gradle.kts",
    ".sbt",
    ".swift",
    ".m",
    ".mm",
    # Functional / ML
    ".hs",
    ".ml",
    ".mli",
    ".clj",
    ".cljs",
    ".elm",
    ".ex",
    ".exs",
    ".erl",
    # Scripting
    ".rb",
    ".php",
    ".lua",
    ".r",
    ".jl",
    ".pl",
    ".pm",
    ".dart",
    # Modern / niche
    ".zig",
    ".nim",
    ".v",
    ".vhdl",
    ".sol",
    ".move",
    ".asm",
    ".s",
    # Query / schema
    ".sql",
    ".graphql",
    ".gql",
    ".proto",
    # Markup / config
    ".xml",
    ".plist",
    ".cmake",
    ".tf",
    ".hcl",
    ".nix",
    ".dhall",
    # VCS / build
    ".lock",
    ".sum",
    ".patch",
    ".diff",
    # Documents
    ".pdf",
}

KNOWN_EXTENSIONLESS = {
    "Dockerfile",
    "Makefile",
    "Rakefile",
    "Gemfile",
    "Procfile",
    "Vagrantfile",
    "Justfile",
    "Brewfile",
    "Taskfile",
    "CMakeLists.txt",
    "LICENSE",
    "CHANGELOG",
    "README",
    "AUTHORS",
    "CONTRIBUTORS",
    "CODEOWNERS",
}

KNOWN_DOTFILES = {
    ".gitignore",
    ".gitattributes",
    ".gitmodules",
    ".editorconfig",
    ".eslintrc",
    ".prettierrc",
    ".dockerignore",
    ".npmrc",
    ".nvmrc",
    ".env",
    ".babelrc",
    ".browserslistrc",
    ".stylelintrc",
    ".flake8",
    ".pylintrc",
    ".rubocop.yml",
}

# Regex for allowed characters in sanitized filenames.
_SAFE_CHARS = re.compile(r"[^a-zA-Z0-9._-]")
_COLLAPSE_UNDERSCORES = re.compile(r"_+")


def sanitize_filename(name: str) -> str:
    """Sanitize a user-provided filename for safe filesystem use.

    Critical for security because file_sync.py uses shell commands like
    ``cat > {remote_path}`` — any special characters could be exploited.

    Steps:
    1. Strip path components (basename only).
    2. Replace unsafe characters with ``_``.
    3. Collapse runs of underscores.
    4. Strip leading dots and underscores (prevent hidden files / confusion).
    5. Fall back to ``uploaded_file`` if nothing remains.
    """
    # Strip path traversal
    base = os.path.basename(name)
    # Replace unsafe chars
    safe = _SAFE_CHARS.sub("_", base)
    # Collapse underscores
    safe = _COLLAPSE_UNDERSCORES.sub("_", safe)
    # Strip leading dots/underscores
    safe = safe.lstrip("._")
    return safe or "uploaded_file"


def is_supported_file(filename: str) -> bool:
    """Check whether a filename is an accepted type for file upload.

    Same 3-branch logic as the frontend ``isSupportedDropFile``.
    """
    lower = filename.lower()
    dot_idx = lower.rfind(".")

    if dot_idx > 0:
        ext = lower[dot_idx:]
        return ext in SUPPORTED_EXTENSIONS

    if dot_idx == 0:
        # Dotfile
        return lower in KNOWN_DOTFILES

    # No dot — extensionless
    return filename in KNOWN_EXTENSIONLESS


def save_uploaded_file(
    file_bytes: bytes,
    original_filename: str,
    dest_dir: str,
) -> str:
    """Save uploaded file bytes to *dest_dir* with a sanitized name.

    Uses ``open(path, 'xb')`` (exclusive create) to avoid TOCTOU races
    when two concurrent uploads produce the same filename.

    Returns the absolute path of the saved file.
    """
    safe_name = sanitize_filename(original_filename)
    os.makedirs(dest_dir, exist_ok=True)

    path = os.path.join(dest_dir, safe_name)

    # Try exclusive create; on collision append _1, _2, ...
    # Use os.open() (low-level syscall) to avoid interference from
    # any builtins.open monkeypatching in the test suite.
    for attempt in range(100):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                os.write(fd, file_bytes)
            finally:
                os.close(fd)
            return path
        except FileExistsError:
            name_part, dot, ext_part = safe_name.rpartition(".")
            if dot:
                candidate = f"{name_part}_{attempt + 1}.{ext_part}"
            else:
                candidate = f"{safe_name}_{attempt + 1}"
            path = os.path.join(dest_dir, candidate)

    # Extremely unlikely — 100 collisions
    raise OSError(f"Could not save file after 100 attempts: {safe_name}")
