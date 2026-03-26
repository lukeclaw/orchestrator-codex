from unittest.mock import patch

from orchestrator.providers.runtime import WorkerLaunchRequest, get_provider_runtime


def test_codex_runtime_is_selected():
    runtime = get_provider_runtime("codex")
    assert runtime.provider_id == "codex"


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


def test_codex_runtime_builds_local_launch_command(db, tmp_path):
    runtime = get_provider_runtime("codex")
    request = WorkerLaunchRequest(
        conn=db,
        session_id="session-3",
        name="worker-3",
        host="localhost",
        work_dir="/tmp/project",
        tmp_dir=str(tmp_path / "worker-3"),
        model="opus",
        effort="high",
    )

    with (
        patch("orchestrator.providers.runtimes.codex.deploy_worker_scripts") as mock_deploy,
        patch(
            "orchestrator.providers.runtimes.codex.start_cdp_proxy", return_value=9777
        ) as mock_proxy,
        patch("orchestrator.providers.runtimes.codex.tmux.send_keys", return_value=True) as mock_send,
    ):
        result = runtime.launch_local_worker(request)

    assert result == {"ok": True}
    mock_deploy.assert_called_once()
    mock_proxy.assert_called_once_with("session-3", chrome_port=9222)
    command = mock_send.call_args.args[2]
    assert "codex" in command
    assert "--add-dir" in command
    assert "gpt-5-codex" in command
    assert "model_reasoning_effort" in command
    assert "model_instructions_file" in command


def test_codex_runtime_rejects_remote_launch(db):
    runtime = get_provider_runtime("codex")
    request = WorkerLaunchRequest(
        conn=db,
        session_id="session-4",
        name="worker-4",
        host="user/rdev-vm",
    )

    result = runtime.launch_remote_worker(request)

    assert result["ok"] is False
    assert "Remote Codex support" in result["error"]
