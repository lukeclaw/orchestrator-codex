"""Unit tests for session state machine."""

import pytest

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


class TestSessionStatus:
    """Test SessionStatus enum."""

    def test_all_statuses_defined(self):
        """All expected statuses should be defined."""
        expected = {"idle", "connecting", "working", "paused", "waiting",
                    "screen_detached", "error", "disconnected"}
        actual = {s.value for s in SessionStatus}
        assert actual == expected

    def test_status_is_string_enum(self):
        """SessionStatus should be a string enum for easy serialization."""
        assert SessionStatus.IDLE == "idle"
        assert SessionStatus.WORKING == "working"


class TestValidTransitions:
    """Test transition validation logic."""

    def test_idle_to_connecting_valid(self):
        """idle -> connecting is valid (rdev setup start)."""
        assert is_valid_transition(SessionStatus.IDLE, SessionStatus.CONNECTING)
        assert is_valid_transition("idle", "connecting")

    def test_idle_to_working_valid(self):
        """idle -> working is valid (local worker starts)."""
        assert is_valid_transition(SessionStatus.IDLE, SessionStatus.WORKING)

    def test_connecting_to_working_valid(self):
        """connecting -> working is valid (setup succeeded)."""
        assert is_valid_transition(SessionStatus.CONNECTING, SessionStatus.WORKING)

    def test_connecting_to_error_valid(self):
        """connecting -> error is valid (setup failed)."""
        assert is_valid_transition(SessionStatus.CONNECTING, SessionStatus.ERROR)

    def test_working_to_paused_valid(self):
        """working -> paused is valid (stop called)."""
        assert is_valid_transition(SessionStatus.WORKING, SessionStatus.PAUSED)

    def test_working_to_screen_detached_valid(self):
        """working -> screen_detached is valid (tunnel dead)."""
        assert is_valid_transition(SessionStatus.WORKING, SessionStatus.SCREEN_DETACHED)

    def test_working_to_disconnected_valid(self):
        """working -> disconnected is valid (health check failed)."""
        assert is_valid_transition(SessionStatus.WORKING, SessionStatus.DISCONNECTED)

    def test_disconnected_to_working_valid(self):
        """disconnected -> working is valid (reconnect succeeded)."""
        assert is_valid_transition(SessionStatus.DISCONNECTED, SessionStatus.WORKING)

    def test_error_to_working_valid(self):
        """error -> working is valid (reconnect succeeded)."""
        assert is_valid_transition(SessionStatus.ERROR, SessionStatus.WORKING)

    def test_screen_detached_to_working_valid(self):
        """screen_detached -> working is valid (reconnected)."""
        assert is_valid_transition(SessionStatus.SCREEN_DETACHED, SessionStatus.WORKING)


class TestInvalidTransitions:
    """Test that invalid transitions are rejected."""

    def test_working_to_connecting_invalid(self):
        """working -> connecting is NOT valid."""
        assert not is_valid_transition(SessionStatus.WORKING, SessionStatus.CONNECTING)

    def test_disconnected_to_paused_invalid(self):
        """disconnected -> paused is NOT valid."""
        assert not is_valid_transition(SessionStatus.DISCONNECTED, SessionStatus.PAUSED)

    def test_idle_to_screen_detached_invalid(self):
        """idle -> screen_detached is NOT valid."""
        assert not is_valid_transition(SessionStatus.IDLE, SessionStatus.SCREEN_DETACHED)

    def test_invalid_string_status(self):
        """Invalid status strings should return False."""
        assert not is_valid_transition("invalid", "working")
        assert not is_valid_transition("idle", "invalid")


class TestValidateTransition:
    """Test validate_transition function."""

    def test_valid_transition_returns_target(self):
        """Valid transition should return the target status."""
        result = validate_transition(SessionStatus.IDLE, SessionStatus.CONNECTING)
        assert result == SessionStatus.CONNECTING

    def test_valid_transition_with_strings(self):
        """Should accept string values."""
        result = validate_transition("idle", "connecting")
        assert result == SessionStatus.CONNECTING

    def test_invalid_transition_raises(self):
        """Invalid transition should raise InvalidTransitionError."""
        with pytest.raises(InvalidTransitionError) as exc:
            validate_transition(SessionStatus.WORKING, SessionStatus.CONNECTING)
        assert exc.value.current == SessionStatus.WORKING
        assert exc.value.target == SessionStatus.CONNECTING


class TestReconnectableStates:
    """Test reconnectable state checks."""

    def test_disconnected_is_reconnectable(self):
        """disconnected should be reconnectable."""
        assert is_reconnectable(SessionStatus.DISCONNECTED)
        assert is_reconnectable("disconnected")

    def test_screen_detached_is_reconnectable(self):
        """screen_detached should be reconnectable."""
        assert is_reconnectable(SessionStatus.SCREEN_DETACHED)
        assert is_reconnectable("screen_detached")

    def test_error_is_reconnectable(self):
        """error should be reconnectable."""
        assert is_reconnectable(SessionStatus.ERROR)
        assert is_reconnectable("error")

    def test_working_is_not_reconnectable(self):
        """working should NOT be reconnectable."""
        assert not is_reconnectable(SessionStatus.WORKING)
        assert not is_reconnectable("working")

    def test_connecting_is_not_reconnectable(self):
        """connecting should NOT be reconnectable."""
        assert not is_reconnectable(SessionStatus.CONNECTING)
        assert not is_reconnectable("connecting")

    def test_idle_is_not_reconnectable(self):
        """idle should NOT be reconnectable."""
        assert not is_reconnectable(SessionStatus.IDLE)

    def test_invalid_status_not_reconnectable(self):
        """Invalid status should return False."""
        assert not is_reconnectable("invalid_status")


class TestGetStatusValue:
    """Test get_status_value function."""

    def test_enum_to_string(self):
        """Should convert enum to string."""
        assert get_status_value(SessionStatus.WORKING) == "working"
        assert get_status_value(SessionStatus.DISCONNECTED) == "disconnected"

    def test_string_passthrough(self):
        """Should pass through string values."""
        assert get_status_value("working") == "working"
        assert get_status_value("disconnected") == "disconnected"


class TestTransitionCompleteness:
    """Test that all statuses have defined transitions."""

    def test_all_statuses_have_transitions(self):
        """Every status should have at least one valid transition."""
        for status in SessionStatus:
            assert status in VALID_TRANSITIONS, f"{status} has no transitions defined"
            assert len(VALID_TRANSITIONS[status]) > 0, f"{status} has empty transitions"

    def test_reconnectable_states_match_constant(self):
        """RECONNECTABLE_STATES should match what's documented."""
        expected = {SessionStatus.DISCONNECTED, SessionStatus.SCREEN_DETACHED, SessionStatus.ERROR}
        assert RECONNECTABLE_STATES == expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
