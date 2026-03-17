# 011: Daemon PATH in Non-Interactive SSH

## Problem

The browser button in the UI always failed for new rdev remote workers. Running `orch-browser --start` directly on the remote host worked fine.

## Root Cause

The RWS daemon is launched via `ssh host python3 -u -c '...'` (non-interactive SSH), which does **not** source `.bashrc`/`.bash_profile`. On rdev, `npx` is available via volta symlinks at `/tmp/orchestrator/workers/*/node-bin/npx` (created by `ensure_rdev_node()` in `session.py`), but the daemon's PATH doesn't include these.

In `_install_chromium()`, `shutil.which("npx")` returned `None` immediately, so the handler returned `{"error": "Chromium not found and auto-install failed"}` every time.

### Secondary: Event Loop Blocking

The daemon uses a single-threaded `selectors` event loop. Handlers run synchronously (`result = handler(cmd)`). If `_install_chromium()` worked, `subprocess.run([npx, playwright, install, chromium], timeout=300)` would block the entire event loop for minutes. The client timeout was 30s, so it would always time out on first install.

### Tertiary: Blocking Call in Async Context

`_auto_start_browser_and_retry()` is `async def` but called `rws.start_browser()` (blocking socket I/O) synchronously, blocking the asyncio event loop.

## Fix

1. **`_find_npx()` helper**: Searches known locations (node-bin symlinks, volta), bootstraps Node 24 via volta if needed, falls back to `shutil.which("npx")`.
2. **Background thread install**: `handle_browser_start()` runs `_install_chromium()` in a daemon thread, returning `{"status": "installing"}` to avoid blocking the event loop.
3. **Client polling**: `start_browser()` polls every 5s on `"installing"` status, with a 300s timeout.
4. **`asyncio.to_thread()`**: Wrapped blocking `rws.start_browser()` calls in the async API route handler.

## Rules

- **Non-interactive SSH has minimal PATH.** Never assume binaries like `npx`, `node`, or `volta` are on PATH in a daemon launched via SSH. Always search known locations explicitly.
- **rdev node-bin symlinks** live at `/tmp/orchestrator/workers/*/node-bin/`. These are created by `ensure_rdev_node()` in `session.py`.
- **rdev ships volta** at `~/.volta/bin/volta` but defaults to Node 16 (too old for Playwright). Run `volta install node@24` before using npx.
- **Don't block the daemon event loop.** Long-running operations (installs, downloads) must run in a background thread.
- **Don't block asyncio from sync code.** Wrap blocking socket I/O in `asyncio.to_thread()` when called from async context.
