"""Tests for output parser — pattern detection against sample terminal outputs.

NOTE: Worker status is managed by Claude Code hooks. This module only tests
event detection (PR created, tests passed, etc.), not status detection.
"""

from orchestrator.terminal.output_parser import (
    EventType,
    parse_output,
)

# --- Event Detection ---

class TestParseOutput:
    def test_detect_pr_created_with_url(self):
        output = "Created PR at https://github.com/org/repo/pull/42"
        events = parse_output(output)
        assert any(e.event_type == EventType.PR_CREATED for e in events)

    def test_detect_pr_created_with_number(self):
        output = "PR #123 created successfully"
        events = parse_output(output)
        assert any(e.event_type == EventType.PR_CREATED for e in events)

    def test_detect_test_pass(self):
        output = "===== 42 passed in 3.2s ====="
        events = parse_output(output)
        assert any(e.event_type == EventType.TEST_PASS for e in events)

    def test_detect_all_tests_pass(self):
        output = "All tests pass! Great job."
        events = parse_output(output)
        assert any(e.event_type == EventType.TEST_PASS for e in events)

    def test_detect_test_fail(self):
        output = "FAILED tests/unit/test_foo.py::test_bar"
        events = parse_output(output)
        assert any(e.event_type == EventType.TEST_FAIL for e in events)

    def test_detect_test_fail_count(self):
        output = "3 failed, 10 passed in 5.1s"
        events = parse_output(output)
        assert any(e.event_type == EventType.TEST_FAIL for e in events)

    def test_detect_build_success(self):
        output = "Build succeeded. Output in dist/"
        events = parse_output(output)
        assert any(e.event_type == EventType.BUILD_SUCCESS for e in events)

    def test_detect_build_failure(self):
        output = "Build failed with 3 errors"
        events = parse_output(output)
        assert any(e.event_type == EventType.BUILD_FAILURE for e in events)

    def test_detect_traceback_error(self):
        output = """Traceback (most recent call last):
  File "foo.py", line 10, in bar
    raise ValueError("bad")
ValueError: bad"""
        events = parse_output(output)
        assert any(e.event_type == EventType.ERROR for e in events)

    def test_no_events_from_normal_output(self):
        output = "Hello, I'm working on the task.\nLet me check the files."
        events = parse_output(output)
        assert len(events) == 0

    def test_detect_compiled_successfully(self):
        output = "Compiled successfully in 2.5s"
        events = parse_output(output)
        assert any(e.event_type == EventType.BUILD_SUCCESS for e in events)
