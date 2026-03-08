"""Regression test for closure-in-loop bug in health-check-all auto-reconnect.

When multiple workers are auto-reconnected simultaneously in the health-check-all
loop, each background thread must receive its own correct tmp_dir value.
Previously, tmp_dir was captured by reference (free variable) instead of by value
(default parameter), causing all threads to use the last worker's tmp_dir.
"""

import os
import threading

import pytest

pytestmark = pytest.mark.allow_threading

WORKER_BASE_DIR = "/tmp/orchestrator/workers"


class TestAutoReconnectClosureBinding:
    """Verify each auto-reconnect thread gets its own tmp_dir.

    Reproduces the exact closure pattern from the health-check-all endpoint
    without needing the full FastAPI app.
    """

    def test_each_thread_gets_own_tmp_dir(self):
        """When 3 workers auto-reconnect, each thread must get its own tmp_dir.

        This replicates the loop in sessions.py health-check-all that spawns
        background threads for auto-reconnect. The fix binds tmp_dir via a
        default parameter (td=tmp_dir) instead of capturing it as a free variable.
        """
        worker_names = ["worker-a", "worker-b", "worker-c"]
        captured = []

        def mock_reconnect(session_name, tmp_dir):
            captured.append({"session_name": session_name, "tmp_dir": tmp_dir})

        threads = []
        # Replicate the FIXED loop pattern from sessions.py line 1134-1163
        for name in worker_names:
            tmux_sess, tmux_win = "orchestrator", name
            tmp_dir = os.path.join(WORKER_BASE_DIR, name)

            def _bg_reconnect(session_name=name, ts=tmux_sess, tw=tmux_win, td=tmp_dir):
                mock_reconnect(session_name, td)

            t = threading.Thread(target=_bg_reconnect, daemon=True)
            threads.append(t)

        # Start all threads after the loop (simulating the real scenario)
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(captured) == 3, f"Expected 3 calls, got {len(captured)}"

        # Each worker must get its own tmp_dir
        for entry in captured:
            assert entry["tmp_dir"].endswith(entry["session_name"]), (
                f"Worker {entry['session_name']} got tmp_dir={entry['tmp_dir']} "
                f"which does not end with its own name. "
                f"Closure-in-loop bug: tmp_dir captured by reference."
            )

        # All tmp_dirs must be distinct
        tmp_dirs = [e["tmp_dir"] for e in captured]
        assert len(set(tmp_dirs)) == 3, (
            f"Expected 3 distinct tmp_dirs, got {tmp_dirs}. "
            f"All threads got the same tmp_dir (closure bug)."
        )

    def test_unbound_closure_would_fail(self):
        """Demonstrate that WITHOUT default-param binding, all threads get the last value.

        This is the BROKEN pattern that was present before the fix.
        """
        worker_names = ["worker-a", "worker-b", "worker-c"]
        captured = []

        def mock_reconnect(session_name, tmp_dir):
            captured.append({"session_name": session_name, "tmp_dir": tmp_dir})

        threads = []
        # Replicate the BROKEN pattern: tmp_dir NOT bound via default param
        for name in worker_names:
            tmp_dir = os.path.join(WORKER_BASE_DIR, name)

            def _bg_reconnect_broken(session_name=name):
                # tmp_dir is a FREE VARIABLE here — captured by reference
                mock_reconnect(session_name, tmp_dir)

            t = threading.Thread(target=_bg_reconnect_broken, daemon=True)
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(captured) == 3

        # All threads should have the LAST worker's tmp_dir (the bug)
        tmp_dirs = [e["tmp_dir"] for e in captured]
        assert all(td.endswith("worker-c") for td in tmp_dirs), (
            f"Expected all threads to get worker-c's tmp_dir (demonstrating the bug), "
            f"got {tmp_dirs}"
        )
