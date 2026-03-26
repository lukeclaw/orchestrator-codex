from unittest.mock import patch

from orchestrator.providers.runtime import WorkerLaunchRequest, get_provider_runtime


def test_claude_runtime_delegates_local_worker_launch(db):
    runtime = get_provider_runtime("claude")
    request = WorkerLaunchRequest(
        conn=db,
        session_id="session-1",
        name="worker-1",
        host="localhost",
    )

    with patch("orchestrator.terminal.session.setup_local_worker", return_value={"ok": True}) as mock_setup:
        result = runtime.launch_local_worker(request)

    assert result == {"ok": True}
    mock_setup.assert_called_once_with(
        db,
        "session-1",
        "worker-1",
        tmux_session="orchestrator",
        api_port=8093,
        work_dir=None,
        tmp_dir=None,
        custom_skills=None,
        disabled_builtin_names=None,
        update_before_start=False,
        skip_permissions=False,
        model="opus",
        effort="high",
    )


def test_claude_runtime_delegates_remote_worker_launch(db):
    runtime = get_provider_runtime("claude")
    request = WorkerLaunchRequest(
        conn=db,
        session_id="session-2",
        name="worker-2",
        host="user/rdev-vm",
    )

    with patch("orchestrator.terminal.session.setup_remote_worker", return_value={"ok": True}) as mock_setup:
        result = runtime.launch_remote_worker(request)

    assert result == {"ok": True}
    mock_setup.assert_called_once_with(
        db,
        "session-2",
        "worker-2",
        "user/rdev-vm",
        tmux_session="orchestrator",
        api_port=8093,
        work_dir=None,
        tmp_dir=None,
        tunnel_manager=None,
        custom_skills=None,
        disabled_builtin_names=None,
        update_before_start=False,
        skip_permissions=False,
        model="opus",
        effort="high",
    )
