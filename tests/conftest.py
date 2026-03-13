"""Shared test fixtures and global guardrails.

Guardrails: any test that forgets to mock socket, subprocess, or threading
will fail immediately with a clear error instead of silently making real SSH
connections, HTTP requests, tmux calls, or spawning daemon threads that
outlive the test scope.

Use ``@pytest.mark.allow_network``, ``@pytest.mark.allow_subprocess``,
``@pytest.mark.allow_threading``, or ``@pytest.mark.allow_sleep`` to opt
out for tests that intentionally need real I/O, concurrency, or wall-clock
timing (e.g. E2E, integration terminal tests, race condition tests).
"""

import importlib
import logging
import socket
import subprocess
import sys
import threading
import time as _real_time_module
import traceback

import pytest

from orchestrator.state.db import get_memory_connection
from orchestrator.state.migrations.runner import apply_migrations

# Suppress noisy WARNING logs during tests (e.g. tmux "can't find window"
# messages from capture_output / send_keys in integration tests).
logging.getLogger("orchestrator").setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# Test ordering — schedule slow e2e tests first so their long setup
# (server startup, browser launch) overlaps with fast unit/integration tests.
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(items):
    """Reorder tests so e2e tests are collected first.

    With xdist ``--dist load``, the first tests collected get assigned to
    workers first.  By putting e2e tests at the front, a worker begins the
    ~9 s server startup immediately while other workers run fast unit tests.
    """
    e2e = [i for i in items if "/e2e/" in str(i.fspath)]
    rest = [i for i in items if "/e2e/" not in str(i.fspath)]
    items[:] = e2e + rest


# ---------------------------------------------------------------------------
# tmux session isolation — all tests use "orchestrator-test" instead of
# "orchestrator" so test windows never pollute the user's real session.
# ---------------------------------------------------------------------------

# The test tmux session name.  With xdist, each worker gets its own session.
TEST_TMUX_SESSION = "orchestrator-test"


@pytest.fixture(scope="session", autouse=True)
def _isolate_tmux_session(worker_id):
    """Route all in-process test tmux operations to a dedicated test session.

    Patches the module-level ``TMUX_SESSION`` constant so code that calls
    ``tmux_target()`` or reads ``manager.TMUX_SESSION`` uses the test
    session name instead of the real ``orchestrator`` session.
    """
    import orchestrator.terminal.manager as mgr

    session_name = (
        TEST_TMUX_SESSION if worker_id == "master" else f"{TEST_TMUX_SESSION}-{worker_id}"
    )

    orig_mgr = mgr.TMUX_SESSION
    mgr.TMUX_SESSION = session_name

    yield session_name

    mgr.TMUX_SESSION = orig_mgr

    # Kill the test session (use saved reference to bypass subprocess guard)
    try:
        _real_run(
            ["tmux", "kill-session", "-t", session_name],
            capture_output=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


# Directories whose code should be blocked by the subprocess/network guards.
# Calls from third-party libraries (e.g. python-multipart) are allowed through.
_PROJECT_ROOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)


def _called_from_project() -> bool:
    """Return True if the subprocess call originated directly from project code.

    Walk down from the guard frame through the stdlib, looking for the first
    frame that is NOT stdlib/conftest.  If that frame belongs to this project
    (not ``.venv/``), the call came from our code and should be blocked.
    """
    stdlib_prefix = sys.prefix
    for frame_info in reversed(traceback.extract_stack()):
        fn = frame_info.filename
        # Skip our guard frames
        if "conftest.py" in fn:
            continue
        # Skip stdlib frames (subprocess.py, etc.)
        if fn.startswith(stdlib_prefix):
            continue
        # First non-stdlib frame: is it ours or third-party?
        if fn.startswith(_PROJECT_ROOT) and "/.venv/" not in fn:
            return True
        return False
    return False


# ---------------------------------------------------------------------------
# Socket guard — blocks real network connections
# ---------------------------------------------------------------------------

_real_socket_connect = socket.socket.connect


def _guarded_socket_connect(self, address):
    if not _called_from_project():
        return _real_socket_connect(self, address)
    raise RuntimeError(
        f"Test tried to make a real network connection to {address!r}. "
        "Mock the network call or mark the test with @pytest.mark.allow_network."
    )


@pytest.fixture(autouse=True)
def _block_network(request, monkeypatch):
    if request.node.get_closest_marker("allow_network"):
        return
    monkeypatch.setattr(socket.socket, "connect", _guarded_socket_connect)


# ---------------------------------------------------------------------------
# Subprocess guard — blocks real subprocess calls
# ---------------------------------------------------------------------------

_real_popen = subprocess.Popen
_real_run = subprocess.run


def _guarded_popen(*args, **kwargs):
    if not _called_from_project():
        return _real_popen(*args, **kwargs)
    cmd = args[0] if args else kwargs.get("args", "<unknown>")
    raise RuntimeError(
        f"Test tried to run a real subprocess: {cmd!r}. "
        "Mock subprocess or mark the test with @pytest.mark.allow_subprocess."
    )


def _guarded_run(*args, **kwargs):
    if not _called_from_project():
        return _real_run(*args, **kwargs)
    cmd = args[0] if args else kwargs.get("args", "<unknown>")
    raise RuntimeError(
        f"Test tried to run a real subprocess: {cmd!r}. "
        "Mock subprocess or mark the test with @pytest.mark.allow_subprocess."
    )


@pytest.fixture(autouse=True)
def _block_subprocess(request, monkeypatch):
    if request.node.get_closest_marker("allow_subprocess"):
        return
    monkeypatch.setattr(subprocess, "Popen", _guarded_popen)
    monkeypatch.setattr(subprocess, "run", _guarded_run)


# ---------------------------------------------------------------------------
# Threading guard — blocks daemon threads that outlive test scope
# ---------------------------------------------------------------------------

_real_thread_start = threading.Thread.start


def _guarded_thread_start(self):
    if not _called_from_project():
        return _real_thread_start(self)
    raise RuntimeError(
        f"Test tried to start a real thread ({self._target!r}). "
        "Mock threading or mark the test with @pytest.mark.allow_threading."
    )


@pytest.fixture(autouse=True)
def _block_threading(request, monkeypatch):
    if request.node.get_closest_marker("allow_threading"):
        return
    monkeypatch.setattr(threading.Thread, "start", _guarded_thread_start)


# ---------------------------------------------------------------------------
# Sleep acceleration — makes time.sleep instant in production code
# ---------------------------------------------------------------------------

# Production modules whose ``time`` reference is replaced with a virtual
# clock during tests.  ``time.sleep(n)`` advances the clock by *n* seconds
# instantly (no wall-clock delay), and ``time.time()`` returns the virtual
# clock value so polling loops behave correctly.
_SLEEP_MODULES = [
    "orchestrator.session.reconnect",
    "orchestrator.session.health",
    "orchestrator.session.tunnel",
    "orchestrator.terminal.manager",
    "orchestrator.terminal.session",
    "orchestrator.terminal.ssh",
    "orchestrator.terminal.markers",
    "orchestrator.terminal.remote_worker_server",
    "orchestrator.terminal.claude_update",
    "orchestrator.api.routes.sessions",
    "orchestrator.api.routes.brain",
    "orchestrator.api.routes.files",
    "orchestrator.api.routes.rdevs",
    "orchestrator.api.routes.updates",
    "orchestrator.api.routes.pr_preview",
    "orchestrator.api.ws_terminal",
    "orchestrator.browser.cdp_proxy",
    "orchestrator.state.db",
]


class _VirtualClock:
    """Drop-in replacement for the ``time`` module with an instant virtual clock.

    ``sleep(n)`` advances the virtual clock by *n* without blocking.
    ``time()`` returns the current virtual-clock value.
    All other ``time.*`` attributes delegate to the real module.
    """

    def __init__(self):
        self._clock = _real_time_module.time()

    def time(self):
        return self._clock

    def sleep(self, seconds):
        self._clock += seconds

    def monotonic(self):
        return self._clock

    def __getattr__(self, name):
        return getattr(_real_time_module, name)


@pytest.fixture(autouse=True)
def _fast_sleep(request, monkeypatch):
    """Replace ``time`` in production modules with a virtual clock.

    Opt out with ``@pytest.mark.allow_sleep`` for tests that need real
    wall-clock timing (e.g. thread-coordination in race-condition tests).
    """
    if request.node.get_closest_marker("allow_sleep"):
        return

    clock = _VirtualClock()

    for module_path in _SLEEP_MODULES:
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            continue
        if hasattr(mod, "time"):
            monkeypatch.setattr(mod, "time", clock)


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db():
    """In-memory SQLite DB with schema applied."""
    conn = get_memory_connection()
    apply_migrations(conn)
    yield conn
    conn.close()


@pytest.fixture
def seeded_db(db):
    """In-memory DB with schema + seed data."""
    from scripts.seed_db import seed_all

    seed_all(db)
    return db
