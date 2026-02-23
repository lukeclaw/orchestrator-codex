"""Deploy agent CLI scripts and prompts.

This module handles copying static scripts from agents/ to worker/brain tmp directories
and generating dynamic configuration (hooks, settings) with session-specific values.
"""

import json
import os
import shutil
import stat

from orchestrator import paths

# Path to the agents directory (resolved via paths module for dev/packaged compat)
_AGENTS_DIR = str(paths.agents_dir())
_SHARED_HOOKS_DIR = os.path.join(_AGENTS_DIR, "shared", "hooks")

# Script names for iteration
WORKER_SCRIPT_NAMES = ["orch-task", "orch-subtask", "orch-worker", "orch-context", "orch-notify", "orch-tunnel", "orch-prs"]
BRAIN_SCRIPT_NAMES = ["orch-workers", "orch-projects", "orch-tasks", "orch-ctx", "orch-send", "orch-notifications", "orch-tunnel", "orch-prs"]


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

# Load task info from API
load_task_info() {{
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


def format_custom_skills_for_prompt(skills: list[dict]) -> str:
    """Format custom skills as a markdown list for prompt injection.

    Args:
        skills: List of dicts with 'name' and optional 'description' keys.

    Returns:
        Markdown section string, or empty string if no skills.
    """
    if not skills:
        return ""
    lines = ["\n### Custom Skills\n"]
    for s in skills:
        desc = s.get("description") or "No description"
        lines.append(f"- **`/{s['name']}`** — {desc}")
    return "\n".join(lines)


def get_worker_prompt(session_id: str, custom_skills_section: str = "") -> str | None:
    """Load and render worker prompt template.

    Args:
        session_id: Worker's session ID (for placeholder replacement)
        custom_skills_section: Pre-formatted custom skills text to inject

    Returns:
        Rendered prompt string, or None if template not found
    """
    template_path = os.path.join(_AGENTS_DIR, "worker", "prompt.md")
    if not os.path.exists(template_path):
        return None

    with open(template_path) as f:
        template = f.read()

    result = template.replace("SESSION_ID", session_id)
    result = result.replace("{{CUSTOM_SKILLS}}", custom_skills_section)
    return result


def get_brain_prompt(custom_skills_section: str = "") -> str | None:
    """Load brain prompt.

    Args:
        custom_skills_section: Pre-formatted custom skills text to inject

    Returns:
        Prompt string, or None if not found
    """
    prompt_path = os.path.join(_AGENTS_DIR, "brain", "prompt.md")
    if not os.path.exists(prompt_path):
        return None

    with open(prompt_path) as f:
        content = f.read()

    content = content.replace("{{CUSTOM_SKILLS}}", custom_skills_section)
    return content


def deploy_custom_skills(skills_dest: str, custom_skills: list[dict]):
    """Write custom skill markdown files to a skills directory.

    Args:
        skills_dest: Directory to write skill .md files into
        custom_skills: List of dicts with 'name', 'description', 'content' keys
    """
    os.makedirs(skills_dest, exist_ok=True)
    for skill in custom_skills:
        skill_path = os.path.join(skills_dest, f"{skill['name']}.md")
        desc = skill.get("description") or ""
        with open(skill_path, "w") as f:
            f.write(f"---\nname: {skill['name']}\ndescription: {desc}\n---\n\n{skill.get('content', '')}")


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
    
    # Copy safety gate hook from shared location (stateless, agent-agnostic)
    src_safety_path = os.path.join(_SHARED_HOOKS_DIR, "check-command.sh")
    safety_hook_path = os.path.join(hooks_dir, "check-command.sh")
    shutil.copy2(src_safety_path, safety_hook_path)
    os.chmod(safety_hook_path, os.stat(safety_hook_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    
    # Copy settings.json template and substitute placeholders
    src_settings_path = os.path.join(_AGENTS_DIR, "worker", "settings.json")
    dst_settings_path = os.path.join(worker_dir, "settings.json")
    
    with open(src_settings_path) as f:
        settings_content = f.read()
    
    settings_content = settings_content.replace("{{HOOK_SCRIPT_PATH}}", hook_script_path)
    settings_content = settings_content.replace("{{SAFETY_HOOK_PATH}}", safety_hook_path)
    
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
    # Deploy hook scripts
    hooks_dir = os.path.join(brain_dir, "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    
    src_hook_path = os.path.join(_AGENTS_DIR, "brain", "hooks", "inject-focus.sh")
    inject_hook_path = os.path.join(hooks_dir, "inject-focus.sh")
    shutil.copy2(src_hook_path, inject_hook_path)
    os.chmod(inject_hook_path, os.stat(inject_hook_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    
    # Copy safety gate hook from shared location (stateless, agent-agnostic)
    src_safety_path = os.path.join(_SHARED_HOOKS_DIR, "check-command.sh")
    safety_hook_path = os.path.join(hooks_dir, "check-command.sh")
    shutil.copy2(src_safety_path, safety_hook_path)
    os.chmod(safety_hook_path, os.stat(safety_hook_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    
    # Copy settings.json template and substitute placeholders
    claude_dir = os.path.join(brain_dir, ".claude")
    os.makedirs(claude_dir, exist_ok=True)
    
    src_settings_path = os.path.join(_AGENTS_DIR, "brain", "settings.json")
    settings_path = os.path.join(claude_dir, "settings.json")
    
    with open(src_settings_path) as f:
        settings_content = f.read()
    
    settings_content = settings_content.replace("{{INJECT_FOCUS_PATH}}", inject_hook_path)
    settings_content = settings_content.replace("{{SAFETY_HOOK_PATH}}", safety_hook_path)
    
    with open(settings_path, "w") as f:
        f.write(settings_content)
    
    return settings_path
