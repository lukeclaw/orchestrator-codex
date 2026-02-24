"""Session status state machine with validated transitions.

Defines all valid session statuses and the allowed transitions between them.
This ensures consistent status management across the codebase.
"""

from enum import Enum


class SessionStatus(str, Enum):
    """All valid session statuses."""
    IDLE = "idle"
    CONNECTING = "connecting"
    WORKING = "working"
    PAUSED = "paused"
    WAITING = "waiting"
    SCREEN_DETACHED = "screen_detached"
    ERROR = "error"
    DISCONNECTED = "disconnected"


# Define valid transitions: from_status -> set of allowed to_statuses
VALID_TRANSITIONS: dict[SessionStatus, set[SessionStatus]] = {
    SessionStatus.IDLE: {
        SessionStatus.CONNECTING,  # Starting rdev setup
        SessionStatus.WORKING,     # Local worker starts working
        SessionStatus.PAUSED,      # Stop called
    },
    SessionStatus.CONNECTING: {
        SessionStatus.WORKING,      # Setup succeeded
        SessionStatus.ERROR,        # Setup failed
        SessionStatus.DISCONNECTED, # Connection lost during setup
    },
    SessionStatus.WORKING: {
        SessionStatus.IDLE,            # Task completed
        SessionStatus.PAUSED,          # Stop called
        SessionStatus.WAITING,         # Claude waiting for input
        SessionStatus.ERROR,           # Something went wrong
        SessionStatus.DISCONNECTED,    # Health check failed
        SessionStatus.SCREEN_DETACHED, # Tunnel dead but Claude alive
    },
    SessionStatus.PAUSED: {
        SessionStatus.WORKING,      # Resume
        SessionStatus.IDLE,         # Clear/reset
        SessionStatus.DISCONNECTED, # Health check failed
        SessionStatus.ERROR,        # Something went wrong
    },
    SessionStatus.WAITING: {
        SessionStatus.WORKING,         # Resumed work
        SessionStatus.IDLE,            # Reset
        SessionStatus.PAUSED,          # Stop called
        SessionStatus.DISCONNECTED,    # Health check failed
        SessionStatus.SCREEN_DETACHED, # Tunnel dead
        SessionStatus.ERROR,           # Something went wrong
    },
    SessionStatus.SCREEN_DETACHED: {
        SessionStatus.WORKING,      # Reconnected successfully
        SessionStatus.WAITING,      # Reconnected, Claude waiting
        SessionStatus.DISCONNECTED, # Full disconnect
        SessionStatus.ERROR,        # Reconnect failed
    },
    SessionStatus.ERROR: {
        SessionStatus.CONNECTING,   # Retry setup
        SessionStatus.WORKING,      # Reconnect succeeded
        SessionStatus.WAITING,      # Reconnect succeeded
        SessionStatus.DISCONNECTED, # Give up
    },
    SessionStatus.DISCONNECTED: {
        SessionStatus.CONNECTING,   # Reconnect attempt
        SessionStatus.WORKING,      # Reconnect succeeded
        SessionStatus.WAITING,      # Reconnect succeeded
    },
}

# States that allow reconnection
RECONNECTABLE_STATES: set[SessionStatus] = {
    SessionStatus.DISCONNECTED,
    SessionStatus.SCREEN_DETACHED,
    SessionStatus.ERROR,
}


class InvalidTransitionError(Exception):
    """Raised when attempting an invalid status transition."""
    def __init__(self, current: SessionStatus, target: SessionStatus):
        self.current = current
        self.target = target
        super().__init__(
            f"Invalid status transition: {current.value} -> {target.value}. "
            f"Allowed transitions from {current.value}: {[s.value for s in VALID_TRANSITIONS.get(current, set())]}"
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
