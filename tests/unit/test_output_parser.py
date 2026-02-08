"""Tests for output parser — pattern detection against sample terminal outputs."""

from orchestrator.terminal.output_parser import (
    EventType,
    SessionState,
    detect_state,
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

    def test_detect_compact(self):
        output = "Context has been compacted successfully."
        events = parse_output(output)
        assert any(e.event_type == EventType.COMPACT for e in events)

    def test_no_events_from_normal_output(self):
        output = "Hello, I'm working on the task.\nLet me check the files."
        events = parse_output(output)
        assert len(events) == 0

    def test_detect_compiled_successfully(self):
        output = "Compiled successfully in 2.5s"
        events = parse_output(output)
        assert any(e.event_type == EventType.BUILD_SUCCESS for e in events)


# --- State Detection ---

class TestDetectState:
    def test_idle_bare_prompt(self):
        output = "some previous output\n>\n"
        assert detect_state(output) == SessionState.IDLE

    def test_working_reading_file(self):
        output = "Reading file src/main.py..."
        assert detect_state(output) == SessionState.WORKING

    def test_working_running_command(self):
        output = "Running: pytest tests/"
        assert detect_state(output) == SessionState.WORKING

    def test_error_traceback(self):
        output = """Traceback (most recent call last):
  File "x.py", line 1
TypeError: bad"""
        assert detect_state(output) == SessionState.ERROR

    def test_unknown_empty_output(self):
        assert detect_state("") == SessionState.UNKNOWN
        assert detect_state("   ") == SessionState.UNKNOWN

    def test_working_editing(self):
        output = "Editing file orchestrator/main.py"
        assert detect_state(output) == SessionState.WORKING
