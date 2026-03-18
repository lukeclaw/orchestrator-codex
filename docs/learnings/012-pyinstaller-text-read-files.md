# 012 — PyInstaller misses files loaded via text read

**Date**: 2026-03-18
**Bug introduced**: Commit `2e66579` (refactor: split remote_worker_server.py into focused modules)
**Bug fixed**: `orchestrator.spec` — added `_rws_daemon.py` to `datas`

## What happened

The refactor in `2e66579` split `remote_worker_server.py` (2,377 lines) into focused modules. The daemon script — previously a ~1,500-line string literal inside the imported module — was extracted into a standalone file `_rws_daemon.py`. The new `_rws_client.py` loads it via `Path(__file__).parent / "_rws_daemon.py"` + `.read_text()`.

Because `_rws_daemon.py` is read as raw text (not imported), PyInstaller's static import analysis cannot detect it. The refactor did not update `orchestrator.spec` to add the file to `datas`, so the packaged app was missing the entire `orchestrator/terminal/` data directory.

## Why it wasn't caught

- The development environment runs from source, where the file is always present.
- No CI step verified the PyInstaller bundle contents.
- The previous architecture (inline string literal) never needed a `datas` entry, so there was no prior entry to remind the developer.

## Root cause

When a Python module is refactored to load a file via filesystem read (`open()`, `Path.read_text()`) instead of `import`, PyInstaller silently drops the file from the bundle. The spec file must be updated in the same commit.

## Rule

**When extracting code into a file that is read at runtime (not imported), always add it to `datas` in `orchestrator.spec` in the same commit.** Treat the spec file as part of any refactor that changes how files are loaded.

**Automated guard**: `tests/unit/test_pyinstaller_spec.py` scans the codebase for `Path(__file__).parent / "..."` + `.read_text()` patterns and asserts each referenced file has a matching `datas` entry. This test will fail if a future refactor introduces a new runtime-read file without updating the spec.

Candidate patterns to watch for:
- `Path(...).read_text()` or `open(...)` loading `.py`, `.sql`, `.yaml`, `.json` files
- `subprocess.run([sys.executable, script_path, ...])` launching a script by path
- Any file resolved relative to `__file__` that isn't a standard Python import
