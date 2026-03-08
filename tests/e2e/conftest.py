"""E2E test fixtures: temp DB, uvicorn server, Playwright browser.

Supports parallel execution via pytest-xdist by isolating each worker:
- Each worker gets its own database file
- Each worker gets its own server on a unique port
- Each worker gets its own browser instance
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent


def pytest_collection_modifyitems(items):
    """E2E tests need longer timeouts and real I/O (server, browser)."""
    for item in items:
        if not item.get_closest_marker("timeout"):
            item.add_marker(pytest.mark.timeout(60))
        item.add_marker(pytest.mark.allow_subprocess)
        item.add_marker(pytest.mark.allow_network)


# ---------------------------------------------------------------------------
# Worker Isolation (for pytest-xdist parallel execution)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def worker_id(request):
    """Get xdist worker ID for isolation, or 'master' if running sequentially."""
    if hasattr(request.config, "workerinput"):
        return request.config.workerinput["workerid"]
    return "master"


@pytest.fixture(scope="module")
def server_port(worker_id):
    """Unique port per worker to avoid collisions in parallel runs."""
    if worker_id == "master":
        return 8099
    # gw0 -> 8100, gw1 -> 8101, etc.
    worker_num = int(worker_id.replace("gw", ""))
    return 8100 + worker_num


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def e2e_db_path(worker_id):
    """Create a temporary SQLite DB with migrations + seed data for E2E tests.

    Each xdist worker gets its own isolated database.
    """
    sys.path.insert(0, str(PROJECT_ROOT))

    from orchestrator.state.db import get_connection
    from orchestrator.state.migrations.runner import apply_migrations
    from scripts.seed_db import seed_all

    fd, path = tempfile.mkstemp(suffix=f"_{worker_id}.db")
    os.close(fd)

    conn = get_connection(path)
    apply_migrations(conn)
    seed_all(conn)

    # --- Sessions (3 with different statuses) ---
    conn.execute(
        "INSERT INTO sessions (id, name, host, status, work_dir) "
        "VALUES ('s1', 'worker-alpha', 'localhost', 'working', '/src/project-a')"
    )
    conn.execute(
        "INSERT INTO sessions (id, name, host, status, work_dir) "
        "VALUES ('s2', 'worker-beta', 'localhost', 'idle', '/src/project-b')"
    )
    conn.execute(
        "INSERT INTO sessions (id, name, host, status) "
        "VALUES ('s3', 'worker-gamma', 'rdev1.example.com', 'disconnected')"
    )

    conn.commit()
    conn.close()

    yield path

    # Cleanup DB and WAL/SHM journal files
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def server(e2e_db_path, server_port):
    """Start a uvicorn subprocess and wait for it to be ready.

    Each xdist worker gets its own server on a unique port.
    """
    import httpx

    env = {
        **os.environ,
        "ORCHESTRATOR_DB_PATH": e2e_db_path,
        "ORCHESTRATOR_SKIP_RECONCILE": "1",
    }

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "orchestrator.api.app:create_app",
            "--factory",
            "--port",
            str(server_port),
            "--host",
            "127.0.0.1",
        ],
        env=env,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    base_url = f"http://127.0.0.1:{server_port}"

    # Poll until ready
    for _ in range(40):
        try:
            r = httpx.get(f"{base_url}/api/health", timeout=1.0)
            if r.status_code == 200:
                break
        except (httpx.ConnectError, httpx.ReadError):
            pass
        time.sleep(0.25)
    else:
        proc.kill()
        stdout = proc.stdout.read().decode() if proc.stdout else ""
        stderr = proc.stderr.read().decode() if proc.stderr else ""
        pytest.fail(
            f"Server did not start on port {server_port}.\nstdout: {stdout}\nstderr: {stderr}"
        )

    yield base_url

    # Teardown
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------------------
# Playwright
# ---------------------------------------------------------------------------

SCREENSHOT_DIR = Path(__file__).parent.parent.parent / "tmp" / "screenshots"


@pytest.fixture(scope="module")
def browser_instance():
    """Launch a browser for the e2e test module.

    Uses *module* scope (not session) so that Playwright's internal event-loop
    greenlet is torn down before pytest-asyncio tries to create ``Runner``
    instances for later async tests.  Since there is only a single e2e test
    module this has no practical performance impact.
    """
    from playwright.sync_api import sync_playwright

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    pw = sync_playwright().start()
    browser = pw.chromium.launch()

    yield browser

    browser.close()
    pw.stop()

    # Playwright's sync_api uses greenlets that leave the asyncio running-loop
    # flag set at the C level.  Clear it so pytest-asyncio works afterwards.
    import asyncio

    asyncio._set_running_loop(None)
    # Replace any stale event loop with a fresh one
    asyncio.set_event_loop(asyncio.new_event_loop())

    # Clean up screenshots after all tests complete
    if SCREENSHOT_DIR.exists():
        for f in SCREENSHOT_DIR.glob("*.png"):
            try:
                f.unlink()
            except OSError:
                pass


@pytest.fixture()
def page(browser_instance, server):
    """Create a fresh page per test, navigated to the dashboard."""
    ctx = browser_instance.new_context(viewport={"width": 1400, "height": 900})
    pg = ctx.new_page()

    # Collect console errors
    pg._console_errors = []
    pg.on(
        "console", lambda msg: pg._console_errors.append(msg.text) if msg.type == "error" else None
    )

    pg.goto(server + "/")
    pg.wait_for_load_state("networkidle")
    # Wait for React hydration — stats bar is one of the first things rendered
    pg.wait_for_selector("[data-testid='stats-bar']", timeout=10000)
    pg.wait_for_timeout(500)  # Reduced from 1500ms for faster tests

    yield pg

    ctx.close()


def screenshot(page, name: str, full_page: bool = False):
    """Save a named screenshot."""
    path = SCREENSHOT_DIR / f"{name}.png"
    page.screenshot(path=str(path), full_page=full_page)
    return path
