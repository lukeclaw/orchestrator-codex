"""Verify orchestrator.spec bundles all files loaded via runtime filesystem reads.

PyInstaller only auto-detects Python imports.  Files loaded via Path.read_text(),
open(), etc. must be listed in the spec's `datas` list.  This test scans the
codebase for such patterns and asserts each resolved file has a matching datas
entry — preventing a repeat of the v1.4.x startup crash caused by _rws_daemon.py
being missing from the bundle.

See docs/learnings/012-pyinstaller-text-read-files.md for context.
"""

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SPEC_FILE = REPO_ROOT / "orchestrator.spec"
ORCHESTRATOR_PKG = REPO_ROOT / "orchestrator"


def _parse_datas_from_spec() -> set[str]:
    """Extract source paths from the datas=[ ... ] block in orchestrator.spec.

    Returns a set of source path strings (as written in the spec), e.g.
    {"agents", "orchestrator/web/dist", "orchestrator/terminal/_rws_daemon.py", ...}.
    """
    text = SPEC_FILE.read_text()
    # Match the datas=[...] block — may contain *migration_files or similar splats
    m = re.search(r"datas\s*=\s*\[(.+?)\]", text, re.DOTALL)
    assert m, "Could not find datas=[ ... ] in orchestrator.spec"
    block = m.group(1)

    sources: set[str] = set()
    # Extract source paths from tuples like ("path/to/file", "dest")
    for src in re.findall(r'\("([^"]+)",\s*"[^"]*"\)', block):
        sources.add(src)
    # Also handle the *migration_files splat — migration .sql files are collected
    # via glob in the spec, so mark the directory as covered.
    if "*migration_files" in block:
        sources.add("orchestrator/state/migrations/versions")
    return sources


def _datas_covers(datas_sources: set[str], file_path: Path) -> bool:
    """Check if a file is covered by any datas entry (exact file or parent dir)."""
    rel = str(file_path.relative_to(REPO_ROOT))
    for src in datas_sources:
        if rel == src:
            return True
        # Directory entry covers all files under it
        if rel.startswith(src + "/"):
            return True
    return False


def _find_runtime_file_reads() -> list[tuple[Path, str]]:
    """Scan orchestrator/ for files loaded via Path(__file__)-relative reads.

    Detects patterns like:
      Path(__file__).parent / "filename"  (followed by .read_text / open)
      SOME_VAR = Path(__file__).parent / "dirname" / "filename"

    Returns [(python_file, referenced_file_path), ...].
    """
    results: list[tuple[Path, str]] = []

    for py_file in ORCHESTRATOR_PKG.rglob("*.py"):
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            # Look for: Path(__file__).parent / "something"
            # which may be followed by .read_text() or used with open()
            if not isinstance(node, ast.Assign):
                continue
            # We look for assignments where the value builds a path from __file__
            _check_path_construction(py_file, source, node, results)

    return results


def _check_path_construction(
    py_file: Path,
    source: str,
    node: ast.Assign,
    results: list[tuple[Path, str]],
) -> None:
    """Check if an assignment constructs a path from __file__ to a data file."""
    # Get the source line(s) for this assignment
    lines = source.splitlines()
    start = node.lineno - 1
    end = node.end_lineno if node.end_lineno else start + 1
    snippet = " ".join(lines[start:end])

    # Pattern: Path(__file__).parent / "filename" or .resolve().parent / ...
    # followed by .read_text or used in open()
    path_pattern = re.compile(r'Path\(__file__\)(?:\.resolve\(\))?\.parent\s*/\s*"([^"]+)"')
    match = path_pattern.search(snippet)
    if not match:
        return

    referenced = match.group(1)
    referenced_path = py_file.parent / referenced

    # Only flag files that exist and are NOT .py modules that would be imported
    # (.py files that are imported are auto-detected by PyInstaller)
    if not referenced_path.exists():
        return

    # Check if this file is read as text (not imported as a module)
    # Heuristic: look for .read_text(), .read_bytes(), or open() nearby,
    # or if it's a non-.py file (always data)
    if referenced_path.suffix != ".py":
        # Non-Python files are always data files
        results.append((py_file, str(referenced_path.relative_to(REPO_ROOT))))
        return

    # For .py files, check if they're read as text rather than imported
    # Look for read_text/read_bytes/open usage of the variable being assigned
    if isinstance(node.targets[0], ast.Name):
        var_name = node.targets[0].id
        if re.search(
            rf"{var_name}\.read_text\(|{var_name}\.read_bytes\(|open\({var_name}",
            source,
        ):
            results.append((py_file, str(referenced_path.relative_to(REPO_ROOT))))


class TestPyInstallerSpec:
    """Ensure orchestrator.spec bundles all runtime-read files."""

    def test_spec_file_exists(self):
        assert SPEC_FILE.exists(), "orchestrator.spec not found at repo root"

    def test_runtime_read_files_are_in_datas(self):
        """Every file loaded via Path(__file__).parent / 'x' + read_text()
        must appear in orchestrator.spec datas."""
        datas = _parse_datas_from_spec()
        runtime_reads = _find_runtime_file_reads()

        missing = []
        for py_file, data_file in runtime_reads:
            if not _datas_covers(datas, REPO_ROOT / data_file):
                missing.append(f"  {data_file}  (referenced in {py_file.relative_to(REPO_ROOT)})")

        assert not missing, (
            "Files loaded at runtime via filesystem read are missing from "
            "orchestrator.spec datas=[].\n"
            "PyInstaller won't bundle these — add them to datas.\n"
            "Missing:\n" + "\n".join(missing)
        )

    def test_datas_entries_point_to_existing_files(self):
        """Every source path in datas should exist (catch stale entries)."""
        datas = _parse_datas_from_spec()
        missing = [
            src
            for src in datas
            if not (REPO_ROOT / src).exists()
            # Skip glob-based entries that are directories
            and not any((REPO_ROOT / src).parent.glob("*"))
        ]
        assert not missing, f"orchestrator.spec datas entries point to missing files: {missing}"

    def test_migration_sql_files_are_bundled(self):
        """SQL migration files must be covered by datas (they're read at runtime)."""
        datas = _parse_datas_from_spec()
        migrations_dir = ORCHESTRATOR_PKG / "state" / "migrations" / "versions"
        if not migrations_dir.exists():
            pytest.skip("No migrations directory")

        sql_files = list(migrations_dir.glob("*.sql"))
        assert sql_files, "Expected at least one .sql migration file"

        for sql_file in sql_files:
            assert _datas_covers(datas, sql_file), f"Migration {sql_file.name} not covered by datas"
