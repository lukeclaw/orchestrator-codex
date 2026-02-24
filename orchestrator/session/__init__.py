"""Session management module."""

from orchestrator.session.health import (
    check_all_workers_health,
    check_and_update_worker_health,
    check_claude_process_local,
    check_claude_process_rdev,  # backward-compat alias
    check_claude_process_remote,
    check_screen_and_claude_rdev,  # backward-compat alias
    check_screen_and_claude_remote,
    check_tui_running_in_pane,
    get_screen_session_name,
)
from orchestrator.session.reconnect import (
    WORKER_BASE_DIR,
    TUIActiveError,
    check_screen_exists_via_tmux,
    cleanup_reconnect_lock,
    get_reconnect_lock,
    reconnect_local_worker,
    reconnect_rdev_worker,  # backward-compat alias
    reconnect_remote_worker,
    reconnect_tunnel_only,
    safe_send_keys,
    trigger_reconnect,
)
from orchestrator.session.state_machine import (
    RECONNECTABLE_STATES,
    VALID_TRANSITIONS,
    InvalidTransitionError,
    SessionStatus,
    get_status_value,
    is_reconnectable,
    is_valid_transition,
    validate_transition,
)

__all__ = [
    # State machine
    "SessionStatus",
    "VALID_TRANSITIONS",
    "RECONNECTABLE_STATES",
    "InvalidTransitionError",
    "is_valid_transition",
    "validate_transition",
    "is_reconnectable",
    "get_status_value",
    # Health checks
    "get_screen_session_name",
    "check_tui_running_in_pane",
    "check_claude_process_local",
    "check_screen_and_claude_remote",
    "check_screen_and_claude_rdev",
    "check_claude_process_remote",
    "check_claude_process_rdev",
    "check_and_update_worker_health",
    "check_all_workers_health",
    # Reconnect
    "TUIActiveError",
    "safe_send_keys",
    "get_reconnect_lock",
    "cleanup_reconnect_lock",
    "check_screen_exists_via_tmux",
    "reconnect_tunnel_only",
    "reconnect_remote_worker",
    "reconnect_rdev_worker",
    "reconnect_local_worker",
    "trigger_reconnect",
    "WORKER_BASE_DIR",
]
