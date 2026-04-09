"""Tests for Jupyter kernel REST endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.api.routes.kernel import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    # Mock the DB connection that _get_session_host needs
    mock_session = SimpleNamespace(host="localhost", work_dir="/tmp/test")
    with patch("orchestrator.api.routes.kernel.repo") as mock_repo:
        mock_repo.get_session.return_value = mock_session
        app.state.conn = MagicMock()
        yield TestClient(app)


class TestKernelSpecs:
    def test_specs_when_unavailable(self, client: TestClient):
        target = "orchestrator.api.routes.kernel.is_kernel_support_available"
        with patch(target, return_value=False):
            resp = client.get("/api/sessions/test-session/kernel/specs")
            assert resp.status_code == 200
            data = resp.json()
            assert data["available"] is False
            assert data["specs"] == []
            assert "pip install" in data["install_hint"]

    def test_specs_when_available(self, client: TestClient):
        mock_specs = {
            "python3": {
                "name": "python3",
                "display_name": "Python 3",
                "language": "python",
                "path": "/usr/share/jupyter/kernels/python3",
            }
        }
        avail = "orchestrator.api.routes.kernel.is_kernel_support_available"
        specs = "orchestrator.api.routes.kernel.list_kernelspecs"
        with patch(avail, return_value=True), patch(specs, return_value=mock_specs):
            resp = client.get("/api/sessions/test-session/kernel/specs")
            assert resp.status_code == 200
            data = resp.json()
            assert data["available"] is True
            assert len(data["specs"]) == 1
            assert data["specs"][0]["name"] == "python3"
            assert data["install_hint"] is None

    def test_specs_returns_host_info(self, client: TestClient):
        target = "orchestrator.api.routes.kernel.is_kernel_support_available"
        with patch(target, return_value=False):
            resp = client.get("/api/sessions/test-session/kernel/specs")
            data = resp.json()
            assert data["is_remote"] is False
            assert data["host"] == "localhost"


class TestKernelStatus:
    def test_status_no_kernel(self, client: TestClient):
        resp = client.get("/api/sessions/test-session/kernel/status?notebook_path=test.ipynb")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "none"
        assert data["kernel_name"] is None


class TestKernelInstall:
    def test_install_session_not_found(self):
        """Verify install returns 404 for unknown session."""
        app = FastAPI()
        app.include_router(router)
        with patch("orchestrator.api.routes.kernel.repo") as mock_repo:
            mock_repo.get_session.return_value = None
            app.state.conn = MagicMock()
            c = TestClient(app)
            resp = c.post("/api/sessions/unknown/kernel/install")
            assert resp.status_code == 404
