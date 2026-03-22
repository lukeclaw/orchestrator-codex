"""Session status state machine with validated transitions.

Defines all valid session statuses and the allowed transitions between them.
This ensures consistent status management across the codebase.
"""

from enum import StrEnum


class SessionStatus(StrEnum):
    """All valid session statuses."""

    IDLE = "idle"
    CONNECTING = "connecting"
    WORKING = "working"
    PAUSED = "paused"
    WAITING = "waiting"
    BLOCKED = "blocked"
    DISCONNECTED = "disconnected"


# Define valid transitions: from_status -> set of allowed to_statuses
VALID_TRANSITIONS: dict[SessionStatus, set[SessionStatus]] = {
    SessionStatus.IDLE: {
        SessionStatus.CONNECTING,  # Starting rdev setup
        SessionStatus.WORKING,  # Local worker starts working
        SessionStatus.PAUSED,  # Stop called
    },
    SessionStatus.CONNECTING: {
        SessionStatus.IDLE,  # Setup succeeded (no task assigned)
        SessionStatus.WORKING,  # Setup succeeded (task assigned)
        SessionStatus.DISCONNECTED,  # Setup failed or connection lost
    },
    SessionStatus.WORKING: {
        SessionStatus.IDLE,  # Task completed
        SessionStatus.PAUSED,  # Stop called
        SessionStatus.WAITING,  # Claude waiting for input
        SessionStatus.BLOCKED,  # Worker detected it's stuck
        SessionStatus.DISCONNECTED,  # Health check failed
    },
    SessionStatus.PAUSED: {
        SessionStatus.WORKING,  # Resume
        SessionStatus.IDLE,  # Clear/reset
        SessionStatus.DISCONNECTED,  # Health check failed
    },
    SessionStatus.WAITING: {
        SessionStatus.WORKING,  # Resumed work
        SessionStatus.IDLE,  # Reset
        SessionStatus.PAUSED,  # Stop called
        SessionStatus.BLOCKED,  # Brain detected worker is stuck
        SessionStatus.DISCONNECTED,  # Health check failed
    },
    SessionStatus.BLOCKED: {
        SessionStatus.WORKING,  # Brain sent help, worker resumed
        SessionStatus.IDLE,  # User/brain gave up, stopped worker
        SessionStatus.PAUSED,  # User paused
        SessionStatus.DISCONNECTED,  # Connection lost
    },
    SessionStatus.DISCONNECTED: {
        SessionStatus.CONNECTING,  # Reconnect attempt
        SessionStatus.WORKING,  # Reconnect succeeded
        SessionStatus.WAITING,  # Reconnect succeeded
        SessionStatus.BLOCKED,  # Reconnect succeeded, was blocked before
    },
}

# States that allow reconnection
RECONNECTABLE_STATES: set[SessionStatus] = {
    SessionStatus.DISCONNECTED,
}


class InvalidTransitionError(Exception):
    """Raised when attempting an invalid status transition."""

    def __init__(self, current: SessionStatus, target: SessionStatus):
        self.current = current
        self.target = target
        super().__init__(
            f"Invalid status transition: {current.value} -> {target.value}. "
            f"Allowed transitions from {current.value}: "
            f"{[s.value for s in VALID_TRANSITIONS.get(current, set())]}"
        )


def is_valid_transition(current: SessionStatus | str, target: SessionStatus | str) -> bool:
    """Check if a status transition is valid.

    Args:
        current: Current session status
        target: Target session status

    Returns:
        True if the transition is valid
    """
    if isinstance(current, str):
        try:
            current = SessionStatus(current)
        except ValueError:
            return False
    if isinstance(target, str):
        try:
            target = SessionStatus(target)
        except ValueError:
            return False

    allowed = VALID_TRANSITIONS.get(current, set())
    return target in allowed


def validate_transition(current: SessionStatus | str, target: SessionStatus | str) -> SessionStatus:
    """Validate and return the target status if transition is valid.

    Args:
        current: Current session status
        target: Target session status

    Returns:
        The target SessionStatus if valid

    Raises:
        InvalidTransitionError: If the transition is not allowed
    """
    if isinstance(current, str):
        current = SessionStatus(current)
    if isinstance(target, str):
        target = SessionStatus(target)

    if not is_valid_transition(current, target):
        raise InvalidTransitionError(current, target)

    return target


def is_reconnectable(status: SessionStatus | str) -> bool:
    """Check if a session status allows reconnection.

    Args:
        status: Session status to check

    Returns:
        True if reconnection is allowed from this status
    """
    if isinstance(status, str):
        try:
            status = SessionStatus(status)
        except ValueError:
            return False

    return status in RECONNECTABLE_STATES


def get_status_value(status: SessionStatus | str) -> str:
    """Get the string value of a status (for DB storage).

    Args:
        status: SessionStatus enum or string

    Returns:
        String value of the status
    """
    if isinstance(status, SessionStatus):
        return status.value
    return status
