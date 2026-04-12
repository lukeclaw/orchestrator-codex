"""Tests for Jupyter kernel manager — mocks jupyter_client to avoid real kernels."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from orchestrator.kernel.manager import (
    KernelPool,
    KernelSession,
    is_kernel_support_available,
    list_kernelspecs,
)


class TestRuntimeDetection:
    def test_is_kernel_support_available(self):
        # The actual value depends on whether jupyter_client is installed
        # in the test environment — just check it returns a bool
        result = is_kernel_support_available()
        assert isinstance(result, bool)

    def test_list_kernelspecs_returns_dict(self):
        result = list_kernelspecs()
        assert isinstance(result, dict)


class TestKernelSession:
    @pytest.mark.allow_subprocess
    @pytest.mark.allow_threading
    async def test_start_without_jupyter_raises(self):
        ks = KernelSession("sess1", "notebook.ipynb", "python3", "/tmp")
        with patch("orchestrator.kernel.manager._jupyter_available", False):
            with pytest.raises(RuntimeError, match="jupyter_client is not installed"):
                await ks.start()

    @pytest.mark.allow_subprocess
    @pytest.mark.allow_threading
    async def test_start_with_unknown_kernel_raises(self):
        with (
            patch("orchestrator.kernel.manager._jupyter_available", True),
            patch("orchestrator.kernel.manager.list_kernelspecs", return_value={"python3": {}}),
        ):
            ks = KernelSession("sess1", "notebook.ipynb", "nonexistent", "/tmp")
            with pytest.raises(ValueError, match="Unknown kernel"):
                await ks.start()

    def test_subscribe_and_notify(self):
        ks = KernelSession("sess1", "notebook.ipynb", "python3", "/tmp")
        received: list[dict] = []
        ks.subscribe(lambda msg: received.append(msg))
        ks._notify_subscribers({"type": "status", "status": "idle"})
        assert len(received) == 1
        assert received[0]["status"] == "idle"

    def test_unsubscribe(self):
        ks = KernelSession("sess1", "notebook.ipynb", "python3", "/tmp")
        received: list[dict] = []
        unsub = ks.subscribe(lambda msg: received.append(msg))
        unsub()
        ks._notify_subscribers({"type": "status", "status": "idle"})
        assert len(received) == 0

    def test_execute_without_client_raises(self):
        ks = KernelSession("sess1", "notebook.ipynb", "python3", "/tmp")
        with pytest.raises(RuntimeError, match="Kernel not connected"):
            ks.execute("cell1", "print(1)")

    def test_interrupt_without_kernel_is_noop(self):
        ks = KernelSession("sess1", "notebook.ipynb", "python3", "/tmp")
        ks.interrupt()  # Should not raise

    def test_initial_status_is_starting(self):
        ks = KernelSession("sess1", "notebook.ipynb", "python3", "/tmp")
        assert ks.status == "starting"


class TestKernelPool:
    def test_singleton(self):
        pool1 = KernelPool.get_instance()
        pool2 = KernelPool.get_instance()
        assert pool1 is pool2

    def test_get_session_returns_none_when_empty(self):
        pool = KernelPool()
        assert pool.get_session("sess1", "notebook.ipynb") is None

    async def test_shutdown_all_empty_pool(self):
        pool = KernelPool()
        await pool.shutdown_all()  # Should not raise

    def test_interrupt_nonexistent_is_noop(self):
        pool = KernelPool()
        pool.interrupt_kernel("sess1", "notebook.ipynb")  # Should not raise

    async def test_shutdown_nonexistent_is_noop(self):
        pool = KernelPool()
        await pool.shutdown_kernel("sess1", "notebook.ipynb")  # Should not raise
