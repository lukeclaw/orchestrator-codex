"""Integration test configuration.

Configures pytest-asyncio to use function-scoped event loops to avoid
conflicts with E2E tests that use session-scoped playwright fixtures.

The tmux session name for tests is set globally by the root conftest's
``_isolate_tmux_session`` fixture.  Integration tests that need explicit
session names use module-unique sub-sessions under the test umbrella.
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


def _short_module_name(request) -> str:
    """Extract a short suffix from the test module name."""
    return request.module.__name__.rsplit(".", 1)[-1].replace("test_", "")


@pytest.fixture(scope="module")
def tmux_session_name(_isolate_tmux_session, request):
    """Module-unique tmux session name under the test umbrella.

    Each integration test module gets its own sub-session so that tests
    which create/destroy sessions don't collide with each other.
    """
    name = f"{_isolate_tmux_session}-{_short_module_name(request)}"
    yield name
    # Clean up the sub-session after the module finishes
    from tests.conftest import _real_run

    try:
        _real_run(["tmux", "kill-session", "-t", name], capture_output=True, timeout=5)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


@pytest.fixture(scope="module")
def tmux_control_session(_isolate_tmux_session, request):
    """Module-unique tmux session for terminal control tests."""
    name = f"{_isolate_tmux_session}-{_short_module_name(request)}"
    yield name
    from tests.conftest import _real_run

    try:
        _real_run(["tmux", "kill-session", "-t", name], capture_output=True, timeout=5)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
