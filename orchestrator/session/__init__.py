"""Session management module."""

from orchestrator.session.health import (
    check_all_workers_health,
    check_and_update_worker_health,
    check_claude_process_local,
    check_claude_running_local,
    check_tui_running_in_pane,
)
from orchestrator.session.reconnect import (
    WORKER_BASE_DIR,
    TUIActiveError,
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
    "check_tui_running_in_pane",
    "check_claude_process_local",
    "check_claude_running_local",
    "check_and_update_worker_health",
    "check_all_workers_health",
    # Reconnect
    "TUIActiveError",
    "safe_send_keys",
    "get_reconnect_lock",
    "cleanup_reconnect_lock",
    "reconnect_tunnel_only",
    "reconnect_remote_worker",
    "reconnect_rdev_worker",
    "reconnect_local_worker",
    "trigger_reconnect",
    "WORKER_BASE_DIR",
]
