"""Session management module."""

from orchestrator.session.state_machine import (
    SessionStatus,
    VALID_TRANSITIONS,
    RECONNECTABLE_STATES,
    InvalidTransitionError,
    is_valid_transition,
    validate_transition,
    is_reconnectable,
    get_status_value,
)

from orchestrator.session.health import (
    get_screen_session_name,
    check_tui_running_in_pane,
    check_claude_process_local,
    check_screen_and_claude_rdev,
    check_claude_process_rdev,
)

from orchestrator.session.reconnect import (
    TUIActiveError,
    safe_send_keys,
    get_reconnect_lock,
    cleanup_reconnect_lock,
    check_screen_exists_via_tmux,
    reconnect_tunnel_only,
    reconnect_rdev_worker,
    reconnect_local_worker,
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
    "check_screen_and_claude_rdev",
    "check_claude_process_rdev",
    # Reconnect
    "TUIActiveError",
    "safe_send_keys",
    "get_reconnect_lock",
    "cleanup_reconnect_lock",
    "check_screen_exists_via_tmux",
    "reconnect_tunnel_only",
    "reconnect_rdev_worker",
    "reconnect_local_worker",
]
