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
    check_claude_process_local,
    check_screen_and_claude_rdev,
    check_claude_process_rdev,
)

from orchestrator.session.reconnect import (
    parse_hostname_from_output,
    check_ssh_alive,
    check_inside_screen,
    detach_from_screen,
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
    "check_claude_process_local",
    "check_screen_and_claude_rdev",
    "check_claude_process_rdev",
    # Reconnect
    "parse_hostname_from_output",
    "check_ssh_alive",
    "check_inside_screen",
    "detach_from_screen",
    "check_screen_exists_via_tmux",
    "reconnect_tunnel_only",
    "reconnect_rdev_worker",
    "reconnect_local_worker",
]
