"""Integration test configuration.

Configures pytest-asyncio to use function-scoped event loops to avoid
conflicts with E2E tests that use session-scoped playwright fixtures.

Supports parallel execution via pytest-xdist by providing worker-isolated
tmux session names.
"""

import pytest

from orchestrator.terminal import manager as tmux


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
    if hasattr(request.config, 'workerinput'):
        return request.config.workerinput['workerid']
    return 'master'


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
    """Clean up any test tmux sessions after tests complete."""
    yield
    # Clean up both possible session names for this worker
    tmux.kill_session(f"orch-test-{worker_id}")
    tmux.kill_session(f"orch-control-{worker_id}")
