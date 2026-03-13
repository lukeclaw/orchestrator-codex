"""Tests for in-memory reconnect step tracking."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.session.reconnect import (
    _reconnect_steps,
    _set_reconnect_step,
    _steps_lock,
    cleanup_reconnect_lock,
    clear_reconnect_step,
    get_reconnect_step,
)


@pytest.fixture(autouse=True)
def _clean_steps():
    """Ensure steps dict is empty before and after each test."""
    with _steps_lock:
        _reconnect_steps.clear()
    yield
    with _steps_lock:
        _reconnect_steps.clear()


# ---------------------------------------------------------------------------
# Basic registry tests
# ---------------------------------------------------------------------------


def test_set_and_get_step():
    _set_reconnect_step("s1", "tunnel")
    assert get_reconnect_step("s1") == "tunnel"


def test_get_nonexistent_returns_none():
    assert get_reconnect_step("nonexistent") is None


def test_clear_step():
    _set_reconnect_step("s1", "daemon")
    clear_reconnect_step("s1")
    assert get_reconnect_step("s1") is None


def test_clear_nonexistent_is_safe():
    clear_reconnect_step("nonexistent")  # should not raise


def test_step_overwrite():
    _set_reconnect_step("s1", "tunnel")
    _set_reconnect_step("s1", "daemon")
    assert get_reconnect_step("s1") == "daemon"


def test_failed_prefix():
    _set_reconnect_step("s1", "failed:daemon")
    step = get_reconnect_step("s1")
    assert step == "failed:daemon"
    assert step.startswith("failed:")


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


@pytest.mark.allow_threading
def test_concurrent_set_get():
    """Multiple threads setting/getting steps should not raise."""
    errors = []

    def worker(sid, steps):
        try:
            for step in steps:
                _set_reconnect_step(sid, step)
                _ = get_reconnect_step(sid)
            clear_reconnect_step(sid)
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=worker, args=(f"s{i}", ["tunnel", "daemon", "pty_check"]))
        for i in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors


# ---------------------------------------------------------------------------
# cleanup_reconnect_lock also clears step
# ---------------------------------------------------------------------------


def test_cleanup_reconnect_lock_clears_step():
    _set_reconnect_step("s1", "deploy")
    assert get_reconnect_step("s1") == "deploy"
    cleanup_reconnect_lock("s1")
    assert get_reconnect_step("s1") is None


# ---------------------------------------------------------------------------
# Event publishing
# ---------------------------------------------------------------------------


def test_set_step_publishes_event():
    with patch("orchestrator.core.events.publish") as mock_pub:
        _set_reconnect_step("s1", "tunnel")
        mock_pub.assert_called_once()
        event = mock_pub.call_args[0][0]
        assert event.type == "reconnect.step_changed"
        assert event.data == {"session_id": "s1", "step": "tunnel"}


def test_clear_step_publishes_none_event():
    _set_reconnect_step("s1", "tunnel")
    with patch("orchestrator.core.events.publish") as mock_pub:
        clear_reconnect_step("s1")
        mock_pub.assert_called_once()
        event = mock_pub.call_args[0][0]
        assert event.data["step"] is None


# ---------------------------------------------------------------------------
# API serialization includes reconnect_step
# ---------------------------------------------------------------------------


def test_serialize_session_includes_step(db):
    """_serialize_session() should include reconnect_step from in-memory store."""
    from orchestrator.api.routes.sessions import _serialize_session
    from orchestrator.state.repositories import sessions as repo

    session = repo.create_session(db, "test-worker", "remote-host")
    _set_reconnect_step(session.id, "daemon")

    result = _serialize_session(session)
    assert result["reconnect_step"] == "daemon"


def test_serialize_session_no_step(db):
    """_serialize_session() should return None when no step is set."""
    from orchestrator.api.routes.sessions import _serialize_session
    from orchestrator.state.repositories import sessions as repo

    session = repo.create_session(db, "test-worker-2", "remote-host")

    result = _serialize_session(session)
    assert result["reconnect_step"] is None


# ---------------------------------------------------------------------------
# Reconnect instrumentation: steps are set in order
# ---------------------------------------------------------------------------


def test_reconnect_rws_pty_worker_sets_steps_in_order():
    """_reconnect_rws_pty_worker sets steps in the expected order."""
    from orchestrator.session.reconnect import _reconnect_rws_pty_worker

    session = SimpleNamespace(
        id="test-session",
        name="test-worker",
        host="remote-host",
        rws_pty_id="old-pty-id",
        work_dir="/home/user",
        claude_session_id=None,
    )

    mock_rws = MagicMock()
    # Simulate PTY dead (not found)
    mock_rws.execute.return_value = {"ptys": []}
    mock_rws.create_pty.return_value = "new-pty-id"

    mock_repo = MagicMock()
    mock_tunnel = MagicMock()
    mock_tunnel.is_alive.return_value = True

    steps_seen = []
    original_set = _set_reconnect_step

    def track_step(sid, step):
        steps_seen.append(step)
        original_set(sid, step)

    # _ensure_rws_ready and _build_claude_command are local imports from
    # orchestrator.terminal.session — patch at source module.
    with (
        patch("orchestrator.session.reconnect._set_reconnect_step", side_effect=track_step),
        patch("orchestrator.terminal.session._ensure_rws_ready", return_value=mock_rws),
        patch("orchestrator.session.reconnect._reconnect_rws_for_host"),
        patch("orchestrator.session.reconnect._ensure_local_configs_exist"),
        patch("orchestrator.session.reconnect._copy_configs_to_remote"),
        patch(
            "orchestrator.session.reconnect._check_claude_session_exists_remote",
            return_value=False,
        ),
        patch("orchestrator.terminal.session._build_claude_command", return_value="claude cmd"),
    ):
        _reconnect_rws_pty_worker(MagicMock(), session, mock_repo, mock_tunnel)

    assert steps_seen == ["tunnel", "daemon", "pty_check", "deploy", "pty_create", "verify"]
