"""Deploy agent CLI scripts and prompts.

This module handles copying static scripts from agents/ to worker/brain tmp directories
and generating dynamic configuration (hooks, settings) with session-specific values.
"""

import json
import os
import shutil
import stat

# Path to the agents directory (relative to this file)
_AGENTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agents"))

# Script names for iteration
WORKER_SCRIPT_NAMES = ["orch-task", "orch-subtask", "orch-worker", "orch-context", "orch-notify", "orch-tunnel"]
BRAIN_SCRIPT_NAMES = ["orch-workers", "orch-projects", "orch-tasks", "orch-ctx", "orch-send", "orch-notifications", "orch-tunnel"]


def get_path_export_command(bin_dir: str) -> str:
    """Get the shell command to add bin_dir to PATH."""
    return f'export PATH="{bin_dir}:$PATH"'


def deploy_worker_scripts(
    worker_dir: str,
    session_id: str,
    api_base: str = "http://127.0.0.1:8093",
) -> str:
    """Deploy worker CLI scripts to the worker's bin directory.
    
    Copies static scripts from agents/worker/bin/ and creates lib.sh with
    environment variable defaults.
    
    Args:
        worker_dir: Base directory for the worker (e.g., /tmp/orchestrator/workers/worker1)
        session_id: Worker's session ID
        api_base: API base URL
        
    Returns:
        Path to the bin directory containing the scripts
    """
    bin_dir = os.path.join(worker_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    
    # Copy lib.sh with environment variable values injected
    lib_content = f'''#!/bin/bash
# Worker CLI library - shared functions for worker scripts
# Auto-generated with session-specific defaults

# Environment variables (can be overridden)
export ORCH_SESSION_ID="${{ORCH_SESSION_ID:-{session_id}}}"
export ORCH_API_BASE="${{ORCH_API_BASE:-{api_base}}}"
export ORCH_WORKER_DIR="${{ORCH_WORKER_DIR:-{worker_dir}}}"

# Aliases for cleaner script code
SESSION_ID="$ORCH_SESSION_ID"
API_BASE="$ORCH_API_BASE"
WORKER_DIR="$ORCH_WORKER_DIR"
CACHE_FILE="$WORKER_DIR/.task_cache"
CACHE_TTL=300  # 5 minutes in seconds

# Get file modification time (cross-platform: macOS and Linux)
get_file_mtime() {{
    local file="$1"
    if [[ "$(uname)" == "Darwin" ]]; then
        stat -f %m "$file" 2>/dev/null
    else
        stat -c %Y "$file" 2>/dev/null
    fi
}}

# Load task info from cache or API
load_task_info() {{
    local force_refresh="$1"
    local cache_valid=false
    
    if [[ -f "$CACHE_FILE" && "$force_refresh" != "true" ]]; then
        local file_mtime=$(get_file_mtime "$CACHE_FILE")
        if [[ -n "$file_mtime" ]]; then
            local cache_age=$(($(date +%s) - file_mtime))
            if [[ $cache_age -lt $CACHE_TTL ]]; then
                cache_valid=true
            fi
        fi
    fi
    
    if [[ "$cache_valid" == "true" ]]; then
        source "$CACHE_FILE"
    else
        local http_code
        local tasks_json
        tasks_json=$(curl -s -w "\\n%{{http_code}}" --connect-timeout 5 "$API_BASE/api/tasks?assigned_session_id=$SESSION_ID")
        http_code=$(echo "$tasks_json" | tail -n1)
        tasks_json=$(echo "$tasks_json" | sed '$d')
        
        if [[ "$http_code" == "000" || -z "$http_code" ]]; then
            echo "Error: Connection failed - cannot reach orchestrator API at $API_BASE" >&2
            return 1
        fi
        
        if [[ "$http_code" != "200" ]]; then
            echo "Error: API request failed with HTTP $http_code" >&2
            return 1
        fi
        
        TASK_ID=$(echo "$tasks_json" | jq -r '.[0].id // empty')
        
        if [[ -z "$TASK_ID" || "$TASK_ID" == "null" ]]; then
            echo "Error: No task assigned to this worker" >&2
            return 1
        fi
        
        PROJECT_ID=$(echo "$tasks_json" | jq -r '.[0].project_id // empty')
        
        mkdir -p "$WORKER_DIR"
        cat > "$CACHE_FILE" << CACHEEOF
TASK_ID="$TASK_ID"
PROJECT_ID="$PROJECT_ID"
CACHEEOF
    fi
    
    return 0
}}

# Helper: JSON-encode a string
json_encode() {{
    if command -v jq &> /dev/null; then
        printf '%s' "$1" | jq -Rs . | sed 's/^"//;s/"$//'
    else
        python3 -c "import json,sys; print(json.dumps(sys.stdin.read())[1:-1])" <<< "$1"
    fi
}}

# Check for --refresh flag
FORCE_REFRESH="false"
FILTERED_ARGS=()
for arg in "$@"; do
    if [[ "$arg" == "--refresh" ]]; then
        FORCE_REFRESH="true"
    else
        FILTERED_ARGS+=("$arg")
    fi
done
set -- "${{FILTERED_ARGS[@]}}"
'''
    
    lib_path = os.path.join(bin_dir, "lib.sh")
    with open(lib_path, "w") as f:
        f.write(lib_content)
    os.chmod(lib_path, os.stat(lib_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    
    # Copy static scripts from agents/worker/bin/
    src_bin_dir = os.path.join(_AGENTS_DIR, "worker", "bin")
    for script_name in WORKER_SCRIPT_NAMES:
        src_path = os.path.join(src_bin_dir, script_name)
        dst_path = os.path.join(bin_dir, script_name)
        if os.path.exists(src_path):
            shutil.copy2(src_path, dst_path)
            os.chmod(dst_path, os.stat(dst_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    
    return bin_dir


def deploy_brain_scripts(
    brain_dir: str,
    api_base: str = "http://127.0.0.1:8093",
) -> str:
    """Deploy brain CLI scripts to the brain's bin directory.
    
    Args:
        brain_dir: Base directory for brain scripts (e.g., /tmp/orchestrator/brain)
        api_base: API base URL
        
    Returns:
        Path to the bin directory containing the scripts
    """
    bin_dir = os.path.join(brain_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    
    # Create lib.sh with environment variable defaults
    lib_content = f'''#!/bin/bash
# Brain CLI library - shared functions for brain scripts
# Auto-generated with session-specific defaults

# Environment variables (can be overridden)
export ORCH_API_BASE="${{ORCH_API_BASE:-{api_base}}}"

# Alias for cleaner script code
API_BASE="$ORCH_API_BASE"

# Helper to pretty-print JSON
pp() {{
    if command -v jq &> /dev/null; then
        jq .
    else
        cat
    fi
}}

# Helper: JSON-encode a string
json_encode() {{
    if command -v jq &> /dev/null; then
        printf '%s' "$1" | jq -Rs . | sed 's/^"//;s/"$//'
    else
        python3 -c "import json,sys; print(json.dumps(sys.stdin.read())[1:-1])" <<< "$1"
    fi
}}

# Helper to build JSON payload
build_json() {{
    local json="{{"
    local first=true
    for arg in "$@"; do
        local key="${{arg%%=*}}"
        local value="${{arg#*=}}"
        if [[ "$first" != true ]]; then
            json="$json,"
        fi
        if [[ "$value" =~ ^[0-9]+$ ]] || [[ "$value" == "true" ]] || [[ "$value" == "false" ]] || [[ "$value" == "null" ]]; then
            json="$json\\"$key\\": $value"
        else
            local escaped_value=$(json_encode "$value")
            json="$json\\"$key\\": \\"$escaped_value\\""
        fi
        first=false
    done
    json="$json}}"
    echo "$json"
}}
'''
    
    lib_path = os.path.join(bin_dir, "lib.sh")
    with open(lib_path, "w") as f:
        f.write(lib_content)
    os.chmod(lib_path, os.stat(lib_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    
    # Copy static scripts from agents/brain/bin/
    src_bin_dir = os.path.join(_AGENTS_DIR, "brain", "bin")
    for script_name in BRAIN_SCRIPT_NAMES:
        src_path = os.path.join(src_bin_dir, script_name)
        dst_path = os.path.join(bin_dir, script_name)
        if os.path.exists(src_path):
            shutil.copy2(src_path, dst_path)
            os.chmod(dst_path, os.stat(dst_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    
    return bin_dir


def get_worker_prompt(session_id: str) -> str | None:
    """Load and render worker prompt template.
    
    Args:
        session_id: Worker's session ID (for placeholder replacement)
        
    Returns:
        Rendered prompt string, or None if template not found
    """
    template_path = os.path.join(_AGENTS_DIR, "worker", "prompt.md")
    if not os.path.exists(template_path):
        return None
    
    with open(template_path) as f:
        template = f.read()
    
    return template.replace("SESSION_ID", session_id)


def get_brain_prompt() -> str | None:
    """Load brain prompt.
    
    Returns:
        Prompt string, or None if not found
    """
    prompt_path = os.path.join(_AGENTS_DIR, "brain", "prompt.md")
    if not os.path.exists(prompt_path):
        return None
    
    with open(prompt_path) as f:
        return f.read()


def get_brain_skills_dir() -> str | None:
    """Get path to brain skills directory.
    
    Returns:
        Path to skills directory, or None if not found
    """
    skills_dir = os.path.join(_AGENTS_DIR, "brain", "skills")
    if os.path.isdir(skills_dir):
        return skills_dir
    return None


def get_worker_skills_dir() -> str | None:
    """Get path to worker skills directory.
    
    Returns:
        Path to skills directory, or None if not found
    """
    skills_dir = os.path.join(_AGENTS_DIR, "worker", "skills")
    if os.path.isdir(skills_dir):
        return skills_dir
    return None


def generate_worker_hooks(
    worker_dir: str,
    session_id: str,
    api_base: str = "http://127.0.0.1:8093",
) -> str:
    """Generate Claude Code hooks settings.json for automatic status management.
    
    Args:
        worker_dir: Directory to generate hooks in (e.g., tmp_dir/configs/)
        session_id: Worker's session ID
        api_base: API base URL
        
    Returns:
        Path to the directory containing settings.json
    """
    hooks_dir = os.path.join(worker_dir, "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    
    # Copy hook script template and substitute placeholders
    src_hook_path = os.path.join(_AGENTS_DIR, "worker", "hooks", "update-status.sh")
    hook_script_path = os.path.join(hooks_dir, "update-status.sh")
    
    with open(src_hook_path) as f:
        hook_content = f.read()
    
    hook_content = hook_content.replace("{{SESSION_ID}}", session_id)
    hook_content = hook_content.replace("{{API_BASE}}", api_base)
    
    with open(hook_script_path, "w") as f:
        f.write(hook_content)
    os.chmod(hook_script_path, os.stat(hook_script_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    
    # Copy settings.json template and substitute placeholders
    src_settings_path = os.path.join(_AGENTS_DIR, "worker", "settings.json")
    dst_settings_path = os.path.join(worker_dir, "settings.json")
    
    with open(src_settings_path) as f:
        settings_content = f.read()
    
    settings_content = settings_content.replace("{{HOOK_SCRIPT_PATH}}", hook_script_path)
    
    with open(dst_settings_path, "w") as f:
        f.write(settings_content)
    
    return worker_dir


def generate_brain_hooks(
    brain_dir: str,
    api_base: str = "http://127.0.0.1:8093",
) -> str:
    """Deploy brain hooks and settings.
    
    Args:
        brain_dir: Directory to deploy to (must be /tmp/orchestrator/brain)
        api_base: API base URL
        
    Returns:
        Path to the settings.json file
    """
    # Deploy hook script
    hooks_dir = os.path.join(brain_dir, "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    
    src_hook_path = os.path.join(_AGENTS_DIR, "brain", "hooks", "inject-focus.sh")
    dst_hook_path = os.path.join(hooks_dir, "inject-focus.sh")
    shutil.copy2(src_hook_path, dst_hook_path)
    os.chmod(dst_hook_path, os.stat(dst_hook_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    
    # Copy settings.json template to .claude/settings.json
    claude_dir = os.path.join(brain_dir, ".claude")
    os.makedirs(claude_dir, exist_ok=True)
    
    src_settings_path = os.path.join(_AGENTS_DIR, "brain", "settings.json")
    settings_path = os.path.join(claude_dir, "settings.json")
    shutil.copy2(src_settings_path, settings_path)
    
    return settings_path
