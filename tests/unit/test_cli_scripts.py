"""Tests for CLI script generation and JSON encoding logic.

These tests verify that:
1. CLI scripts are generated correctly with proper escaping
2. The json_encode helper function is included and works correctly
3. stdin options (--content-stdin, --notes-stdin, etc.) are supported
"""

import os
import subprocess
import tempfile
import pytest

from orchestrator.brain.cli_scripts import (
    generate_brain_scripts,
    BRAIN_CONTEXT_SCRIPT,
    BRAIN_SCRIPT_HEADER,
    BRAIN_WORKERS_SCRIPT,
    BRAIN_PROJECTS_SCRIPT,
    BRAIN_TASKS_SCRIPT,
    BRAIN_SEND_SCRIPT,
    BRAIN_NOTIFICATIONS_SCRIPT,
)
from orchestrator.worker.cli_scripts import (
    generate_worker_scripts,
    ORCH_TASK_SCRIPT,
    ORCH_SUBTASK_SCRIPT,
    SCRIPT_HEADER,
)


class TestBrainCliScripts:
    """Tests for brain CLI script generation."""

    def test_generate_brain_scripts_creates_files(self):
        """Verify all expected scripts are created with correct permissions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = generate_brain_scripts(tmpdir, "http://localhost:8093")
            
            expected_scripts = [
                "orch-workers",
                "orch-projects", 
                "orch-tasks",
                "orch-ctx",
                "orch-send",
                "orch-notifications",
            ]
            
            for script_name in expected_scripts:
                script_path = os.path.join(bin_dir, script_name)
                assert os.path.exists(script_path), f"{script_name} should exist"
                assert os.access(script_path, os.X_OK), f"{script_name} should be executable"

    def test_brain_context_script_has_json_encode(self):
        """Verify the json_encode helper is included in context script."""
        assert "json_encode()" in BRAIN_CONTEXT_SCRIPT
        assert "jq -Rs" in BRAIN_CONTEXT_SCRIPT
        assert "python3 -c" in BRAIN_CONTEXT_SCRIPT

    def test_brain_context_script_has_content_stdin(self):
        """Verify --content-stdin option is supported."""
        assert "--content-stdin" in BRAIN_CONTEXT_SCRIPT
        assert "content_stdin" in BRAIN_CONTEXT_SCRIPT

    def test_brain_context_script_has_content_file(self):
        """Verify --content-file option is supported."""
        assert "--content-file" in BRAIN_CONTEXT_SCRIPT
        assert "content_file" in BRAIN_CONTEXT_SCRIPT

    def test_brain_context_script_uses_json_encode_for_content(self):
        """Verify content is properly JSON-encoded using json_encode."""
        assert 'escaped_content=$(json_encode "$content")' in BRAIN_CONTEXT_SCRIPT

    def test_brain_tasks_script_has_delete_command(self):
        """Verify orch-tasks has delete command."""
        assert "delete <id>" in BRAIN_TASKS_SCRIPT
        assert "cmd_delete()" in BRAIN_TASKS_SCRIPT
        assert 'curl -s -X DELETE "$API_BASE/api/tasks/$id"' in BRAIN_TASKS_SCRIPT
        assert "delete) shift; cmd_delete" in BRAIN_TASKS_SCRIPT

    def test_brain_workers_script_has_stop_command(self):
        """Verify orch-workers has stop command."""
        assert "stop <id>" in BRAIN_WORKERS_SCRIPT
        assert "cmd_stop()" in BRAIN_WORKERS_SCRIPT
        assert 'curl -s -X POST "$API_BASE/api/sessions/$id/stop"' in BRAIN_WORKERS_SCRIPT
        assert "stop) shift; cmd_stop" in BRAIN_WORKERS_SCRIPT

    def test_brain_workers_script_has_reconnect_command(self):
        """Verify orch-workers has reconnect command."""
        assert "reconnect <id>" in BRAIN_WORKERS_SCRIPT
        assert "cmd_reconnect()" in BRAIN_WORKERS_SCRIPT
        assert 'curl -s -X POST "$API_BASE/api/sessions/$id/reconnect"' in BRAIN_WORKERS_SCRIPT
        assert "reconnect) shift; cmd_reconnect" in BRAIN_WORKERS_SCRIPT

    def test_brain_notifications_script_has_delete_command(self):
        """Verify orch-notifications has delete command."""
        assert "delete <id>" in BRAIN_NOTIFICATIONS_SCRIPT
        assert "cmd_delete()" in BRAIN_NOTIFICATIONS_SCRIPT
        assert 'curl -s -X DELETE "$API_BASE/api/notifications/$id"' in BRAIN_NOTIFICATIONS_SCRIPT
        assert "delete) shift; cmd_delete" in BRAIN_NOTIFICATIONS_SCRIPT


class TestWorkerCliScripts:
    """Tests for worker CLI script generation."""

    def test_generate_worker_scripts_creates_files(self):
        """Verify all expected worker scripts are created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = generate_worker_scripts(
                tmpdir, "test-worker", "session-123", "http://localhost:8093"
            )
            
            expected_scripts = [
                "orch-task",
                "orch-subtask",
                "orch-worker",
                "orch-context",
            ]
            
            for script_name in expected_scripts:
                script_path = os.path.join(bin_dir, script_name)
                assert os.path.exists(script_path), f"{script_name} should exist"
                assert os.access(script_path, os.X_OK), f"{script_name} should be executable"

    def test_worker_script_header_has_json_encode(self):
        """Verify the json_encode helper is in the shared header."""
        assert "json_encode()" in SCRIPT_HEADER
        assert "jq -Rs" in SCRIPT_HEADER
        assert "python3 -c" in SCRIPT_HEADER

    def test_task_script_has_notes_stdin(self):
        """Verify --notes-stdin option is supported in task script."""
        assert "--notes-stdin" in ORCH_TASK_SCRIPT
        assert "notes_stdin" in ORCH_TASK_SCRIPT

    def test_task_script_uses_json_encode_for_notes(self):
        """Verify notes are properly JSON-encoded."""
        assert 'escaped_notes=$(json_encode "$notes")' in ORCH_TASK_SCRIPT

    def test_subtask_script_has_notes_stdin(self):
        """Verify --notes-stdin option is supported in subtask script."""
        assert "--notes-stdin" in ORCH_SUBTASK_SCRIPT
        assert "notes_stdin" in ORCH_SUBTASK_SCRIPT

    def test_subtask_script_has_description_stdin(self):
        """Verify --description-stdin option is supported in subtask script."""
        assert "--description-stdin" in ORCH_SUBTASK_SCRIPT
        assert "description_stdin" in ORCH_SUBTASK_SCRIPT

    def test_subtask_script_uses_json_encode(self):
        """Verify subtask script uses json_encode for text fields."""
        assert 'escaped_notes=$(json_encode "$notes")' in ORCH_SUBTASK_SCRIPT
        assert 'escaped_desc=$(json_encode "$description")' in ORCH_SUBTASK_SCRIPT


class TestJsonEncodeFunction:
    """Tests for the json_encode bash function via actual execution."""

    @pytest.fixture
    def json_encode_script(self):
        """Create a minimal script that exposes json_encode for testing."""
        script = '''#!/bin/bash
# Helper: JSON-encode a string (handles newlines, quotes, backslashes, etc.)
json_encode() {
    if command -v jq &> /dev/null; then
        printf '%s' "$1" | jq -Rs . | sed 's/^"//;s/"$//'
    else
        python3 -c "import json,sys; print(json.dumps(sys.stdin.read())[1:-1])" <<< "$1"
    fi
}

# Test: output the JSON-encoded version of the input
json_encode "$1"
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(script)
            f.flush()
            os.chmod(f.name, 0o755)
            yield f.name
        os.unlink(f.name)

    def test_json_encode_simple_string(self, json_encode_script):
        """Test encoding a simple string without special characters."""
        result = subprocess.run(
            [json_encode_script, "hello world"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "hello world"

    def test_json_encode_with_quotes(self, json_encode_script):
        """Test encoding a string with double quotes."""
        result = subprocess.run(
            [json_encode_script, 'hello "world"'],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert result.stdout.strip() == r'hello \"world\"'

    def test_json_encode_with_newlines(self, json_encode_script):
        """Test encoding a string with newlines."""
        result = subprocess.run(
            [json_encode_script, "line1\nline2\nline3"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert result.stdout.strip() == r"line1\nline2\nline3"

    def test_json_encode_with_backslashes(self, json_encode_script):
        """Test encoding a string with backslashes."""
        result = subprocess.run(
            [json_encode_script, r"path\to\file"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert result.stdout.strip() == r"path\\to\\file"

    def test_json_encode_with_tabs(self, json_encode_script):
        """Test encoding a string with tab characters."""
        result = subprocess.run(
            [json_encode_script, "col1\tcol2\tcol3"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert result.stdout.strip() == r"col1\tcol2\tcol3"

    def test_json_encode_markdown_content(self, json_encode_script):
        """Test encoding typical markdown content with special chars."""
        markdown = '''# Heading

This has:
- `backticks`
- "quotes"
- backslash \\
- newlines'''
        result = subprocess.run(
            [json_encode_script, markdown],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        # Verify key escapes are present
        encoded = result.stdout.strip()
        assert r"\n" in encoded  # newlines escaped
        assert r"\"" in encoded  # quotes escaped
        assert r"\\" in encoded  # backslashes escaped


class TestStdinOptions:
    """Tests for --*-stdin options in generated scripts."""

    @pytest.fixture
    def brain_ctx_script(self):
        """Generate brain orch-ctx script for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = generate_brain_scripts(tmpdir, "http://localhost:8093")
            script_path = os.path.join(bin_dir, "orch-ctx")
            with open(script_path, 'r') as f:
                content = f.read()
            yield script_path, content

    def test_content_stdin_reads_from_stdin(self, brain_ctx_script):
        """Verify --content-stdin triggers reading from cat."""
        script_path, content = brain_ctx_script
        # Check the logic flow
        assert 'if [[ -n "$content_stdin" ]]; then' in content
        assert 'content=$(cat)' in content

    def test_content_file_reads_from_file(self, brain_ctx_script):
        """Verify --content-file reads content from specified file."""
        script_path, content = brain_ctx_script
        assert 'if [[ -n "$content_file" ]]; then' in content
        assert 'content=$(cat "$content_file")' in content

    def test_help_text_includes_stdin_option(self, brain_ctx_script):
        """Verify help text documents the --content-stdin option."""
        script_path, content = brain_ctx_script
        assert "--content-stdin" in content
        assert "Read content from stdin" in content


class TestCrossPlatformCompatibility:
    """Tests verifying cross-platform (macOS/Linux) compatibility."""

    def test_no_gnu_sed_syntax_in_brain_scripts(self):
        """Ensure no GNU-specific sed multi-line syntax is used."""
        # The problematic pattern was: sed ':a;N;$!ba;s/\n/\\n/g'
        assert ":a;N;$!ba" not in BRAIN_CONTEXT_SCRIPT
        assert ":a;N;$!ba" not in BRAIN_SCRIPT_HEADER

    def test_no_gnu_sed_syntax_in_worker_scripts(self):
        """Ensure no GNU-specific sed syntax in worker scripts."""
        assert ":a;N;$!ba" not in ORCH_TASK_SCRIPT
        assert ":a;N;$!ba" not in ORCH_SUBTASK_SCRIPT
        assert ":a;N;$!ba" not in SCRIPT_HEADER

    def test_uses_portable_sed_for_quote_stripping(self):
        """Verify sed command for stripping quotes is POSIX-compatible."""
        # The portable pattern: sed 's/^"//;s/"$//'
        assert "sed 's/^\"//;s/\"$//'" in SCRIPT_HEADER
        # Brain scripts include it via the json_encode function
        formatted_header = BRAIN_SCRIPT_HEADER.format(
            script_name="test",
            script_description="test",
            api_base="http://localhost:8093"
        )
        # Check brain context script which has json_encode
        assert "sed 's/^\"//;s/\"$//'" in BRAIN_CONTEXT_SCRIPT


class TestJsonEscapingInBrainScripts:
    """Tests verifying all brain CLI scripts properly escape JSON strings."""

    def test_brain_header_has_json_encode(self):
        """Verify json_encode is in brain script header for all scripts to use."""
        assert "json_encode()" in BRAIN_SCRIPT_HEADER
        assert "jq -Rs" in BRAIN_SCRIPT_HEADER

    def test_brain_header_build_json_uses_json_encode(self):
        """Verify build_json helper escapes string values."""
        assert "escaped_value=$(json_encode \"$value\")" in BRAIN_SCRIPT_HEADER

    def test_workers_script_escapes_name_and_host(self):
        """Verify orch-workers create escapes name and host fields."""
        assert 'escaped_name=$(json_encode "$name")' in BRAIN_WORKERS_SCRIPT
        assert 'escaped_host=$(json_encode "$host")' in BRAIN_WORKERS_SCRIPT
        assert 'escaped_work_dir=$(json_encode "$work_dir")' in BRAIN_WORKERS_SCRIPT

    def test_projects_script_escapes_fields(self):
        """Verify orch-projects create/update escapes all text fields."""
        assert 'escaped_name=$(json_encode "$name")' in BRAIN_PROJECTS_SCRIPT
        assert 'escaped_desc=$(json_encode "$description")' in BRAIN_PROJECTS_SCRIPT

    def test_tasks_script_escapes_title(self):
        """Verify orch-tasks create/update escapes title field."""
        assert 'escaped_title=$(json_encode "$title")' in BRAIN_TASKS_SCRIPT

    def test_tasks_script_escapes_links(self):
        """Verify orch-tasks update escapes link URL and tag."""
        assert 'escaped_link=$(json_encode "$add_link")' in BRAIN_TASKS_SCRIPT
        assert 'escaped_tag=$(json_encode "$add_link_tag")' in BRAIN_TASKS_SCRIPT

    def test_context_script_escapes_title_and_description(self):
        """Verify orch-ctx create/update escapes title and description."""
        # cmd_create
        assert 'escaped_title=$(json_encode "$title")' in BRAIN_CONTEXT_SCRIPT
        # cmd_update - description
        assert 'escaped_desc=$(json_encode "$description")' in BRAIN_CONTEXT_SCRIPT

    def test_send_script_escapes_message(self):
        """Verify orch-send escapes message field."""
        assert 'escaped_message=$(json_encode "$message")' in BRAIN_SEND_SCRIPT
        assert '\\"message\\": \\"$escaped_message\\"' in BRAIN_SEND_SCRIPT

    def test_notifications_script_escapes_message_and_link(self):
        """Verify orch-notifications create escapes message and link fields."""
        assert 'escaped_message=$(json_encode "$message")' in BRAIN_NOTIFICATIONS_SCRIPT
        assert 'escaped_link=$(json_encode "$link")' in BRAIN_NOTIFICATIONS_SCRIPT


class TestJsonEscapingInWorkerScripts:
    """Tests verifying all worker CLI scripts properly escape JSON strings."""

    def test_worker_header_has_json_encode(self):
        """Verify json_encode is in worker script header."""
        assert "json_encode()" in SCRIPT_HEADER
        assert "jq -Rs" in SCRIPT_HEADER

    def test_task_script_escapes_links(self):
        """Verify orch-task update escapes link URL and tag."""
        assert 'escaped_link=$(json_encode "$add_link")' in ORCH_TASK_SCRIPT
        assert 'escaped_tag=$(json_encode "$add_link_tag")' in ORCH_TASK_SCRIPT

    def test_subtask_script_escapes_title(self):
        """Verify orch-subtask create escapes title field."""
        assert 'escaped_title=$(json_encode "$title")' in ORCH_SUBTASK_SCRIPT

    def test_subtask_script_escapes_links_in_create(self):
        """Verify orch-subtask create escapes URLs in comma-separated links."""
        assert 'escaped_url=$(json_encode "$url")' in ORCH_SUBTASK_SCRIPT

    def test_subtask_script_escapes_links_in_update(self):
        """Verify orch-subtask update escapes link URL and tag."""
        assert 'escaped_link=$(json_encode "$add_link")' in ORCH_SUBTASK_SCRIPT
        assert 'escaped_tag=$(json_encode "$add_link_tag")' in ORCH_SUBTASK_SCRIPT


class TestJsonEncodeEdgeCases:
    """Integration tests for json_encode handling various edge cases."""

    @pytest.fixture
    def json_encode_script(self):
        """Create a minimal script that exposes json_encode for testing."""
        script = '''#!/bin/bash
json_encode() {
    if command -v jq &> /dev/null; then
        printf '%s' "$1" | jq -Rs . | sed 's/^"//;s/"$//'
    else
        python3 -c "import json,sys; print(json.dumps(sys.stdin.read())[1:-1])" <<< "$1"
    fi
}
json_encode "$1"
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(script)
            f.flush()
            os.chmod(f.name, 0o755)
            yield f.name
        os.unlink(f.name)

    def test_empty_string(self, json_encode_script):
        """Test encoding an empty string."""
        result = subprocess.run(
            [json_encode_script, ""],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_unicode_characters(self, json_encode_script):
        """Test encoding unicode characters."""
        result = subprocess.run(
            [json_encode_script, "Hello 世界 🌍"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        # Unicode should pass through (jq handles unicode correctly)
        assert "Hello" in result.stdout

    def test_control_characters(self, json_encode_script):
        """Test encoding control characters like carriage return."""
        result = subprocess.run(
            [json_encode_script, "line1\r\nline2"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        # \r should be escaped as \r
        assert r"\r" in result.stdout or r"\n" in result.stdout

    def test_mixed_special_characters(self, json_encode_script):
        """Test encoding a complex string with multiple special chars."""
        test_input = 'Path: C:\\Users\\test\nMessage: "Hello"\tTab here'
        result = subprocess.run(
            [json_encode_script, test_input],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        encoded = result.stdout.strip()
        # All special chars should be escaped
        assert r'\\' in encoded  # backslashes
        assert r'\n' in encoded  # newline
        assert r'\"' in encoded  # quotes
        assert r'\t' in encoded  # tab

    def test_json_injection_attempt(self, json_encode_script):
        """Test that JSON injection attempts are safely escaped."""
        # Attempt to break out of JSON string
        malicious = '", "injected": "value'
        result = subprocess.run(
            [json_encode_script, malicious],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        encoded = result.stdout.strip()
        # Quotes should be escaped, preventing injection
        assert r'\"' in encoded
        # The result should be a safe string that won't break JSON parsing
        assert 'injected' in encoded  # content preserved

    def test_multiline_code_block(self, json_encode_script):
        """Test encoding a multi-line code block with various chars."""
        code = '''def hello():
    print("Hello, World!")
    return {"key": "value"}'''
        result = subprocess.run(
            [json_encode_script, code],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        encoded = result.stdout.strip()
        assert r'\n' in encoded  # newlines escaped
        assert r'\"' in encoded  # quotes escaped

    def test_only_special_characters(self, json_encode_script):
        """Test encoding a string of only special characters."""
        result = subprocess.run(
            [json_encode_script, '"\n\t\\'],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        encoded = result.stdout.strip()
        assert r'\"' in encoded
        assert r'\n' in encoded
        assert r'\t' in encoded
        assert r'\\' in encoded
