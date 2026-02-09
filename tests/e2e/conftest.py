"""E2E test fixtures: temp DB, uvicorn server, Playwright browser."""

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

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def e2e_db_path():
    """Create a temporary SQLite DB with migrations + seed data for E2E tests."""
    sys.path.insert(0, str(PROJECT_ROOT))

    from orchestrator.state.db import get_connection
    from orchestrator.state.migrations.runner import apply_migrations
    from scripts.seed_db import seed_all

    fd, path = tempfile.mkstemp(suffix=".db")
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

    # --- Decisions (2 with different urgencies) ---
    conn.execute(
        "INSERT INTO decisions (id, session_id, question, options, urgency, status) "
        "VALUES ('d1', 's1', 'Should we refactor the auth module before adding OAuth?', "
        "'[\"Yes, refactor first\", \"No, add OAuth directly\"]', 'high', 'pending')"
    )
    conn.execute(
        "INSERT INTO decisions (id, session_id, question, context, urgency, status) "
        "VALUES ('d2', 's2', 'PR #42 has merge conflicts. How should we resolve?', "
        "'Conflicts in src/auth.py and src/config.py', 'critical', 'pending')"
    )

    # --- Activities ---
    conn.execute(
        "INSERT INTO activities (id, session_id, event_type, event_data) "
        "VALUES ('a1', 's1', 'task.started', '{\"task\": \"Implement OAuth flow\"}')"
    )
    conn.execute(
        "INSERT INTO activities (id, session_id, event_type, event_data) "
        "VALUES ('a2', 's2', 'pr.created', '{\"pr\": \"#42 Add user auth\"}')"
    )
    conn.execute(
        "INSERT INTO activities (id, session_id, event_type, event_data) "
        "VALUES ('a3', 's1', 'session.connected', '{\"host\": \"localhost\"}')"
    )

    conn.commit()
    conn.close()

    yield path

    # Cleanup
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def server(e2e_db_path):
    """Start a uvicorn subprocess and wait for it to be ready."""
    import httpx

    port = 8099
    env = {
        **os.environ,
        "ORCHESTRATOR_DB_PATH": e2e_db_path,
        "ORCHESTRATOR_SKIP_RECONCILE": "1",
    }

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "orchestrator.api.app:create_app",
            "--factory",
            "--port", str(port),
            "--host", "127.0.0.1",
        ],
        env=env,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    base_url = f"http://127.0.0.1:{port}"

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
        pytest.fail(f"Server did not start.\nstdout: {stdout}\nstderr: {stderr}")

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


@pytest.fixture(scope="session")
def browser_instance():
    """Launch a single browser for all tests."""
    from playwright.sync_api import sync_playwright

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    pw = sync_playwright().start()
    browser = pw.chromium.launch()

    yield browser

    browser.close()
    pw.stop()


@pytest.fixture()
def page(browser_instance, server):
    """Create a fresh page per test, navigated to the dashboard."""
    ctx = browser_instance.new_context(viewport={"width": 1400, "height": 900})
    pg = ctx.new_page()

    # Collect console errors
    pg._console_errors = []
    pg.on("console", lambda msg: pg._console_errors.append(msg.text) if msg.type == "error" else None)

    pg.goto(server + "/")
    pg.wait_for_load_state("networkidle")
    # Wait for React hydration — stats bar is one of the first things rendered
    pg.wait_for_selector("[data-testid='stats-bar']", timeout=10000)
    pg.wait_for_timeout(1500)

    yield pg

    ctx.close()


def screenshot(page, name: str, full_page: bool = False):
    """Save a named screenshot."""
    path = SCREENSHOT_DIR / f"{name}.png"
    page.screenshot(path=str(path), full_page=full_page)
    return path
