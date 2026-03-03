"""Tests for CLI script deployment and JSON encoding logic.

These tests verify that:
1. CLI scripts are deployed correctly with proper permissions
2. The json_encode helper function works correctly
3. Deployed scripts contain expected functionality
"""

import os
import subprocess
import tempfile

import pytest

from orchestrator.agents import (
    BRAIN_SCRIPT_NAMES,
    WORKER_SCRIPT_NAMES,
    deploy_brain_scripts,
    deploy_worker_scripts,
    generate_brain_hooks,
    generate_worker_hooks,
)


def _read_script(bin_dir: str, script_name: str) -> str:
    """Read content of a deployed script."""
    with open(os.path.join(bin_dir, script_name)) as f:
        return f.read()


class TestBrainCliScripts:
    """Tests for brain CLI script deployment."""

    def test_deploy_brain_scripts_creates_files(self):
        """Verify all expected scripts are created with correct permissions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_brain_scripts(tmpdir, "http://localhost:8093")

            # Check all expected scripts plus lib.sh
            for script_name in BRAIN_SCRIPT_NAMES + ["lib.sh"]:
                script_path = os.path.join(bin_dir, script_name)
                assert os.path.exists(script_path), f"{script_name} should exist"
                assert os.access(script_path, os.X_OK), f"{script_name} should be executable"

    def test_brain_lib_has_json_encode(self):
        """Verify the json_encode helper is included in lib.sh."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_brain_scripts(tmpdir)
            lib_content = _read_script(bin_dir, "lib.sh")
            assert "json_encode()" in lib_content
            assert "jq -Rs" in lib_content

    def test_brain_ctx_script_has_content_stdin(self):
        """Verify --content-stdin option is supported."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_brain_scripts(tmpdir)
            content = _read_script(bin_dir, "orch-ctx")
            assert "--content-stdin" in content
            assert "content_stdin" in content

    def test_brain_tasks_script_has_delete_command(self):
        """Verify orch-tasks has delete command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_brain_scripts(tmpdir)
            content = _read_script(bin_dir, "orch-tasks")
            assert "delete <id>" in content
            assert "cmd_delete()" in content

    def test_brain_workers_script_has_stop_command(self):
        """Verify orch-workers has stop command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_brain_scripts(tmpdir)
            content = _read_script(bin_dir, "orch-workers")
            assert "stop <id>" in content
            assert "cmd_stop()" in content

    def test_brain_workers_script_has_reconnect_command(self):
        """Verify orch-workers has reconnect command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_brain_scripts(tmpdir)
            content = _read_script(bin_dir, "orch-workers")
            assert "reconnect <id>" in content
            assert "cmd_reconnect()" in content

    def test_brain_skills_script_has_all_commands(self):
        """Verify orch-skills has list, show, create, update, delete commands."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_brain_scripts(tmpdir)
            content = _read_script(bin_dir, "orch-skills")
            assert "cmd_list()" in content
            assert "cmd_show()" in content
            assert "cmd_create()" in content
            assert "cmd_update()" in content
            assert "cmd_delete()" in content

    def test_brain_skills_script_has_content_stdin(self):
        """Verify orch-skills supports --content-stdin option."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_brain_scripts(tmpdir)
            content = _read_script(bin_dir, "orch-skills")
            assert "--content-stdin" in content
            assert "content_stdin" in content

    def test_brain_skills_script_has_enabled_flag(self):
        """Verify orch-skills supports --enabled flag for update."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_brain_scripts(tmpdir)
            content = _read_script(bin_dir, "orch-skills")
            assert "--enabled" in content
            assert "enabled" in content
            assert "$enabled" in content

    def test_brain_skills_script_has_target_filter(self):
        """Verify orch-skills list supports --target filter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_brain_scripts(tmpdir)
            content = _read_script(bin_dir, "orch-skills")
            assert "--target" in content
            assert "target=$target" in content

    def test_brain_skills_script_has_search_filter(self):
        """Verify orch-skills list supports --search filter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_brain_scripts(tmpdir)
            content = _read_script(bin_dir, "orch-skills")
            assert "--search" in content
            assert "search=$search" in content

    def test_brain_script_names_includes_orch_skills(self):
        """Verify orch-skills is in BRAIN_SCRIPT_NAMES."""
        assert "orch-skills" in BRAIN_SCRIPT_NAMES


class TestWorkerCliScripts:
    """Tests for worker CLI script deployment."""

    def test_deploy_worker_scripts_creates_files(self):
        """Verify all expected worker scripts are created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123", "http://localhost:8093")

            # Check all expected scripts plus lib.sh
            for script_name in WORKER_SCRIPT_NAMES + ["lib.sh"]:
                script_path = os.path.join(bin_dir, script_name)
                assert os.path.exists(script_path), f"{script_name} should exist"
                assert os.access(script_path, os.X_OK), f"{script_name} should be executable"

    def test_worker_lib_has_json_encode(self):
        """Verify the json_encode helper is in lib.sh."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123")
            lib_content = _read_script(bin_dir, "lib.sh")
            assert "json_encode()" in lib_content
            assert "jq -Rs" in lib_content

    def test_worker_lib_has_session_id(self):
        """Verify lib.sh contains the session ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "test-session-456")
            lib_content = _read_script(bin_dir, "lib.sh")
            assert "test-session-456" in lib_content

    def test_task_script_has_notes_stdin(self):
        """Verify --notes-stdin option is supported in task script."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123")
            content = _read_script(bin_dir, "orch-task")
            assert "--notes-stdin" in content
            assert "notes_stdin" in content

    def test_subtask_script_has_description_stdin(self):
        """Verify --description-stdin option is supported in subtask script."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123")
            content = _read_script(bin_dir, "orch-subtask")
            assert "--description-stdin" in content
            assert "description_stdin" in content

    def test_context_script_has_delete_command(self):
        """Verify orch-context has delete command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123")
            content = _read_script(bin_dir, "orch-context")
            assert "cmd_delete()" in content
            assert "delete ID" in content

    def test_context_script_has_update_command(self):
        """Verify orch-context has update command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123")
            content = _read_script(bin_dir, "orch-context")
            assert "cmd_update()" in content

    def test_context_script_has_project_scope_guard(self):
        """Verify orch-context update/delete enforce project scope."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123")
            content = _read_script(bin_dir, "orch-context")
            assert "verify_project_scope" in content
            assert "Workers can only modify project-scoped items" in content
            assert "belongs to a different project" in content


@pytest.mark.allow_subprocess
class TestJsonEncodeFunction:
    """Tests for the json_encode bash function via actual execution."""

    @pytest.fixture
    def json_encode_script(self):
        """Create a minimal script that exposes json_encode for testing."""
        script = """#!/bin/bash
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
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
            f.write(script)
            f.flush()
            os.chmod(f.name, 0o755)
            yield f.name
        os.unlink(f.name)

    def test_json_encode_simple_string(self, json_encode_script):
        """Test encoding a simple string without special characters."""
        result = subprocess.run([json_encode_script, "hello world"], capture_output=True, text=True)
        assert result.returncode == 0
        assert result.stdout.strip() == "hello world"

    def test_json_encode_with_quotes(self, json_encode_script):
        """Test encoding a string with double quotes."""
        result = subprocess.run(
            [json_encode_script, 'hello "world"'], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert result.stdout.strip() == r"hello \"world\""

    def test_json_encode_with_newlines(self, json_encode_script):
        """Test encoding a string with newlines."""
        result = subprocess.run(
            [json_encode_script, "line1\nline2\nline3"], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert result.stdout.strip() == r"line1\nline2\nline3"

    def test_json_encode_with_backslashes(self, json_encode_script):
        """Test encoding a string with backslashes."""
        result = subprocess.run(
            [json_encode_script, r"path\to\file"], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert result.stdout.strip() == r"path\\to\\file"

    def test_json_encode_with_tabs(self, json_encode_script):
        """Test encoding a string with tab characters."""
        result = subprocess.run(
            [json_encode_script, "col1\tcol2\tcol3"], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert result.stdout.strip() == r"col1\tcol2\tcol3"

    def test_json_encode_markdown_content(self, json_encode_script):
        """Test encoding typical markdown content with special chars."""
        markdown = """# Heading

This has:
- `backticks`
- "quotes"
- backslash \\
- newlines"""
        result = subprocess.run([json_encode_script, markdown], capture_output=True, text=True)
        assert result.returncode == 0
        # Verify key escapes are present
        encoded = result.stdout.strip()
        assert r"\n" in encoded  # newlines escaped
        assert r"\"" in encoded  # quotes escaped
        assert r"\\" in encoded  # backslashes escaped


class TestStdinOptions:
    """Tests for --*-stdin options in deployed scripts."""

    def test_content_stdin_reads_from_stdin(self):
        """Verify --content-stdin triggers reading from cat."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_brain_scripts(tmpdir)
            content = _read_script(bin_dir, "orch-ctx")
            assert 'if [[ -n "$content_stdin" ]]; then' in content
            assert "content=$(cat)" in content

    def test_content_file_reads_from_file(self):
        """Verify --content-file reads content from specified file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_brain_scripts(tmpdir)
            content = _read_script(bin_dir, "orch-ctx")
            assert 'if [[ -n "$content_file" ]]; then' in content
            assert 'content=$(cat "$content_file")' in content


class TestCrossPlatformCompatibility:
    """Tests verifying cross-platform (macOS/Linux) compatibility."""

    def test_no_gnu_sed_syntax_in_brain_scripts(self):
        """Ensure no GNU-specific sed multi-line syntax is used."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_brain_scripts(tmpdir)
            for script_name in BRAIN_SCRIPT_NAMES + ["lib.sh"]:
                content = _read_script(bin_dir, script_name)
                # The problematic pattern was: sed ':a;N;$!ba;s/\n/\\n/g'
                assert ":a;N;$!ba" not in content, f"GNU sed syntax found in {script_name}"

    def test_no_gnu_sed_syntax_in_worker_scripts(self):
        """Ensure no GNU-specific sed syntax in worker scripts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123")
            for script_name in WORKER_SCRIPT_NAMES + ["lib.sh"]:
                content = _read_script(bin_dir, script_name)
                assert ":a;N;$!ba" not in content, f"GNU sed syntax found in {script_name}"

    def test_uses_portable_sed_for_quote_stripping(self):
        """Verify sed command for stripping quotes is POSIX-compatible."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123")
            lib_content = _read_script(bin_dir, "lib.sh")
            # The portable pattern: sed 's/^"//;s/"$//'
            assert "sed 's/^\"//;s/\"$//'" in lib_content


class TestJsonEscapingInScripts:
    """Tests verifying CLI scripts properly escape JSON strings."""

    def test_brain_scripts_escape_fields(self):
        """Verify brain scripts use json_encode for text fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_brain_scripts(tmpdir)

            # orch-workers
            workers = _read_script(bin_dir, "orch-workers")
            assert 'escaped_name=$(json_encode "$name")' in workers

            # orch-projects
            projects = _read_script(bin_dir, "orch-projects")
            assert 'escaped_name=$(json_encode "$name")' in projects

            # orch-tasks
            tasks = _read_script(bin_dir, "orch-tasks")
            assert 'escaped_title=$(json_encode "$title")' in tasks

            # orch-ctx
            ctx = _read_script(bin_dir, "orch-ctx")
            assert 'escaped_content=$(json_encode "$content")' in ctx

            # orch-skills
            skills = _read_script(bin_dir, "orch-skills")
            assert 'escaped_name=$(json_encode "$name")' in skills
            assert 'escaped_content=$(json_encode "$content")' in skills

    def test_worker_scripts_escape_fields(self):
        """Verify worker scripts use json_encode for text fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123")

            # orch-task
            task = _read_script(bin_dir, "orch-task")
            assert 'escaped_notes=$(json_encode "$notes")' in task

            # orch-subtask
            subtask = _read_script(bin_dir, "orch-subtask")
            assert 'escaped_title=$(json_encode "$title")' in subtask


@pytest.mark.allow_subprocess
class TestJsonEncodeEdgeCases:
    """Integration tests for json_encode handling various edge cases."""

    @pytest.fixture
    def json_encode_script(self):
        """Create a minimal script that exposes json_encode for testing."""
        script = """#!/bin/bash
json_encode() {
    if command -v jq &> /dev/null; then
        printf '%s' "$1" | jq -Rs . | sed 's/^"//;s/"$//'
    else
        python3 -c "import json,sys; print(json.dumps(sys.stdin.read())[1:-1])" <<< "$1"
    fi
}
json_encode "$1"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
            f.write(script)
            f.flush()
            os.chmod(f.name, 0o755)
            yield f.name
        os.unlink(f.name)

    def test_empty_string(self, json_encode_script):
        """Test encoding an empty string."""
        result = subprocess.run([json_encode_script, ""], capture_output=True, text=True)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_unicode_characters(self, json_encode_script):
        """Test encoding unicode characters."""
        result = subprocess.run(
            [json_encode_script, "Hello 世界 🌍"], capture_output=True, text=True
        )
        assert result.returncode == 0
        # Unicode should pass through (jq handles unicode correctly)
        assert "Hello" in result.stdout

    def test_control_characters(self, json_encode_script):
        """Test encoding control characters like carriage return."""
        result = subprocess.run(
            [json_encode_script, "line1\r\nline2"], capture_output=True, text=True
        )
        assert result.returncode == 0
        # \r should be escaped as \r
        assert r"\r" in result.stdout or r"\n" in result.stdout

    def test_mixed_special_characters(self, json_encode_script):
        """Test encoding a complex string with multiple special chars."""
        test_input = 'Path: C:\\Users\\test\nMessage: "Hello"\tTab here'
        result = subprocess.run([json_encode_script, test_input], capture_output=True, text=True)
        assert result.returncode == 0
        encoded = result.stdout.strip()
        # All special chars should be escaped
        assert r"\\" in encoded  # backslashes
        assert r"\n" in encoded  # newline
        assert r"\"" in encoded  # quotes
        assert r"\t" in encoded  # tab

    def test_json_injection_attempt(self, json_encode_script):
        """Test that JSON injection attempts are safely escaped."""
        # Attempt to break out of JSON string
        malicious = '", "injected": "value'
        result = subprocess.run([json_encode_script, malicious], capture_output=True, text=True)
        assert result.returncode == 0
        encoded = result.stdout.strip()
        # Quotes should be escaped, preventing injection
        assert r"\"" in encoded
        # The result should be a safe string that won't break JSON parsing
        assert "injected" in encoded  # content preserved

    def test_multiline_code_block(self, json_encode_script):
        """Test encoding a multi-line code block with various chars."""
        code = """def hello():
    print("Hello, World!")
    return {"key": "value"}"""
        result = subprocess.run([json_encode_script, code], capture_output=True, text=True)
        assert result.returncode == 0
        encoded = result.stdout.strip()
        assert r"\n" in encoded  # newlines escaped
        assert r"\"" in encoded  # quotes escaped

    def test_only_special_characters(self, json_encode_script):
        """Test encoding a string of only special characters."""
        result = subprocess.run([json_encode_script, '"\n\t\\'], capture_output=True, text=True)
        assert result.returncode == 0
        encoded = result.stdout.strip()
        assert r"\"" in encoded
        assert r"\n" in encoded
        assert r"\t" in encoded
        assert r"\\" in encoded


class TestWorkerLibCdpPortAndHeadless:
    """Tests for ORCH_CDP_PORT and ORCH_BROWSER_HEADLESS in deployed lib.sh."""

    def test_default_cdp_port_in_lib(self):
        """Verify lib.sh contains default CDP port 9222."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123")
            lib_content = _read_script(bin_dir, "lib.sh")
            assert "ORCH_CDP_PORT" in lib_content
            assert "9222" in lib_content

    def test_custom_cdp_port_in_lib(self):
        """Verify lib.sh reflects a custom CDP port."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123", cdp_port=9333)
            lib_content = _read_script(bin_dir, "lib.sh")
            assert ":-9333" in lib_content

    def test_default_browser_headless_true(self):
        """Verify lib.sh defaults to headless=true."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123")
            lib_content = _read_script(bin_dir, "lib.sh")
            assert "ORCH_BROWSER_HEADLESS" in lib_content
            assert ":-true" in lib_content

    def test_browser_headless_false_for_local(self):
        """Verify lib.sh sets headless=false when browser_headless=False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123", browser_headless=False)
            lib_content = _read_script(bin_dir, "lib.sh")
            assert ":-false" in lib_content

    def test_cdp_port_and_headless_together(self):
        """Verify both params are written correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(
                tmpdir, "session-456", cdp_port=9444, browser_headless=False
            )
            lib_content = _read_script(bin_dir, "lib.sh")
            assert "ORCH_CDP_PORT" in lib_content
            assert ":-9444" in lib_content
            assert "ORCH_BROWSER_HEADLESS" in lib_content
            assert ":-false" in lib_content


class TestOrchBrowserScript:
    """Tests for orch-browser CLI script."""

    def test_orch_browser_in_worker_script_names(self):
        """Verify orch-browser is included in WORKER_SCRIPT_NAMES."""
        assert "orch-browser" in WORKER_SCRIPT_NAMES

    def test_orch_browser_script_has_start_command(self):
        """Verify orch-browser has --start command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123")
            content = _read_script(bin_dir, "orch-browser")
            assert "--start" in content
            assert "do_start" in content

    def test_orch_browser_script_has_close_command(self):
        """Verify orch-browser has --close command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123")
            content = _read_script(bin_dir, "orch-browser")
            assert "--close" in content
            assert "do_close" in content

    def test_orch_browser_script_has_status_command(self):
        """Verify orch-browser has --status command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123")
            content = _read_script(bin_dir, "orch-browser")
            assert "--status" in content
            assert "do_status" in content

    def test_orch_browser_script_has_minimize_restore(self):
        """Verify orch-browser has --minimize and --restore commands."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123")
            content = _read_script(bin_dir, "orch-browser")
            assert "--minimize" in content
            assert "--restore" in content
            assert "do_minimize" in content
            assert "do_restore" in content

    def test_orch_browser_reads_cdp_port_from_env(self):
        """Verify orch-browser reads DEFAULT_PORT from ORCH_CDP_PORT env var."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123")
            content = _read_script(bin_dir, "orch-browser")
            assert "ORCH_CDP_PORT" in content
            assert 'DEFAULT_PORT="${ORCH_CDP_PORT:-9222}"' in content

    def test_orch_browser_respects_headless_env(self):
        """Verify orch-browser uses ORCH_BROWSER_HEADLESS for headed/headless mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123")
            content = _read_script(bin_dir, "orch-browser")
            assert "ORCH_BROWSER_HEADLESS" in content
            assert "--window-size=1280,960" in content

    def test_orch_browser_script_no_runtime_mcp_commands(self):
        """Verify orch-browser does not use claude mcp add/remove (config is pre-deployed)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = deploy_worker_scripts(tmpdir, "session-123")
            content = _read_script(bin_dir, "orch-browser")
            assert "claude mcp add" not in content
            assert "claude mcp remove" not in content

    def test_orch_browser_permission_in_settings(self):
        """Verify orch-browser permission is in worker settings.json."""
        import json

        settings_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "agents",
            "worker",
            "settings.json",
        )
        with open(settings_path) as f:
            settings = json.load(f)

        permissions = settings["permissions"]["allow"]
        assert "Bash(orch-browser *)" in permissions


class TestWorkerHooksGeneration:
    """Tests for worker hooks and settings.json generation from templates."""

    def test_generate_worker_hooks_creates_settings_json(self):
        """Verify settings.json is created with correct content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            configs_dir = os.path.join(tmpdir, "configs")
            os.makedirs(configs_dir)
            generate_worker_hooks(configs_dir, "test-session-123", "http://localhost:8093")

            settings_path = os.path.join(configs_dir, "settings.json")
            assert os.path.exists(settings_path), "settings.json should exist"

            with open(settings_path) as f:
                content = f.read()

            # Verify key settings from template
            assert '"CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION": "false"' in content
            assert '"spinnerTipsEnabled": false' in content
            assert '"terminalProgressBarEnabled": false' in content

    def test_generate_worker_hooks_creates_hook_script(self):
        """Verify update-status.sh hook script is created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            configs_dir = os.path.join(tmpdir, "configs")
            os.makedirs(configs_dir)
            generate_worker_hooks(configs_dir, "test-session-456", "http://localhost:9000")

            hook_path = os.path.join(configs_dir, "hooks", "update-status.sh")
            assert os.path.exists(hook_path), "update-status.sh should exist"
            assert os.access(hook_path, os.X_OK), "update-status.sh should be executable"

    def test_generate_worker_hooks_substitutes_session_id(self):
        """Verify session ID placeholder is substituted in hook script."""
        with tempfile.TemporaryDirectory() as tmpdir:
            configs_dir = os.path.join(tmpdir, "configs")
            os.makedirs(configs_dir)
            generate_worker_hooks(configs_dir, "my-unique-session-789", "http://localhost:8093")

            hook_path = os.path.join(configs_dir, "hooks", "update-status.sh")
            with open(hook_path) as f:
                content = f.read()

            assert "my-unique-session-789" in content
            assert "{{SESSION_ID}}" not in content, "Placeholder should be substituted"

    def test_generate_worker_hooks_substitutes_api_base(self):
        """Verify API base placeholder is substituted in hook script."""
        with tempfile.TemporaryDirectory() as tmpdir:
            configs_dir = os.path.join(tmpdir, "configs")
            os.makedirs(configs_dir)
            generate_worker_hooks(configs_dir, "session-123", "http://custom-host:9999")

            hook_path = os.path.join(configs_dir, "hooks", "update-status.sh")
            with open(hook_path) as f:
                content = f.read()

            assert "http://custom-host:9999" in content
            assert "{{API_BASE}}" not in content, "Placeholder should be substituted"

    def test_generate_worker_hooks_substitutes_hook_path_in_settings(self):
        """Verify hook script path is substituted in settings.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            configs_dir = os.path.join(tmpdir, "configs")
            os.makedirs(configs_dir)
            generate_worker_hooks(configs_dir, "session-123", "http://localhost:8093")

            settings_path = os.path.join(configs_dir, "settings.json")
            with open(settings_path) as f:
                content = f.read()

            # The hook path should be the actual path, not the placeholder
            expected_hook_path = os.path.join(configs_dir, "hooks", "update-status.sh")
            assert expected_hook_path in content
            assert "{{HOOK_SCRIPT_PATH}}" not in content, "Placeholder should be substituted"

    def test_generate_worker_hooks_deploys_safety_hook(self):
        """Verify check-command.sh safety hook is deployed and executable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            configs_dir = os.path.join(tmpdir, "configs")
            os.makedirs(configs_dir)
            generate_worker_hooks(configs_dir, "session-123", "http://localhost:8093")

            safety_path = os.path.join(configs_dir, "hooks", "check-command.sh")
            assert os.path.exists(safety_path), "check-command.sh should be deployed"
            assert os.access(safety_path, os.X_OK), "check-command.sh should be executable"

    def test_generate_worker_hooks_substitutes_safety_hook_path_in_settings(self):
        """Verify safety hook path is substituted in settings.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            configs_dir = os.path.join(tmpdir, "configs")
            os.makedirs(configs_dir)
            generate_worker_hooks(configs_dir, "session-123", "http://localhost:8093")

            settings_path = os.path.join(configs_dir, "settings.json")
            with open(settings_path) as f:
                content = f.read()

            expected_safety_path = os.path.join(configs_dir, "hooks", "check-command.sh")
            assert expected_safety_path in content
            assert "{{SAFETY_HOOK_PATH}}" not in content, "Placeholder should be substituted"


class TestBrainHooksGeneration:
    """Tests for brain hook deployment."""

    def test_generate_brain_hooks_deploys_safety_hook(self):
        """Verify check-command.sh safety hook is deployed and executable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            generate_brain_hooks(tmpdir)

            safety_path = os.path.join(tmpdir, "hooks", "check-command.sh")
            assert os.path.exists(safety_path), "check-command.sh should be deployed"
            assert os.access(safety_path, os.X_OK), "check-command.sh should be executable"

    def test_generate_brain_hooks_deploys_inject_focus(self):
        """Verify inject-focus.sh hook is deployed and executable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            generate_brain_hooks(tmpdir)

            hook_path = os.path.join(tmpdir, "hooks", "inject-focus.sh")
            assert os.path.exists(hook_path), "inject-focus.sh should be deployed"
            assert os.access(hook_path, os.X_OK), "inject-focus.sh should be executable"

    def test_generate_brain_hooks_substitutes_placeholders(self):
        """Verify all placeholders are substituted in settings.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = generate_brain_hooks(tmpdir)

            with open(settings_path) as f:
                content = f.read()

            assert "{{SAFETY_HOOK_PATH}}" not in content
            assert "{{INJECT_FOCUS_PATH}}" not in content
            assert os.path.join(tmpdir, "hooks", "check-command.sh") in content
            assert os.path.join(tmpdir, "hooks", "inject-focus.sh") in content


class TestWorkerHooksOverwrite:
    """Tests for worker hook overwrite behavior."""

    def test_generate_worker_hooks_overwrites_existing(self):
        """Verify regeneration overwrites existing files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            configs_dir = os.path.join(tmpdir, "configs")
            os.makedirs(configs_dir)

            # First generation
            generate_worker_hooks(configs_dir, "old-session", "http://localhost:8093")

            # Second generation with different session ID
            generate_worker_hooks(configs_dir, "new-session", "http://localhost:8093")

            hook_path = os.path.join(configs_dir, "hooks", "update-status.sh")
            with open(hook_path) as f:
                content = f.read()

            assert "new-session" in content
            assert "old-session" not in content, "Old session ID should be overwritten"
