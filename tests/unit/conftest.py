"""Unit test guardrails: block real network and subprocess calls.

Any unit test that forgets to mock socket or subprocess will fail immediately
with a clear error instead of hanging on a real SSH connection or HTTP request.

Use @pytest.mark.allow_network or @pytest.mark.allow_subprocess to opt out
for tests that intentionally need real I/O.
"""

import socket
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Socket guard — blocks real network connections
# ---------------------------------------------------------------------------

_real_socket_connect = socket.socket.connect


def _guarded_socket_connect(self, address):
    raise RuntimeError(
        f"Unit test tried to make a real network connection to {address!r}. "
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
    cmd = args[0] if args else kwargs.get("args", "<unknown>")
    raise RuntimeError(
        f"Unit test tried to run a real subprocess: {cmd!r}. "
        "Mock subprocess or mark the test with @pytest.mark.allow_subprocess."
    )


def _guarded_run(*args, **kwargs):
    cmd = args[0] if args else kwargs.get("args", "<unknown>")
    raise RuntimeError(
        f"Unit test tried to run a real subprocess: {cmd!r}. "
        "Mock subprocess or mark the test with @pytest.mark.allow_subprocess."
    )


@pytest.fixture(autouse=True)
def _block_subprocess(request, monkeypatch):
    if request.node.get_closest_marker("allow_subprocess"):
        return
    monkeypatch.setattr(subprocess, "Popen", _guarded_popen)
    monkeypatch.setattr(subprocess, "run", _guarded_run)
