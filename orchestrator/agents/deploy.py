"""Deploy agent CLI scripts and prompts.

This module handles copying static scripts from agents/ to worker/brain tmp directories
and generating dynamic configuration (hooks, settings) with session-specific values.

It also provides Single Source of Truth (SOT) functions for deploying complete
worker and brain tmp directories, and manifest-based health verification.
"""

import json
import logging
import os
import shutil
import sqlite3
import stat

from orchestrator import paths

logger = logging.getLogger(__name__)

# Path to the agents directory (resolved via paths module for dev/packaged compat)
_AGENTS_DIR = str(paths.agents_dir())
_SHARED_HOOKS_DIR = os.path.join(_AGENTS_DIR, "shared", "hooks")

# Script names for iteration
WORKER_SCRIPT_NAMES = [
    "orch-task",
    "orch-subtask",
    "orch-worker",
    "orch-blocked",
    "orch-context",
    "orch-notify",
    "orch-tunnel",
    "orch-prs",
    "orch-interactive",
    "orch-browser",
    "orch-skills",
]
BRAIN_SCRIPT_NAMES = [
    "orch-workers",
    "orch-projects",
    "orch-tasks",
    "orch-ctx",
    "orch-memory",
    "orch-skills",
    "orch-send",
    "orch-notifications",
    "orch-tunnel",
    "orch-prs",
]


def get_path_export_command(bin_dir: str) -> str:
    """Get the shell command to add bin_dir to PATH."""
    return f'export PATH="{bin_dir}:$PATH"'


def deploy_worker_scripts(
    worker_dir: str,
    session_id: str,
    api_base: str = "http://127.0.0.1:8093",
    cdp_port: int = 9222,
    browser_headless: bool = True,
) -> str:
    """Deploy worker CLI scripts to the worker's bin directory.

    Copies static scripts from agents/worker/bin/ and creates lib.sh with
    environment variable defaults.

    Args:
        worker_dir: Base directory for the worker (e.g., /tmp/orchestrator/workers/worker1)
        session_id: Worker's session ID
        api_base: API base URL
        cdp_port: CDP port for browser debugging (default 9222)
        browser_headless: Whether browser runs headless (True for remote, False for local)

    Returns:
        Path to the bin directory containing the scripts
    """
    bin_dir = os.path.join(worker_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)

    # Copy lib.sh with environment variable values injected
    lib_content = f"""#!/bin/bash
# Worker CLI library - shared functions for worker scripts
# Auto-generated with session-specific defaults

# Environment variables (can be overridden)
export ORCH_SESSION_ID="${{ORCH_SESSION_ID:-{session_id}}}"
export ORCH_API_BASE="${{ORCH_API_BASE:-{api_base}}}"
export ORCH_WORKER_DIR="${{ORCH_WORKER_DIR:-{worker_dir}}}"
export ORCH_CDP_PORT="${{ORCH_CDP_PORT:-{cdp_port}}}"
export ORCH_BROWSER_HEADLESS="${{ORCH_BROWSER_HEADLESS:-{str(browser_headless).lower()}}}"

# Aliases for cleaner script code
SESSION_ID="$ORCH_SESSION_ID"
API_BASE="$ORCH_API_BASE"
WORKER_DIR="$ORCH_WORKER_DIR"

# Load task info from API
load_task_info() {{
    local http_code
    local tasks_json
    tasks_json=$(curl -s -w "\\n%{{http_code}}" \\
        --connect-timeout 5 \\
        "$API_BASE/api/tasks?assigned_session_id=$SESSION_ID")
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
"""

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
            os.chmod(
                dst_path, os.stat(dst_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )

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
    lib_content = f"""#!/bin/bash
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
        if [[ "$value" =~ ^[0-9]+$ ]] || \\
           [[ "$value" == "true" ]] || \\
           [[ "$value" == "false" ]] || \\
           [[ "$value" == "null" ]]; then
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
"""

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
            os.chmod(
                dst_path, os.stat(dst_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )

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


def get_brain_memory_section(conn: sqlite3.Connection, provider: str | None = None) -> str:
    """Load the curated brain wisdom item for system prompt injection.

    Looks for a single context item with scope=brain, category=wisdom.
    When ``provider`` is set, shared and provider-matching wisdom is considered.
    Returns formatted markdown section, or empty string if not found.
    """
    from orchestrator.state.repositories.context import list_context

    items = list_context(conn, scope="brain", category="wisdom", provider=provider)
    if not items:
        return ""

    item = items[0]
    if provider is not None:
        exact_match = next((candidate for candidate in items if candidate.provider == provider), None)
        if exact_match is not None:
            item = exact_match

    content = item.content.strip()
    if not content:
        return ""

    return f"\n## Long-Term Memory\n\n{content}\n"


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


def get_brain_prompt(custom_skills_section: str = "", brain_memory_section: str = "") -> str | None:
    """Load brain prompt.

    Args:
        custom_skills_section: Pre-formatted custom skills text to inject
        brain_memory_section: Pre-formatted brain long-term memory to inject

    Returns:
        Prompt string, or None if not found
    """
    prompt_path = os.path.join(_AGENTS_DIR, "brain", "prompt.md")
    if not os.path.exists(prompt_path):
        return None

    with open(prompt_path) as f:
        content = f.read()

    content = content.replace("{{CUSTOM_SKILLS}}", custom_skills_section)
    content = content.replace("{{BRAIN_MEMORY}}", brain_memory_section)
    return content


def _read_provider_prompt_template(provider: str, *parts: str) -> str | None:
    prompt_path = os.path.join(_AGENTS_DIR, provider, *parts)
    if not os.path.exists(prompt_path):
        return None

    with open(prompt_path) as f:
        return f.read()


def get_codex_worker_prompt() -> str | None:
    """Load the Codex worker prompt template."""
    return _read_provider_prompt_template("codex", "worker", "prompt.md")


def get_codex_brain_prompt(brain_memory_section: str = "") -> str | None:
    """Load the Codex brain prompt template with optional memory prelude."""
    content = _read_provider_prompt_template("codex", "brain", "prompt.md")
    if content is None:
        return None
    if brain_memory_section:
        return f"{brain_memory_section.rstrip()}\n\n{content}"
    return content


def deploy_custom_skills(skills_dest: str, custom_skills: list[dict]):
    """Write custom skill markdown files to a skills directory.

    The generated file includes YAML frontmatter plus a leading heading and
    description paragraph so the deployed file is self-documenting.  The DB
    stores name, description, and body content separately; this function
    reassembles them into a complete .md file.

    Args:
        skills_dest: Directory to write skill .md files into
        custom_skills: List of dicts with 'name', 'description', 'content' keys
    """
    os.makedirs(skills_dest, exist_ok=True)
    for skill in custom_skills:
        skill_path = os.path.join(skills_dest, f"{skill['name']}.md")
        name = skill["name"]
        desc = skill.get("description") or ""
        body = skill.get("content", "")

        parts = [f"---\nname: {name}\ndescription: {desc}\n---\n"]
        parts.append(f"\n# {name}\n")
        if desc:
            parts.append(f"\n{desc}\n")
        if body.strip():
            parts.append(f"\n{body}")

        with open(skill_path, "w") as f:
            f.write("".join(parts))


def generate_worker_hooks(
    worker_dir: str,
    session_id: str,
    api_base: str = "http://127.0.0.1:8093",
    model: str = "opus",
    effort: str = "high",
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
    os.chmod(
        hook_script_path,
        os.stat(hook_script_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
    )

    # Copy safety gate hook from shared location (stateless, agent-agnostic)
    src_safety_path = os.path.join(_SHARED_HOOKS_DIR, "check-command.sh")
    safety_hook_path = os.path.join(hooks_dir, "check-command.sh")
    shutil.copy2(src_safety_path, safety_hook_path)
    os.chmod(
        safety_hook_path,
        os.stat(safety_hook_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
    )

    # Copy settings.json template, substitute placeholders, and inject model/effort
    src_settings_path = os.path.join(_AGENTS_DIR, "worker", "settings.json")
    dst_settings_path = os.path.join(worker_dir, "settings.json")

    with open(src_settings_path) as f:
        settings_content = f.read()

    settings_content = settings_content.replace("{{HOOK_SCRIPT_PATH}}", hook_script_path)
    settings_content = settings_content.replace("{{SAFETY_HOOK_PATH}}", safety_hook_path)

    # Inject user-configured model and effort
    settings_data = json.loads(settings_content)
    settings_data["model"] = model
    if effort != "high":
        settings_data["reasoningEffort"] = effort

    with open(dst_settings_path, "w") as f:
        json.dump(settings_data, f, indent=2)
        f.write("\n")

    return worker_dir


def generate_brain_hooks(
    brain_dir: str,
    api_base: str = "http://127.0.0.1:8093",
    model: str = "opus",
    effort: str = "high",
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
    os.chmod(
        inject_hook_path,
        os.stat(inject_hook_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
    )

    # Copy safety gate hook from shared location (stateless, agent-agnostic)
    src_safety_path = os.path.join(_SHARED_HOOKS_DIR, "check-command.sh")
    safety_hook_path = os.path.join(hooks_dir, "check-command.sh")
    shutil.copy2(src_safety_path, safety_hook_path)
    os.chmod(
        safety_hook_path,
        os.stat(safety_hook_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
    )

    # Pre-compaction memory flush hook
    src_precompact_path = os.path.join(_AGENTS_DIR, "brain", "hooks", "pre-compact.sh")
    precompact_hook_path = os.path.join(hooks_dir, "pre-compact.sh")
    shutil.copy2(src_precompact_path, precompact_hook_path)
    os.chmod(
        precompact_hook_path,
        os.stat(precompact_hook_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
    )

    # Session start hook (re-deploy + re-arm heartbeat after /clear)
    src_session_start_path = os.path.join(_AGENTS_DIR, "brain", "hooks", "on-session-start.sh")
    session_start_hook_path = os.path.join(hooks_dir, "on-session-start.sh")
    shutil.copy2(src_session_start_path, session_start_hook_path)
    os.chmod(
        session_start_hook_path,
        os.stat(session_start_hook_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
    )

    # Copy settings.json template, substitute placeholders, and inject model/effort
    claude_dir = os.path.join(brain_dir, ".claude")
    os.makedirs(claude_dir, exist_ok=True)

    src_settings_path = os.path.join(_AGENTS_DIR, "brain", "settings.json")
    settings_path = os.path.join(claude_dir, "settings.json")

    with open(src_settings_path) as f:
        settings_content = f.read()

    settings_content = settings_content.replace("{{INJECT_FOCUS_PATH}}", inject_hook_path)
    settings_content = settings_content.replace("{{SAFETY_HOOK_PATH}}", safety_hook_path)
    settings_content = settings_content.replace("{{PRE_COMPACT_HOOK_PATH}}", precompact_hook_path)
    settings_content = settings_content.replace(
        "{{SESSION_START_HOOK_PATH}}", session_start_hook_path
    )

    # Inject user-configured model and effort
    settings_data = json.loads(settings_content)
    settings_data["model"] = model
    if effort != "high":
        settings_data["reasoningEffort"] = effort

    with open(settings_path, "w") as f:
        json.dump(settings_data, f, indent=2)
        f.write("\n")

    return settings_path


# =============================================================================
# Manifest I/O — used by SOT deploy functions and health checks
# =============================================================================

MANIFEST_FILENAME = ".manifest.json"


def _write_manifest(base_dir: str, paths_list: list[str]) -> None:
    """Write a manifest file listing all deployed paths.

    The manifest is a JSON file with a version number and sorted list of
    relative paths. Health checks read this to verify completeness.
    """
    manifest_path = os.path.join(base_dir, MANIFEST_FILENAME)
    manifest = {
        "version": 1,
        "paths": sorted(set(paths_list)),
    }
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


def _read_manifest(base_dir: str) -> list[str] | None:
    """Read the manifest file and return list of relative paths.

    Returns None if the manifest is missing or corrupt.
    """
    manifest_path = os.path.join(base_dir, MANIFEST_FILENAME)
    try:
        with open(manifest_path) as f:
            data = json.load(f)
        if isinstance(data, dict) and "paths" in data:
            return data["paths"]
        return None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


# =============================================================================
# DB Helpers — shared by SOT functions and health checks
# =============================================================================


def _get_custom_skills_from_db(conn: sqlite3.Connection, target: str) -> list[dict]:
    """Read enabled custom skills from a DB connection.

    Args:
        conn: SQLite connection
        target: "worker" or "brain"

    Returns:
        List of dicts with 'name', 'description', 'content' keys.
    """
    try:
        rows = conn.execute(
            "SELECT name, description, content FROM skills WHERE target = ? AND enabled = 1",
            (target,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        # Table may not exist if migration hasn't run yet
        return []


def _get_disabled_builtins_from_db(conn: sqlite3.Connection, target: str = "worker") -> set[str]:
    """Read disabled built-in skill names from a DB connection.

    Args:
        conn: SQLite connection
        target: "worker" or "brain"

    Returns:
        Set of skill names that are disabled.
    """
    try:
        rows = conn.execute(
            "SELECT name FROM skill_overrides WHERE enabled = 0 AND target = ?",
            (target,),
        ).fetchall()
        return {r["name"] for r in rows}
    except Exception:
        # Table may not exist yet
        return set()


def _deploy_builtin_skills(
    skills_src: str | None, skills_dest: str, disabled_names: set[str] | None = None
) -> list[str]:
    """Copy built-in skill .md files, skipping disabled ones.

    Clears existing .md files in skills_dest before copying to avoid stale files.

    Args:
        skills_src: Path to source skills directory (e.g., agents/worker/skills/).
            If None or not a directory, no files are copied.
        skills_dest: Destination directory for skill .md files.
        disabled_names: Set of skill names to skip.

    Returns:
        List of filenames copied (e.g., ["deploy.md", "check-worker.md"]).
    """
    disabled = disabled_names or set()
    copied = []

    # Clear stale skill files before repopulating
    if os.path.isdir(skills_dest):
        for f in os.listdir(skills_dest):
            if f.endswith(".md"):
                os.remove(os.path.join(skills_dest, f))

    if not skills_src or not os.path.isdir(skills_src):
        return copied

    os.makedirs(skills_dest, exist_ok=True)
    for skill_file in os.listdir(skills_src):
        if skill_file.endswith(".md"):
            skill_name = os.path.splitext(skill_file)[0]
            if skill_name in disabled:
                continue
            shutil.copy2(
                os.path.join(skills_src, skill_file),
                os.path.join(skills_dest, skill_file),
            )
            copied.append(skill_file)

    return copied


# =============================================================================
# SOT Deploy Functions — Single Source of Truth for tmp dir contents
# =============================================================================


def deploy_worker_tmp_contents(
    tmp_dir: str,
    session_id: str,
    api_base: str = "http://127.0.0.1:8093",
    cdp_port: int = 9222,
    browser_headless: bool = False,
    conn: sqlite3.Connection | None = None,
    custom_skills: list[dict] | None = None,
    disabled_builtin_names: set[str] | None = None,
    model: str = "opus",
    effort: str = "high",
) -> list[str]:
    """Deploy all worker files to tmp_dir. SINGLE SOURCE OF TRUTH.

    THIS IS THE SINGLE SOURCE OF TRUTH for what the worker tmp directory
    should contain. All callers that need to create or regenerate the tmp
    dir must use this function.

    Called by:
    - Initial worker launch (session.py — both local and remote)
    - Reconnect regeneration (reconnect.py — replaces _ensure_local_configs_exist)
    - Health-check recovery (health.py — via ensure_tmp_dir_health)

    When ``conn`` is provided, skills and disabled overrides are read from
    the DB (takes precedence over explicit ``custom_skills``/``disabled_builtin_names``).

    Args:
        tmp_dir: Worker's tmp directory (e.g., /tmp/orchestrator/workers/worker1)
        session_id: Worker's session ID
        api_base: API base URL
        cdp_port: CDP port for browser debugging
        browser_headless: Whether browser runs headless
        conn: Optional DB connection for reading skills/overrides
        custom_skills: Explicit custom skills (used when conn is None)
        disabled_builtin_names: Explicit disabled builtins (used when conn is None)

    Returns:
        List of relative paths (from tmp_dir) of all files created.
        Also writes .manifest.json for health-check verification.
    """
    created: list[str] = []
    os.makedirs(tmp_dir, exist_ok=True)

    # 1. Hooks + settings.json
    configs_dir = os.path.join(tmp_dir, "configs")
    os.makedirs(configs_dir, exist_ok=True)
    generate_worker_hooks(configs_dir, session_id, api_base, model=model, effort=effort)
    created += [
        "configs/settings.json",
        "configs/hooks/update-status.sh",
        "configs/hooks/check-command.sh",
    ]

    # 2. Bin scripts (CLI tools like orch-browser, orch-status)
    deploy_worker_scripts(
        tmp_dir, session_id, api_base, cdp_port=cdp_port, browser_headless=browser_headless
    )
    created.append("bin/lib.sh")
    created += [f"bin/{name}" for name in WORKER_SCRIPT_NAMES]

    # 3. Resolve skills parameters from DB or explicit args
    if conn is not None:
        resolved_disabled = _get_disabled_builtins_from_db(conn, "worker")
        resolved_custom = _get_custom_skills_from_db(conn, "worker")
    else:
        resolved_disabled = disabled_builtin_names or set()
        resolved_custom = custom_skills or []

    # 4. Built-in skills → .claude/commands/
    skills_src = get_worker_skills_dir()
    local_skills_dir = os.path.join(tmp_dir, ".claude", "commands")
    copied_builtins = _deploy_builtin_skills(skills_src, local_skills_dir, resolved_disabled)
    created += [f".claude/commands/{f}" for f in copied_builtins]
    logger.info(
        "Deployed %d built-in skills to %s",
        len(copied_builtins),
        local_skills_dir,
    )

    # 5. Custom skills from DB → .claude/commands/
    if resolved_custom:
        deploy_custom_skills(local_skills_dir, resolved_custom)
        created += [f".claude/commands/{s['name']}.md" for s in resolved_custom]
        logger.info("Deployed %d custom skills to %s", len(resolved_custom), local_skills_dir)

    # 6. prompt.md (worker system prompt)
    custom_skills_section = format_custom_skills_for_prompt(resolved_custom)
    prompt = get_worker_prompt(session_id, custom_skills_section=custom_skills_section)
    if prompt:
        with open(os.path.join(tmp_dir, "prompt.md"), "w") as f:
            f.write(prompt)
        created.append("prompt.md")

    # Write manifest — the health check uses this to verify completeness
    _write_manifest(tmp_dir, created)
    logger.info("deploy_worker_tmp_contents: deployed %d files to %s", len(created), tmp_dir)

    return created


def deploy_codex_worker_tmp_contents(
    tmp_dir: str,
    session_id: str,
    api_base: str = "http://127.0.0.1:8093",
    cdp_port: int = 9222,
    browser_headless: bool = False,
) -> list[str]:
    """Deploy all Codex worker files to tmp_dir."""
    created: list[str] = []
    os.makedirs(tmp_dir, exist_ok=True)

    deploy_worker_scripts(
        tmp_dir, session_id, api_base, cdp_port=cdp_port, browser_headless=browser_headless
    )
    created.append("bin/lib.sh")
    created += [f"bin/{name}" for name in WORKER_SCRIPT_NAMES]

    prompt = get_codex_worker_prompt()
    if prompt:
        with open(os.path.join(tmp_dir, "prompt.md"), "w") as f:
            f.write(prompt)
        created.append("prompt.md")

    _write_manifest(tmp_dir, created)
    logger.info(
        "deploy_codex_worker_tmp_contents: deployed %d files to %s", len(created), tmp_dir
    )
    return created


def deploy_brain_tmp_contents(
    brain_dir: str,
    api_base: str = "http://127.0.0.1:8093",
    conn: sqlite3.Connection | None = None,
    provider: str | None = None,
    custom_skills: list[dict] | None = None,
    disabled_builtin_names: set[str] | None = None,
    model: str = "opus",
    effort: str = "high",
) -> list[str]:
    """Deploy all brain files to brain_dir. SINGLE SOURCE OF TRUTH.

    THIS IS THE SINGLE SOURCE OF TRUTH for what the brain tmp directory
    should contain. All callers that need to create or regenerate the brain
    dir must use this function.

    Called by:
    - Brain start (brain.py)
    - Health-check recovery (brain.py — via _ensure_brain_tmp_health)

    When ``conn`` is provided, skills and disabled overrides are read from
    the DB (takes precedence over explicit params).

    Args:
        brain_dir: Brain's working directory (e.g., /tmp/orchestrator/brain)
        api_base: API base URL
        conn: Optional DB connection for reading skills/overrides
        custom_skills: Explicit custom skills (used when conn is None)
        disabled_builtin_names: Explicit disabled builtins (used when conn is None)

    Returns:
        List of relative paths (from brain_dir) of all files created.
        Also writes .manifest.json for health-check verification.
    """
    created: list[str] = []
    os.makedirs(brain_dir, exist_ok=True)

    # 1. Resolve skills parameters from DB or explicit args
    if conn is not None:
        resolved_disabled = _get_disabled_builtins_from_db(conn, "brain")
        resolved_custom = _get_custom_skills_from_db(conn, "brain")
    else:
        resolved_disabled = disabled_builtin_names or set()
        resolved_custom = custom_skills or []

    # 2. CLAUDE.md (brain prompt)
    custom_skills_section = format_custom_skills_for_prompt(resolved_custom)
    brain_memory_section = (
        get_brain_memory_section(conn, provider=provider) if conn is not None else ""
    )
    brain_prompt = get_brain_prompt(
        custom_skills_section=custom_skills_section,
        brain_memory_section=brain_memory_section,
    )
    if brain_prompt:
        with open(os.path.join(brain_dir, "CLAUDE.md"), "w") as f:
            f.write(brain_prompt)
        created.append("CLAUDE.md")

    # 3. Hooks + settings
    generate_brain_hooks(brain_dir, api_base, model=model, effort=effort)
    created += [
        "hooks/inject-focus.sh",
        "hooks/check-command.sh",
        "hooks/pre-compact.sh",
        "hooks/on-session-start.sh",
        ".claude/settings.json",
    ]

    # 4. Bin scripts
    deploy_brain_scripts(brain_dir, api_base)
    created.append("bin/lib.sh")
    created += [f"bin/{name}" for name in BRAIN_SCRIPT_NAMES]

    # 5. Built-in skills → .claude/commands/
    skills_src = get_brain_skills_dir()
    skills_dest = os.path.join(brain_dir, ".claude", "commands")
    copied_builtins = _deploy_builtin_skills(skills_src, skills_dest, resolved_disabled)
    created += [f".claude/commands/{f}" for f in copied_builtins]
    logger.info("Deployed %d built-in brain skills to %s", len(copied_builtins), skills_dest)

    # 6. Custom skills from DB → .claude/commands/
    if resolved_custom:
        deploy_custom_skills(skills_dest, resolved_custom)
        created += [f".claude/commands/{s['name']}.md" for s in resolved_custom]
        logger.info("Deployed %d custom brain skills to %s", len(resolved_custom), skills_dest)

    # Write manifest
    _write_manifest(brain_dir, created)
    logger.info("deploy_brain_tmp_contents: deployed %d files to %s", len(created), brain_dir)

    return created


def deploy_codex_brain_tmp_contents(
    brain_dir: str,
    api_base: str = "http://127.0.0.1:8093",
    conn: sqlite3.Connection | None = None,
    provider: str | None = "codex",
) -> list[str]:
    """Deploy all Codex brain files to brain_dir."""
    created: list[str] = []
    os.makedirs(brain_dir, exist_ok=True)

    brain_memory_section = (
        get_brain_memory_section(conn, provider=provider) if conn is not None else ""
    )
    prompt = get_codex_brain_prompt(brain_memory_section=brain_memory_section)
    if prompt:
        with open(os.path.join(brain_dir, "prompt.md"), "w") as f:
            f.write(prompt)
        created.append("prompt.md")

    deploy_brain_scripts(brain_dir, api_base)
    created.append("bin/lib.sh")
    created += [f"bin/{name}" for name in BRAIN_SCRIPT_NAMES]

    _write_manifest(brain_dir, created)
    logger.info(
        "deploy_codex_brain_tmp_contents: deployed %d files to %s", len(created), brain_dir
    )
    return created
