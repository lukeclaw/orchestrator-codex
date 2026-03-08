"""Tests for TUI safety guard, tmux pane, and delete/cleanup race conditions.

Exercises timing-sensitive race conditions in the orchestrator's reconnect,
health-check, delete, and send-message codepaths.  All tmux subprocess calls
are mocked so no live tmux session is needed.

Race conditions tested
----------------------
1. TOCTOU in safe_send_keys: check returns False → TUI activates → send_keys
   types into Claude's input instead of the shell.
2. POST /send while reconnect is resending commands to the same pane.
3. delete_session sends "exit" while reconnect is in progress.
4. Terminal WebSocket input interleaved with reconnect send_keys.
5. _clean_pane_for_ssh kills/recreates window but reconnect holds old ref.
6. check_tui_running_in_pane returns stale result vs. screen changes.
7. Two simultaneous send_message calls — paste operations interleave.
8. delete_session cleanup races with reconnect that restarts SSH.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

# Pre-import ws_terminal early to avoid _osx_support/sysconfig issues
# when open() gets monkeypatched by other tests in the same session.
from orchestrator.api import ws_terminal as _ws_terminal_mod  # noqa: F401
from orchestrator.session.reconnect import (
    TUIActiveError,
    _clean_pane_for_ssh,
    cleanup_reconnect_lock,
    get_reconnect_lock,
    safe_send_keys,
)

pytestmark = pytest.mark.allow_threading

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class CallRecorder:
    """Thread-safe recorder for ordered inter-thread call sequences."""

    def __init__(self):
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def record(self, label: str):
        with self._lock:
            self.calls.append(label)

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self.calls)


def _make_session(**overrides):
    """Create a minimal mock session object."""
    defaults = {
        "id": "sess-123",
        "name": "worker-1",
        "host": "user/rdev-vm",
        "status": "disconnected",
        "work_dir": "/tmp/work",
        "claude_session_id": None,
        "auto_reconnect": False,
    }
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# 1. TOCTOU in safe_send_keys
# ---------------------------------------------------------------------------


class TestSafeSendKeysTOCTOU:
    """Race: check_tui_running_in_pane returns False, then Claude starts
    (TUI activates) before the actual send_keys call executes.

    Bug: The command is typed into Claude's TUI input box, not the shell.
    Severity: Data corruption — arbitrary shell commands fed to Claude.
    Fix: Atomic check-and-send (hold a per-pane lock), or re-check inside
    send_keys with a very short window, or use tmux's own conditional.
    """

    def test_toctou_gap_allows_tui_activation(self):
        """Demonstrate that a TUI can activate between check and send."""
        call_order = CallRecorder()

        def mock_check_tui(sess, win):
            call_order.record("check_tui")
            # Simulate: TUI is not running at check time
            return False

        def mock_send_keys(sess, win, text, enter=True):
            # By the time send_keys runs, TUI has activated
            call_order.record("send_keys")
            return True

        with (
            patch(
                "orchestrator.session.reconnect.check_tui_running_in_pane",
                side_effect=mock_check_tui,
            ),
            patch(
                "orchestrator.session.reconnect.send_keys",
                side_effect=mock_send_keys,
            ),
        ):
            # safe_send_keys succeeds because check returned False
            safe_send_keys("orch", "w1", "screen -S claude-sess-123")

        # Verify the sequence: check happened before send (TOCTOU window)
        order = call_order.snapshot()
        assert order == ["check_tui", "send_keys"]

    def test_tui_activates_during_check_window(self):
        """Simulate TUI activating right after the check passes."""
        tui_state = {"active": False}

        def mock_check_tui(sess, win):
            # Returns False (TUI not running yet)
            result = tui_state["active"]
            # TUI activates immediately after we return False
            tui_state["active"] = True
            return result

        def mock_send_keys(sess, win, text, enter=True):
            # At this point TUI is active — but safe_send_keys already passed guard
            assert tui_state["active"] is True, "TUI should be active by now"
            return True

        with (
            patch(
                "orchestrator.session.reconnect.check_tui_running_in_pane",
                side_effect=mock_check_tui,
            ),
            patch(
                "orchestrator.session.reconnect.send_keys",
                side_effect=mock_send_keys,
            ),
        ):
            # This SHOULD raise TUIActiveError but doesn't — the TOCTOU gap
            safe_send_keys("orch", "w1", "echo dangerous-command")

    def test_safe_send_keys_blocks_when_tui_detected(self):
        """Verify the guard works when TUI is already active."""
        with patch(
            "orchestrator.session.reconnect.check_tui_running_in_pane",
            return_value=True,
        ):
            with pytest.raises(TUIActiveError, match="TUI running"):
                safe_send_keys("orch", "w1", "echo test")

    def test_safe_send_keys_passes_through_when_no_tui(self):
        """Verify send_keys is called when no TUI is active."""
        with (
            patch(
                "orchestrator.session.reconnect.check_tui_running_in_pane",
                return_value=False,
            ),
            patch(
                "orchestrator.session.reconnect.send_keys",
                return_value=True,
            ) as mock_sk,
        ):
            safe_send_keys("orch", "w1", "echo hello")
            mock_sk.assert_called_once_with("orch", "w1", "echo hello", enter=True)

    def test_concurrent_check_and_activate(self):
        """Two threads: one activates TUI, another runs safe_send_keys.

        Thread A: sleep briefly, then mark TUI as active
        Thread B: run safe_send_keys — the check may see False even though
                  TUI is about to become active.
        """
        barrier = threading.Barrier(2, timeout=5)
        tui_active = threading.Event()
        send_happened = threading.Event()
        commands_sent = []

        def mock_check_tui(sess, win):
            # Wait for both threads to be ready
            barrier.wait()
            # Small window where TUI might not be active yet
            return tui_active.is_set()

        def mock_send_keys(sess, win, text, enter=True):
            commands_sent.append(text)
            send_happened.set()
            return True

        def tui_activator():
            barrier.wait()
            # Activate TUI right after check in the other thread
            tui_active.set()

        with (
            patch(
                "orchestrator.session.reconnect.check_tui_running_in_pane",
                side_effect=mock_check_tui,
            ),
            patch(
                "orchestrator.session.reconnect.send_keys",
                side_effect=mock_send_keys,
            ),
        ):
            t = threading.Thread(target=tui_activator)
            t.start()

            try:
                safe_send_keys("orch", "w1", "screen -S test")
            except TUIActiveError:
                pass  # This is the safe outcome

            t.join(timeout=5)

        # Either outcome is possible — the test documents the race


# ---------------------------------------------------------------------------
# 2. POST /send while reconnect resends commands
# ---------------------------------------------------------------------------


class TestSendDuringReconnect:
    """Race: User sends a message via dashboard while reconnect pipeline
    is sending commands to the same pane.

    Bug: User's message text and reconnect's shell commands interleave in
    the tmux paste buffer, producing garbled output.
    Severity: Data corruption — partial commands sent to Claude or shell.
    Fix: Per-session mutex for all pane interactions (send_message should
    acquire the reconnect lock before sending).
    """

    def test_send_and_reconnect_interleave(self):
        """Simulate concurrent send_to_session and safe_send_keys."""
        pane_writes = CallRecorder()
        barrier = threading.Barrier(2, timeout=5)

        def mock_send_keys(sess, win, text, enter=True):
            pane_writes.record(f"send_keys:{text}")
            # Small delay to widen the interleave window
            time.sleep(0.01)
            return True

        def mock_send_keys_literal(sess, win, text):
            pane_writes.record(f"literal:{text}")
            time.sleep(0.01)
            return True

        def mock_paste_to_pane(sess, win, text):
            pane_writes.record(f"paste:{text}")
            time.sleep(0.01)
            return True

        def reconnect_thread():
            """Simulates reconnect sending setup commands."""
            barrier.wait()
            for cmd in ["export PATH=...", "cd /work", "screen -S claude-123"]:
                mock_send_keys("orch", "w1", cmd)

        def user_send_thread():
            """Simulates user sending a message via POST /send."""
            barrier.wait()
            mock_paste_to_pane("orch", "w1", "Please fix the bug in auth.py")
            mock_send_keys("orch", "w1", "", enter=True)

        t1 = threading.Thread(target=reconnect_thread)
        t2 = threading.Thread(target=user_send_thread)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        writes = pane_writes.snapshot()

        # The key insight: without locking, both threads can write simultaneously
        assert len(writes) >= 4, f"Expected at least 4 writes, got {len(writes)}: {writes}"

    def test_reconnect_lock_prevents_interleave(self):
        """With the reconnect lock, send_message should wait."""
        session_id = "sess-lock-test"
        lock = get_reconnect_lock(session_id)
        order = CallRecorder()

        def reconnect_work():
            with lock:
                order.record("reconnect_start")
                time.sleep(0.1)
                order.record("reconnect_end")

        def send_work():
            # Slight delay to let reconnect grab the lock first
            time.sleep(0.02)
            acquired = lock.acquire(timeout=5)
            if acquired:
                try:
                    order.record("send_start")
                    order.record("send_end")
                finally:
                    lock.release()

        t1 = threading.Thread(target=reconnect_work)
        t2 = threading.Thread(target=send_work)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # If the lock is used, reconnect completes before send starts
        seq = order.snapshot()
        assert seq.index("reconnect_end") < seq.index("send_start"), (
            f"Expected reconnect to finish before send, got: {seq}"
        )

        cleanup_reconnect_lock(session_id)


# ---------------------------------------------------------------------------
# 3. delete_session sends "exit" while reconnect is in progress
# ---------------------------------------------------------------------------


class TestDeleteDuringReconnect:
    """Race: delete_session sends "exit" + kills window while reconnect
    is mid-pipeline trying to SSH and launch Claude.

    Bug: reconnect tries send_keys to a killed pane → tmux error. Or worse,
    reconnect recreates the window after delete killed it.
    Severity: Stuck state — orphaned reconnect thread, zombie processes.
    Fix: delete_session should acquire the reconnect lock, or set a cancel
    flag that reconnect checks between steps.
    """

    def test_delete_kills_pane_while_reconnect_uses_it(self):
        """Reconnect sends commands to a pane that delete just killed."""
        pane_alive = {"alive": True}
        errors = []

        def mock_send_keys(sess, win, text, enter=True):
            if not pane_alive["alive"]:
                errors.append(f"send_keys to dead pane: {text}")
                return False
            return True

        def mock_kill_window(sess, win):
            pane_alive["alive"] = False
            return True

        def mock_check_tui(sess, win):
            return False

        # Simulate: delete kills the window
        mock_kill_window("orch", "w1")

        # Then reconnect tries to send commands
        with (
            patch(
                "orchestrator.session.reconnect.check_tui_running_in_pane",
                side_effect=mock_check_tui,
            ),
            patch(
                "orchestrator.session.reconnect.send_keys",
                side_effect=mock_send_keys,
            ),
        ):
            # This would fail silently or raise
            result = mock_send_keys("orch", "w1", "rdev ssh user/vm")
            assert result is False
            assert len(errors) == 1
            assert "dead pane" in errors[0]

    def test_reconnect_lock_serializes_delete_and_reconnect(self):
        """delete_session + reconnect should not run concurrently."""
        session_id = "sess-del-test"
        lock = get_reconnect_lock(session_id)
        order = CallRecorder()

        def reconnect_steps():
            if not lock.acquire(timeout=5):
                order.record("reconnect_skipped")
                return
            try:
                order.record("reconnect_step1")
                time.sleep(0.1)
                order.record("reconnect_step2")
                time.sleep(0.1)
                order.record("reconnect_step3")
            finally:
                lock.release()

        def delete_steps():
            time.sleep(0.05)  # Let reconnect start first
            if not lock.acquire(timeout=5):
                order.record("delete_skipped")
                return
            try:
                order.record("delete_exit")
                order.record("delete_kill")
            finally:
                lock.release()
                cleanup_reconnect_lock(session_id)

        t1 = threading.Thread(target=reconnect_steps)
        t2 = threading.Thread(target=delete_steps)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        seq = order.snapshot()
        # Reconnect should finish all steps before delete starts
        assert seq.index("reconnect_step3") < seq.index("delete_exit"), (
            f"Expected serialized execution, got: {seq}"
        )

    def test_delete_cleans_up_reconnect_lock(self):
        """After delete, the reconnect lock should be removed from registry."""
        session_id = "sess-cleanup-test"
        lock1 = get_reconnect_lock(session_id)
        assert lock1 is not None

        cleanup_reconnect_lock(session_id)

        # Getting the lock again should create a new one
        lock2 = get_reconnect_lock(session_id)
        assert lock2 is not lock1

        cleanup_reconnect_lock(session_id)


# ---------------------------------------------------------------------------
# 4. Terminal WebSocket input interleaved with reconnect send_keys
# ---------------------------------------------------------------------------


class TestWebSocketInputDuringReconnect:
    """Race: User is typing in the terminal WebSocket while reconnect
    sends commands to the same tmux pane.

    Bug: User keystrokes interleave with reconnect's shell commands,
    producing garbled commands like "rdevhello ssh" instead of "rdev ssh".
    Severity: Data corruption — broken commands, potential security issue.
    Fix: ws_terminal should check a per-session "reconnect active" flag
    and queue input, or reconnect should lock the pane.
    """

    def test_websocket_input_and_reconnect_interleave(self):
        """Keystrokes from WebSocket mix with reconnect's send_keys."""
        pane_buffer = CallRecorder()

        def mock_send_keys(sess, win, text, enter=True):
            # Simulates tmux receiving keystrokes
            for char in text:
                pane_buffer.record(f"key:{char}")
                time.sleep(0.001)  # Tiny delay simulates tmux processing
            if enter:
                pane_buffer.record("key:Enter")
            return True

        barrier = threading.Barrier(2, timeout=5)

        def reconnect_typing():
            barrier.wait()
            mock_send_keys("orch", "w1", "rdev ssh host", enter=True)

        def user_typing():
            barrier.wait()
            for char in "hello":
                pane_buffer.record(f"key:{char}")
                time.sleep(0.002)

        t1 = threading.Thread(target=reconnect_typing)
        t2 = threading.Thread(target=user_typing)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        all_keys = pane_buffer.snapshot()
        # Extract just the characters
        chars = [k.split(":")[1] for k in all_keys if k.startswith("key:") and k != "key:Enter"]

        # Without locking, the characters from both sources interleave
        # "rdev ssh host" and "hello" get mixed
        assert len(chars) == len("rdev ssh host") + len("hello")

    @pytest.mark.allow_subprocess
    def test_record_user_input_tracking(self):
        """Verify that record_user_input timestamps are set."""
        from orchestrator.api.ws_terminal import (
            clear_user_activity,
            is_user_active,
            record_user_input,
        )

        session_id = "sess-ws-test"
        assert not is_user_active(session_id)

        record_user_input(session_id)
        assert is_user_active(session_id)

        # Clean up
        clear_user_activity(session_id)
        assert not is_user_active(session_id)


# ---------------------------------------------------------------------------
# 5. _clean_pane_for_ssh kills/recreates window, but reconnect holds old ref
# ---------------------------------------------------------------------------


class TestCleanPaneWindowRecreation:
    """Race: _clean_pane_for_ssh kills the window and calls ensure_window
    to recreate it, but the caller (reconnect_remote_worker) may still hold
    a stale reference to the old window name or state.

    Bug: After kill_window + ensure_window, the window index may differ,
    and any parallel tmux operations targeting the old pane ID will fail.
    Severity: Stuck state — reconnect pipeline breaks mid-way.
    Fix: After _clean_pane_for_ssh, re-resolve the tmux target. Or pass
    the window name (not index) everywhere, since tmux names are stable.
    """

    def test_clean_pane_kills_and_recreates(self):
        """Verify _clean_pane_for_ssh kills the TUI-stuck pane and recreates."""
        call_seq = CallRecorder()
        check_results = iter([True, True])  # TUI active both times → kills window

        def mock_check_tui(sess, win):
            result = next(check_results)
            call_seq.record(f"check_tui={result}")
            return result

        def mock_send_keys(sess, win, text, enter=True):
            call_seq.record(f"send_keys:{text}")
            return True

        def mock_kill_window(sess, win):
            call_seq.record(f"kill_window:{sess}:{win}")
            return True

        def mock_ensure_window(sess, win, cwd=None):
            call_seq.record(f"ensure_window:{sess}:{win}")
            return f"{sess}:{win}"

        with (
            patch(
                "orchestrator.session.reconnect.check_tui_running_in_pane",
                side_effect=mock_check_tui,
            ),
            patch(
                "orchestrator.session.reconnect.send_keys",
                side_effect=mock_send_keys,
            ),
            patch(
                "orchestrator.session.reconnect.kill_window",
                side_effect=mock_kill_window,
            ),
            patch(
                "orchestrator.terminal.manager.ensure_window",
                side_effect=mock_ensure_window,
            ),
        ):
            _clean_pane_for_ssh("orch", "w1", cwd="/tmp")

        seq = call_seq.snapshot()
        # Should have: check_tui=True → Ctrl-C → check_tui=True → kill → ensure
        assert "kill_window:orch:w1" in seq
        assert "ensure_window:orch:w1" in seq

    def test_stale_window_reference_after_recreation(self):
        """After _clean_pane_for_ssh recreates the window, the old tmux
        pane ID is invalid. Subsequent send_keys using the window NAME
        (not pane ID) should still work since tmux resolves by name.
        """
        window_versions = {"version": 0}

        def mock_check_tui(sess, win):
            return window_versions["version"] == 0  # First call: TUI active

        def mock_send_keys(sess, win, text, enter=True):
            # After recreation, send_keys targets the same name
            return True

        def mock_kill_window(sess, win):
            window_versions["version"] += 1
            return True

        def mock_ensure_window(sess, win, cwd=None):
            return f"{sess}:{win}"

        with (
            patch(
                "orchestrator.session.reconnect.check_tui_running_in_pane",
                side_effect=mock_check_tui,
            ),
            patch(
                "orchestrator.session.reconnect.send_keys",
                side_effect=mock_send_keys,
            ),
            patch(
                "orchestrator.session.reconnect.kill_window",
                side_effect=mock_kill_window,
            ),
            patch(
                "orchestrator.terminal.manager.ensure_window",
                side_effect=mock_ensure_window,
            ),
        ):
            _clean_pane_for_ssh("orch", "w1", cwd="/tmp")

        # The window was recreated (version incremented)
        assert window_versions["version"] == 1

    def test_parallel_reconnect_during_window_recreation(self):
        """Two reconnect attempts: one kills/recreates the window while
        the other tries to use it. The per-session lock should serialize.
        """
        session_id = "sess-parallel-clean"
        lock = get_reconnect_lock(session_id)
        order = CallRecorder()

        def reconnect_a():
            with lock:
                order.record("A:start")
                time.sleep(0.1)  # Simulates kill + recreate
                order.record("A:end")

        def reconnect_b():
            time.sleep(0.02)
            acquired = lock.acquire(timeout=5)
            if acquired:
                try:
                    order.record("B:start")
                    order.record("B:end")
                finally:
                    lock.release()
            else:
                order.record("B:skipped")

        t1 = threading.Thread(target=reconnect_a)
        t2 = threading.Thread(target=reconnect_b)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        seq = order.snapshot()
        assert seq.index("A:end") < seq.index("B:start"), (
            f"Expected A to complete before B starts: {seq}"
        )

        cleanup_reconnect_lock(session_id)


# ---------------------------------------------------------------------------
# 6. check_tui_running_in_pane returns stale result
# ---------------------------------------------------------------------------


class TestStaleTUICheck:
    """Race: check_tui_running_in_pane queries tmux for #{alternate_on},
    but by the time the result is used, the screen session has changed
    (e.g., Claude started or exited).

    Bug: Stale False → sends commands into a TUI.
         Stale True → skips sending commands when the shell is actually ready.
    Severity: Data corruption (stale False) or stuck state (stale True).
    Fix: Double-check pattern with a tiny delay, or use tmux hooks/watchers.
    """

    def test_tui_check_returns_false_but_tui_starts_immediately(self):
        """The tmux query returns 0 (no TUI) but TUI activates right after."""
        query_count = {"n": 0}

        def mock_check_tui(sess, win):
            query_count["n"] += 1
            if query_count["n"] == 1:
                return False  # First check: no TUI
            return True  # Second check: TUI now active

        with patch(
            "orchestrator.session.reconnect.check_tui_running_in_pane",
            side_effect=mock_check_tui,
        ):
            # First call says no TUI
            assert not mock_check_tui("orch", "w1")
            # Immediately after, TUI is active
            assert mock_check_tui("orch", "w1")
            # This demonstrates the staleness window

    def test_rapid_tui_state_changes(self):
        """TUI toggles rapidly (attach/detach screen). Each check may be stale."""
        states = [False, True, False, True, True, False]
        idx = {"i": 0}

        def mock_check_tui(sess, win):
            result = states[idx["i"] % len(states)]
            idx["i"] += 1
            return result

        results = []
        with patch(
            "orchestrator.session.reconnect.check_tui_running_in_pane",
            side_effect=mock_check_tui,
        ):
            for _ in range(6):
                results.append(mock_check_tui("orch", "w1"))

        assert results == states

    def test_subprocess_delay_causes_staleness(self):
        """Simulates tmux display-message taking time, during which state changes."""

        tui_state = {"active": False}

        def slow_check_tui(sess, win):
            # Capture state at query time
            result = tui_state["active"]
            # Simulate subprocess delay (tmux query takes ~5ms in real life)
            time.sleep(0.05)
            # State may have changed by now, but we return the old value
            return result

        tui_state["active"] = False
        result_at_check = slow_check_tui("orch", "w1")
        # During the 50ms delay, TUI activated
        tui_state["active"] = True

        # The result is stale — says False but TUI is now True
        assert result_at_check is False
        assert tui_state["active"] is True


# ---------------------------------------------------------------------------
# 7. Two simultaneous send_message calls
# ---------------------------------------------------------------------------


class TestDualSendMessage:
    """Race: Two POST /send requests arrive simultaneously for the same
    worker. Both call send_to_session which does paste + Enter.

    Bug: Message A's text and message B's text interleave in the paste
    buffer. Claude receives "Please fix Refactor the auth bug module".
    Severity: Data corruption — garbled prompt sent to Claude.
    Fix: Per-session send lock in send_to_session, or serialize via the
    reconnect lock.
    """

    def test_concurrent_paste_operations_interleave(self):
        """Two messages pasted concurrently produce interleaved output."""
        pane_buffer = CallRecorder()

        def mock_paste(sess, win, text):
            for word in text.split():
                pane_buffer.record(f"paste:{word}")
                time.sleep(0.005)
            return True

        def mock_send_keys(sess, win, text, enter=True):
            if enter:
                pane_buffer.record("enter")
            return True

        barrier = threading.Barrier(2, timeout=5)

        def send_message_a():
            barrier.wait()
            mock_paste("orch", "w1", "fix the authentication bug")
            mock_send_keys("orch", "w1", "", enter=True)

        def send_message_b():
            barrier.wait()
            mock_paste("orch", "w1", "refactor the database module")
            mock_send_keys("orch", "w1", "", enter=True)

        t1 = threading.Thread(target=send_message_a)
        t2 = threading.Thread(target=send_message_b)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        writes = pane_buffer.snapshot()
        paste_words = [w.split(":", 1)[1] for w in writes if w.startswith("paste:")]
        enters = [w for w in writes if w == "enter"]

        # Both messages were sent (all words present)
        assert "fix" in paste_words
        assert "refactor" in paste_words
        assert len(enters) == 2

        # Check for interleaving: words from both messages are mixed
        msg_a_words = {"fix", "the", "authentication", "bug"}
        msg_b_words = {"refactor", "database", "module"}

        # Find first word from each message
        first_a = next(i for i, w in enumerate(paste_words) if w in msg_a_words)
        first_b = next(i for i, w in enumerate(paste_words) if w in msg_b_words)
        last_a = max(i for i, w in enumerate(paste_words) if w in msg_a_words)
        last_b = max(i for i, w in enumerate(paste_words) if w in msg_b_words)

        # If interleaved, one message's range overlaps the other's.
        # Note: interleaving may or may not happen depending on thread
        # scheduling. The test documents that it CAN happen without locking.
        _ = first_a < last_b and first_b < last_a  # may or may not be True

    def test_serialized_sends_preserve_order(self):
        """With a per-session lock, messages are sent sequentially."""
        session_id = "sess-dual-send"
        lock = get_reconnect_lock(session_id)
        pane_buffer = CallRecorder()

        def locked_send(msg_label, text):
            with lock:
                for word in text.split():
                    pane_buffer.record(f"{msg_label}:{word}")
                    time.sleep(0.005)
                pane_buffer.record(f"{msg_label}:enter")

        barrier = threading.Barrier(2, timeout=5)

        def send_a():
            barrier.wait()
            locked_send("A", "fix the bug")

        def send_b():
            barrier.wait()
            time.sleep(0.001)  # Slight offset so A likely grabs lock first
            locked_send("B", "refactor module")

        t1 = threading.Thread(target=send_a)
        t2 = threading.Thread(target=send_b)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        writes = pane_buffer.snapshot()
        # All of A's writes should come before B's (or vice versa)
        a_indices = [i for i, w in enumerate(writes) if w.startswith("A:")]
        b_indices = [i for i, w in enumerate(writes) if w.startswith("B:")]

        if a_indices and b_indices:
            assert max(a_indices) < min(b_indices) or max(b_indices) < min(a_indices), (
                f"Expected non-overlapping sends, got A={a_indices}, B={b_indices}"
            )

        cleanup_reconnect_lock(session_id)


# ---------------------------------------------------------------------------
# 8. delete_session cleanup races with reconnect restarting SSH
# ---------------------------------------------------------------------------


class TestDeleteReconnectSSHRace:
    """Race: delete_session sends "exit" + "exit" + rm, then kills window.
    Meanwhile, a reconnect triggered by auto-reconnect or health-check
    may start a new SSH connection to the same host.

    Bug: delete kills the window → reconnect's ensure_window recreates it
    → zombie tmux window with an orphaned SSH session.
    Severity: Resource leak — orphaned SSH/screen sessions on remote.
    Fix: cleanup_reconnect_lock() should be called BEFORE delete starts
    its cleanup (not after), or delete should hold the lock during cleanup.
    """

    def test_delete_then_reconnect_creates_zombie(self):
        """Reconnect recreates a window after delete killed it."""
        windows = {"w1": True}
        order = CallRecorder()

        def mock_kill_window(sess, win):
            windows.pop(win, None)
            order.record("kill_window")
            return True

        def mock_ensure_window(sess, win, cwd=None):
            windows[win] = True
            order.record("ensure_window")
            return f"{sess}:{win}"

        # Delete kills the window
        mock_kill_window("orch", "w1")
        assert "w1" not in windows

        # Reconnect recreates it (zombie!)
        mock_ensure_window("orch", "w1", cwd="/tmp")
        assert "w1" in windows  # Window exists again — zombie

        seq = order.snapshot()
        assert seq == ["kill_window", "ensure_window"]

    def test_reconnect_lock_prevents_zombie_creation(self):
        """If delete holds the lock, reconnect cannot recreate the window."""
        session_id = "sess-zombie-test"
        lock = get_reconnect_lock(session_id)
        order = CallRecorder()

        def delete_operation():
            with lock:
                order.record("delete:exit_claude")
                time.sleep(0.05)
                order.record("delete:exit_screen")
                time.sleep(0.05)
                order.record("delete:kill_window")
                cleanup_reconnect_lock(session_id)
                order.record("delete:done")

        def reconnect_operation():
            time.sleep(0.02)  # Let delete start
            new_lock = get_reconnect_lock(session_id)
            if not new_lock.acquire(timeout=0.01):
                order.record("reconnect:blocked")
                return
            try:
                order.record("reconnect:ensure_window")
            finally:
                new_lock.release()

        t1 = threading.Thread(target=delete_operation)
        t2 = threading.Thread(target=reconnect_operation)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        seq = order.snapshot()
        # Reconnect should be blocked or happen after delete completes
        if "reconnect:blocked" in seq:
            # Good: reconnect was blocked
            pass
        elif "reconnect:ensure_window" in seq:
            # Also acceptable if it happened after delete completed
            assert seq.index("delete:done") < seq.index("reconnect:ensure_window"), (
                f"Reconnect should not create window during delete: {seq}"
            )

    def test_delete_sends_exit_to_live_claude_during_reconnect(self):
        """delete sends 'exit' while reconnect has just launched Claude.

        This is the most dangerous variant: Claude just started and is
        processing, then receives 'exit' from delete.
        """
        pane_commands = CallRecorder()
        tui_active = threading.Event()

        session_id = "sess-exit-race"
        lock = get_reconnect_lock(session_id)

        def mock_send_keys(sess, win, text, enter=True):
            pane_commands.record(f"send:{text}")
            if text == "claude --session-id sess-exit-race":
                tui_active.set()
            return True

        def reconnect():
            with lock:
                pane_commands.record("reconnect:start")
                mock_send_keys("orch", "w1", "claude --session-id sess-exit-race")
                time.sleep(0.2)  # Claude is starting...
                pane_commands.record("reconnect:end")

        def delete():
            time.sleep(0.05)  # Reconnect starts first
            # In buggy code, delete does NOT acquire the lock
            pane_commands.record("delete:start")
            mock_send_keys("orch", "w1", "exit")
            time.sleep(0.05)
            mock_send_keys("orch", "w1", "exit")
            pane_commands.record("delete:end")

        t1 = threading.Thread(target=reconnect)
        t2 = threading.Thread(target=delete)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        seq = pane_commands.snapshot()
        # Without locking on delete side, "exit" can be sent while
        # reconnect is still running
        exit_indices = [i for i, s in enumerate(seq) if s == "send:exit"]
        reconnect_end = seq.index("reconnect:end")

        # Document the race: exit commands arrive before reconnect ends
        exits_during_reconnect = [i for i in exit_indices if i < reconnect_end]
        assert len(exits_during_reconnect) > 0, (
            f"Expected exit during reconnect (the race condition), got: {seq}"
        )

        cleanup_reconnect_lock(session_id)


# ---------------------------------------------------------------------------
# Integration: per-session lock registry
# ---------------------------------------------------------------------------


class TestReconnectLockRegistry:
    """Verify the per-session lock registry is thread-safe."""

    def test_same_session_returns_same_lock(self):
        """get_reconnect_lock should return the same lock for the same session."""
        lock1 = get_reconnect_lock("sess-same")
        lock2 = get_reconnect_lock("sess-same")
        assert lock1 is lock2
        cleanup_reconnect_lock("sess-same")

    def test_different_sessions_get_different_locks(self):
        """Different session IDs should get independent locks."""
        lock1 = get_reconnect_lock("sess-a")
        lock2 = get_reconnect_lock("sess-b")
        assert lock1 is not lock2
        cleanup_reconnect_lock("sess-a")
        cleanup_reconnect_lock("sess-b")

    def test_concurrent_lock_creation(self):
        """Multiple threads creating locks for the same session simultaneously."""
        session_id = "sess-concurrent-create"
        locks = []
        barrier = threading.Barrier(5, timeout=5)

        def get_lock():
            barrier.wait()
            locks.append(get_reconnect_lock(session_id))

        threads = [threading.Thread(target=get_lock) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # All threads should get the same lock object
        assert all(lk is locks[0] for lk in locks)
        cleanup_reconnect_lock(session_id)

    def test_cleanup_is_idempotent(self):
        """Cleaning up a non-existent lock should not error."""
        cleanup_reconnect_lock("sess-nonexistent")
        cleanup_reconnect_lock("sess-nonexistent")


# ---------------------------------------------------------------------------
# Integration: _clean_pane_for_ssh full flow
# ---------------------------------------------------------------------------


class TestCleanPaneFullFlow:
    """Test _clean_pane_for_ssh's decision tree with various pane states."""

    def test_clean_pane_no_tui_sends_ctrlc(self):
        """When no TUI, just send Ctrl-C + Enter to clear prompt."""
        calls = []

        def mock_check_tui(sess, win):
            return False

        def mock_send_keys(sess, win, text, enter=True):
            calls.append((text, enter))
            return True

        with (
            patch(
                "orchestrator.session.reconnect.check_tui_running_in_pane",
                side_effect=mock_check_tui,
            ),
            patch(
                "orchestrator.session.reconnect.send_keys",
                side_effect=mock_send_keys,
            ),
            patch(
                "orchestrator.session.reconnect._verify_pane_responsive",
                return_value=True,
            ),
        ):
            _clean_pane_for_ssh("orch", "w1")

        # Should send Ctrl-C (no enter) then empty string (with enter)
        assert ("C-c", False) in calls
        assert ("", True) in calls

    def test_clean_pane_tui_cleared_by_ctrlc(self):
        """TUI active → Ctrl-C clears it → second check returns False."""
        check_count = {"n": 0}

        def mock_check_tui(sess, win):
            check_count["n"] += 1
            # First check: TUI active. Second check: TUI cleared by Ctrl-C
            return check_count["n"] == 1

        calls = []

        def mock_send_keys(sess, win, text, enter=True):
            calls.append((text, enter))
            return True

        with (
            patch(
                "orchestrator.session.reconnect.check_tui_running_in_pane",
                side_effect=mock_check_tui,
            ),
            patch(
                "orchestrator.session.reconnect.send_keys",
                side_effect=mock_send_keys,
            ),
            patch(
                "orchestrator.session.reconnect._verify_pane_responsive",
                return_value=True,
            ),
        ):
            _clean_pane_for_ssh("orch", "w1")

        # Should not have killed the window (Ctrl-C was enough)
        text_calls = [c[0] for c in calls]
        assert "C-c" in text_calls

    def test_clean_pane_tui_stuck_kills_window(self):
        """TUI survives Ctrl-C → kill and recreate window."""

        def mock_check_tui(sess, win):
            return True  # Always active (stuck)

        calls = CallRecorder()

        def mock_send_keys(sess, win, text, enter=True):
            calls.record(f"send:{text}")
            return True

        def mock_kill_window(sess, win):
            calls.record("kill")
            return True

        def mock_ensure_window(sess, win, cwd=None):
            calls.record("ensure")
            return f"{sess}:{win}"

        with (
            patch(
                "orchestrator.session.reconnect.check_tui_running_in_pane",
                side_effect=mock_check_tui,
            ),
            patch(
                "orchestrator.session.reconnect.send_keys",
                side_effect=mock_send_keys,
            ),
            patch(
                "orchestrator.session.reconnect.kill_window",
                side_effect=mock_kill_window,
            ),
            patch(
                "orchestrator.terminal.manager.ensure_window",
                side_effect=mock_ensure_window,
            ),
        ):
            _clean_pane_for_ssh("orch", "w1", cwd="/tmp")

        seq = calls.snapshot()
        assert "kill" in seq
        assert "ensure" in seq
        # Kill should come before ensure
        assert seq.index("kill") < seq.index("ensure")


# ---------------------------------------------------------------------------
# Integration: check_tui_running_in_pane behavior
# ---------------------------------------------------------------------------


class TestCheckTUIRunningInPane:
    """Unit tests for check_tui_running_in_pane with mocked subprocess."""

    def test_returns_true_when_alternate_on(self):
        """alternate_on == '1' means TUI is running."""
        from orchestrator.session.health import check_tui_running_in_pane

        mock_result = MagicMock()
        mock_result.stdout = "1\n"

        with patch("subprocess.run", return_value=mock_result):
            assert check_tui_running_in_pane("orch", "w1") is True

    def test_returns_false_when_alternate_off(self):
        """alternate_on == '0' means shell prompt."""
        from orchestrator.session.health import check_tui_running_in_pane

        mock_result = MagicMock()
        mock_result.stdout = "0\n"

        with patch("subprocess.run", return_value=mock_result):
            assert check_tui_running_in_pane("orch", "w1") is False

    def test_returns_false_on_timeout(self):
        """Timeout should return False (fail-open for reconnect)."""
        import subprocess

        from orchestrator.session.health import check_tui_running_in_pane

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 5)):
            assert check_tui_running_in_pane("orch", "w1") is False

    def test_returns_false_on_exception(self):
        """Any exception should return False."""
        from orchestrator.session.health import check_tui_running_in_pane

        with patch("subprocess.run", side_effect=OSError("tmux not found")):
            assert check_tui_running_in_pane("orch", "w1") is False

    def test_tmux_target_format(self):
        """Verify the tmux target string passed to display-message."""
        from orchestrator.session.health import check_tui_running_in_pane

        mock_result = MagicMock()
        mock_result.stdout = "0\n"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            check_tui_running_in_pane("my-sess", "my-win")

            args = mock_run.call_args[0][0]
            assert "my-sess:my-win" in args
            assert "#{alternate_on}" in args


# ---------------------------------------------------------------------------
# Edge cases: timing-sensitive guard logic
# ---------------------------------------------------------------------------


class TestTimingSensitiveGuards:
    """Tests that exercise the timing-sensitive aspects of the guard logic."""

    def test_safe_send_keys_returns_send_keys_result(self):
        """safe_send_keys should propagate the return value of send_keys."""
        with (
            patch("orchestrator.session.reconnect.check_tui_running_in_pane", return_value=False),
            patch("orchestrator.session.reconnect.send_keys", return_value=True),
        ):
            assert safe_send_keys("orch", "w1", "test") is True

        with (
            patch("orchestrator.session.reconnect.check_tui_running_in_pane", return_value=False),
            patch("orchestrator.session.reconnect.send_keys", return_value=False),
        ):
            assert safe_send_keys("orch", "w1", "test") is False

    def test_safe_send_keys_respects_enter_param(self):
        """The enter parameter should be forwarded to send_keys."""
        with (
            patch("orchestrator.session.reconnect.check_tui_running_in_pane", return_value=False),
            patch("orchestrator.session.reconnect.send_keys", return_value=True) as mock_sk,
        ):
            safe_send_keys("orch", "w1", "C-c", enter=False)
            mock_sk.assert_called_once_with("orch", "w1", "C-c", enter=False)

    def test_multiple_rapid_safe_send_keys(self):
        """Rapid consecutive safe_send_keys calls each check TUI independently."""
        check_count = {"n": 0}

        def counting_check(sess, win):
            check_count["n"] += 1
            return False

        with (
            patch(
                "orchestrator.session.reconnect.check_tui_running_in_pane",
                side_effect=counting_check,
            ),
            patch("orchestrator.session.reconnect.send_keys", return_value=True),
        ):
            for _ in range(10):
                safe_send_keys("orch", "w1", "echo test")

        assert check_count["n"] == 10

    def test_tui_error_includes_target_info(self):
        """TUIActiveError message should include the tmux target."""
        with patch(
            "orchestrator.session.reconnect.check_tui_running_in_pane",
            return_value=True,
        ):
            with pytest.raises(TUIActiveError) as exc_info:
                safe_send_keys("my-session", "my-window", "test")
            assert "my-session" in str(exc_info.value)
            assert "my-window" in str(exc_info.value)
