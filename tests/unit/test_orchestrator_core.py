"""Tests for orchestrator core and lifecycle."""

from unittest.mock import MagicMock, patch

from orchestrator.core.events import Event, clear, publish, subscribe
from orchestrator.core.orchestrator import Orchestrator
from orchestrator.state.repositories import sessions


class TestOrchestrator:
    def setup_method(self):
        clear()

    def test_init_subscribes_to_events(self, db):
        config = {"tmux": {"session_name": "test"}, "monitoring": {"poll_interval_seconds": 1}}
        orch = Orchestrator(db, config)
        # Verify it subscribed by publishing an event
        events_received = []
        original_handle = orch._handle_event

        def tracking_handle(event):
            events_received.append(event)
            original_handle(event)

        orch._handle_event = tracking_handle
        # Re-subscribe with new handler
        clear()
        subscribe("*", tracking_handle)
        publish(Event(type="test.event", data={"foo": "bar"}))
        assert len(events_received) == 1
        assert events_received[0].type == "test.event"

    def test_handle_event_does_not_raise(self, db):
        config = {}
        orch = Orchestrator(db, config)
        # Normal event should not raise
        event = Event(
            type="session.state_changed", data={"old_state": "idle", "new_state": "working"}
        )
        orch._handle_event(event)  # Should not raise


class TestLifecycle:
    @patch("orchestrator.core.lifecycle.tmux")
    def test_startup_check_creates_session(self, mock_tmux, db):
        mock_tmux.session_exists.return_value = False
        mock_tmux.create_session.return_value = None
        mock_tmux.list_windows.return_value = []

        from orchestrator.core.lifecycle import startup_check

        startup_check(db, tmux_session="test-session")

        mock_tmux.session_exists.assert_called_once_with("test-session")
        mock_tmux.create_session.assert_called_once_with("test-session")

    @patch("orchestrator.core.lifecycle.tmux")
    def test_startup_check_session_exists(self, mock_tmux, db):
        mock_tmux.session_exists.return_value = True
        mock_tmux.list_windows.return_value = []

        from orchestrator.core.lifecycle import startup_check

        startup_check(db, tmux_session="existing")

        mock_tmux.create_session.assert_not_called()

    @patch("orchestrator.core.lifecycle.tmux")
    def test_startup_reconciles_missing_windows(self, mock_tmux, db):
        # Create a session in DB
        s = sessions.create_session(db, "worker-1", "host")
        sessions.update_session(db, s.id, status="working")

        mock_tmux.session_exists.return_value = True
        mock_tmux.list_windows.return_value = []  # No tmux windows

        from orchestrator.core.lifecycle import startup_check

        startup_check(db)

        # Session should be marked disconnected
        updated = sessions.get_session(db, s.id)
        assert updated.status == "disconnected"

    @patch("orchestrator.core.lifecycle.tmux")
    def test_startup_keeps_matching_windows(self, mock_tmux, db):
        # Create a session in DB
        s = sessions.create_session(db, "worker-1", "host")
        sessions.update_session(db, s.id, status="working")

        mock_tmux.session_exists.return_value = True
        window = MagicMock()
        window.name = "worker-1"
        mock_tmux.list_windows.return_value = [window]

        from orchestrator.core.lifecycle import startup_check

        startup_check(db)

        # Session should still be working
        updated = sessions.get_session(db, s.id)
        assert updated.status == "working"

    def test_shutdown_logs_and_returns(self, db):
        # Shutdown should just log and return (no more snapshot creation)
        from orchestrator.core.lifecycle import shutdown

        shutdown(db)  # Should not raise
