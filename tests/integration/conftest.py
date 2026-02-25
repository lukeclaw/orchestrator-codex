"""Integration test configuration.

Configures pytest-asyncio to use function-scoped event loops to avoid
conflicts with E2E tests that use session-scoped playwright fixtures.

Supports parallel execution via pytest-xdist by providing worker-isolated
tmux session names.
"""

import subprocess

import pytest


@pytest.fixture(scope="function")
def event_loop_policy():
    """Use default event loop policy for integration tests."""
    import asyncio

    return asyncio.DefaultEventLoopPolicy()


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
def tmux_session_name(worker_id):
    """Worker-isolated tmux session name to avoid parallel conflicts."""
    return f"orch-test-{worker_id}"


@pytest.fixture(scope="module")
def tmux_control_session(worker_id):
    """Worker-isolated tmux session for terminal control tests."""
    return f"orch-control-{worker_id}"


@pytest.fixture(autouse=True)
def cleanup_test_sessions(request, worker_id):
    """Clean up any test tmux sessions after tests that use real tmux."""
    yield
    if not request.node.get_closest_marker("allow_subprocess"):
        return
    # Use the real subprocess.run (guards are patched via monkeypatch and
    # already restored by this point in teardown, but import the saved
    # reference just in case).
    from tests.conftest import _real_run

    for name in (f"orch-test-{worker_id}", f"orch-control-{worker_id}"):
        try:
            _real_run(
                ["tmux", "kill-session", "-t", name],
                capture_output=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
