"""Integration test configuration.

Configures pytest-asyncio to use function-scoped event loops to avoid
conflicts with E2E tests that use session-scoped playwright fixtures.
"""

import pytest


@pytest.fixture(scope="function")
def event_loop_policy():
    """Use default event loop policy for integration tests."""
    import asyncio
    return asyncio.DefaultEventLoopPolicy()
