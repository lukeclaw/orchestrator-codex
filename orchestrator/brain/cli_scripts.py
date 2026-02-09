"""Generate CLI scripts for brain agent.

These scripts provide a simple command-line interface to the orchestrator API,
replacing complex curl commands with easy-to-use tools.

Scripts are deployed to the brain's bin directory when starting.
"""

import os
import stat

# Simple header for brain scripts (no session/task scoping needed)
BRAIN_SCRIPT_HEADER = '''#!/bin/bash
# {script_name}: {script_description}
# Brain CLI tool

API_BASE="{api_base}"

# Helper to pretty-print JSON if jq is available
pp() {{
    if command -v jq &> /dev/null; then
        jq .
    else
        cat
    fi
}}

# Helper to build JSON payload from key=value pairs
build_json() {{
    local json="{{"
    local first=true
    for arg in "$@"; do
        local key="${{arg%%=*}}"
        local value="${{arg#*=}}"
        if [[ "$first" != true ]]; then
            json="$json,"
        fi
        # Check if value looks like a number or boolean
        if [[ "$value" =~ ^[0-9]+$ ]] || [[ "$value" == "true" ]] || [[ "$value" == "false" ]] || [[ "$value" == "null" ]]; then
            json="$json\\"$key\\": $value"
        else
            json="$json\\"$key\\": \\"$value\\""
        fi
        first=false
    done
    json="$json}}"
    echo "$json"
}}
'''

# ============================================================================
# orch-workers: Manage worker sessions
# ============================================================================
BRAIN_WORKERS_SCRIPT = BRAIN_SCRIPT_HEADER + '''
show_help() {{
    echo "Usage: orch-workers <command> [options]"
    echo ""
    echo "Commands:"
    echo "  list                          List all workers"
    echo "  show <id>                     Show worker details"
    echo "  delete <id>                   Delete a worker"
    echo ""
    echo "Examples:"
    echo "  orch-workers list"
    echo "  orch-workers show abc123"
    echo "  orch-workers delete abc123"
}}

cmd_list() {{
    curl -s "$API_BASE/api/sessions?session_type=worker" | pp
}}

cmd_show() {{
    local id="$1"
    if [[ -z "$id" ]]; then
        echo "Error: Worker ID required" >&2
        exit 1
    fi
    curl -s "$API_BASE/api/sessions/$id" | pp
}}

cmd_delete() {{
    local id="$1"
    if [[ -z "$id" ]]; then
        echo "Error: Worker ID required" >&2
        exit 1
    fi
    curl -s -X DELETE "$API_BASE/api/sessions/$id" | pp
}}

case "$1" in
    list) shift; cmd_list "$@" ;;
    show) shift; cmd_show "$@" ;;
    delete) shift; cmd_delete "$@" ;;
    -h|--help|"") show_help ;;
    *) echo "Unknown command: $1" >&2; show_help; exit 1 ;;
esac
'''

# ============================================================================
# orch-projects: Manage projects
# ============================================================================
BRAIN_PROJECTS_SCRIPT = BRAIN_SCRIPT_HEADER + '''
show_help() {{
    echo "Usage: orch-projects <command> [options]"
    echo ""
    echo "Commands:"
    echo "  list                          List all projects"
    echo "  show <id>                     Show project details"
    echo "  create [options]              Create a new project"
    echo "  update <id> [options]         Update a project"
    echo ""
    echo "Create/Update Options:"
    echo "  --name NAME                   Project name (required for create)"
    echo "  --description DESC            Project description"
    echo "  --status STATUS               Project status (active|completed|archived)"
    echo "  --task-prefix PREFIX          Task key prefix (e.g., UTI)"
    echo ""
    echo "Examples:"
    echo "  orch-projects list"
    echo "  orch-projects create --name \\"Auth Migration\\" --description \\"Migrate to OAuth 2.0\\""
    echo "  orch-projects update abc123 --status completed"
}}

cmd_list() {{
    curl -s "$API_BASE/api/projects" | pp
}}

cmd_show() {{
    local id="$1"
    if [[ -z "$id" ]]; then
        echo "Error: Project ID required" >&2
        exit 1
    fi
    curl -s "$API_BASE/api/projects/$id" | pp
}}

cmd_create() {{
    local name="" description="" status="" task_prefix=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --name) name="$2"; shift 2 ;;
            --description) description="$2"; shift 2 ;;
            --status) status="$2"; shift 2 ;;
            --task-prefix) task_prefix="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    
    if [[ -z "$name" ]]; then
        echo "Error: --name is required" >&2
        exit 1
    fi
    
    local json="{{\\"name\\": \\"$name\\""
    [[ -n "$description" ]] && json="$json, \\"description\\": \\"$description\\""
    [[ -n "$status" ]] && json="$json, \\"status\\": \\"$status\\""
    [[ -n "$task_prefix" ]] && json="$json, \\"task_prefix\\": \\"$task_prefix\\""
    json="$json}}"
    
    curl -s -X POST "$API_BASE/api/projects" \\
        -H 'Content-Type: application/json' \\
        -d "$json" | pp
}}

cmd_update() {{
    local id="$1"
    shift
    if [[ -z "$id" ]]; then
        echo "Error: Project ID required" >&2
        exit 1
    fi
    
    local name="" description="" status="" task_prefix=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --name) name="$2"; shift 2 ;;
            --description) description="$2"; shift 2 ;;
            --status) status="$2"; shift 2 ;;
            --task-prefix) task_prefix="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    
    local json="{{"
    local first=true
    if [[ -n "$name" ]]; then
        json="$json\\"name\\": \\"$name\\""; first=false
    fi
    if [[ -n "$description" ]]; then
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"description\\": \\"$description\\""; first=false
    fi
    if [[ -n "$status" ]]; then
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"status\\": \\"$status\\""; first=false
    fi
    if [[ -n "$task_prefix" ]]; then
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"task_prefix\\": \\"$task_prefix\\""
    fi
    json="$json}}"
    
    curl -s -X PATCH "$API_BASE/api/projects/$id" \\
        -H 'Content-Type: application/json' \\
        -d "$json" | pp
}}

case "$1" in
    list) shift; cmd_list "$@" ;;
    show) shift; cmd_show "$@" ;;
    create) shift; cmd_create "$@" ;;
    update) shift; cmd_update "$@" ;;
    -h|--help|"") show_help ;;
    *) echo "Unknown command: $1" >&2; show_help; exit 1 ;;
esac
'''

# ============================================================================
# orch-tasks: Manage tasks
# ============================================================================
BRAIN_TASKS_SCRIPT = BRAIN_SCRIPT_HEADER + '''
show_help() {{
    echo "Usage: orch-tasks <command> [options]"
    echo ""
    echo "Commands:"
    echo "  list [--project-id ID] [--status S]   List tasks"
    echo "  show <id>                             Show task details"
    echo "  create [options]                      Create a new task"
    echo "  update <id> [options]                 Update a task"
    echo "  assign <task-id> <worker-id>          Assign task to worker"
    echo "  unassign <task-id>                    Unassign task from worker"
    echo ""
    echo "Create/Update Options:"
    echo "  --project-id ID               Project ID (required for create)"
    echo "  --title TITLE                 Task title (required for create)"
    echo "  --description DESC            Task description"
    echo "  --status STATUS               Task status (todo|in_progress|done|blocked)"
    echo "  --priority PRIORITY           Priority (high|medium|low)"
    echo "  --parent-id ID                Parent task ID (for subtasks)"
    echo "  --add-link URL                Add a link to the task"
    echo "  --add-link-tag TAG            Tag for the link (e.g., PR, PRD, DOC)"
    echo ""
    echo "Examples:"
    echo "  orch-tasks list --project-id abc123"
    echo "  orch-tasks create --project-id abc123 --title \\"Add OAuth callback\\" --priority high"
    echo "  orch-tasks assign task123 worker456"
    echo "  orch-tasks update task123 --status done"
    echo "  orch-tasks update task123 --add-link \\"https://github.com/pr/123\\" --add-link-tag PR"
}}

cmd_list() {{
    local project_id="" status="" assigned=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --project-id) project_id="$2"; shift 2 ;;
            --status) status="$2"; shift 2 ;;
            --assigned) assigned="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    
    local url="$API_BASE/api/tasks"
    local sep="?"
    [[ -n "$project_id" ]] && url="$url${{sep}}project_id=$project_id" && sep="&"
    [[ -n "$status" ]] && url="$url${{sep}}status=$status" && sep="&"
    [[ -n "$assigned" ]] && url="$url${{sep}}assigned_session_id=$assigned"
    
    curl -s "$url" | pp
}}

cmd_show() {{
    local id="$1"
    if [[ -z "$id" ]]; then
        echo "Error: Task ID required" >&2
        exit 1
    fi
    curl -s "$API_BASE/api/tasks/$id" | pp
}}

cmd_create() {{
    local project_id="" title="" description="" status="" priority="" parent_id=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --project-id) project_id="$2"; shift 2 ;;
            --title) title="$2"; shift 2 ;;
            --description) description="$2"; shift 2 ;;
            --status) status="$2"; shift 2 ;;
            --priority) priority="$2"; shift 2 ;;
            --parent-id) parent_id="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    
    if [[ -z "$project_id" ]]; then
        echo "Error: --project-id is required" >&2
        exit 1
    fi
    if [[ -z "$title" ]]; then
        echo "Error: --title is required" >&2
        exit 1
    fi
    
    local json="{{\\"project_id\\": \\"$project_id\\", \\"title\\": \\"$title\\""
    [[ -n "$description" ]] && json="$json, \\"description\\": \\"$description\\""
    [[ -n "$status" ]] && json="$json, \\"status\\": \\"$status\\""
    [[ -n "$priority" ]] && json="$json, \\"priority\\": \\"$priority\\""
    [[ -n "$parent_id" ]] && json="$json, \\"parent_task_id\\": \\"$parent_id\\""
    json="$json}}"
    
    curl -s -X POST "$API_BASE/api/tasks" \\
        -H 'Content-Type: application/json' \\
        -d "$json" | pp
}}

cmd_update() {{
    local id="$1"
    shift
    if [[ -z "$id" ]]; then
        echo "Error: Task ID required" >&2
        exit 1
    fi
    
    local title="" description="" status="" priority="" add_link="" add_link_tag=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --title) title="$2"; shift 2 ;;
            --description) description="$2"; shift 2 ;;
            --status) status="$2"; shift 2 ;;
            --priority) priority="$2"; shift 2 ;;
            --add-link) add_link="$2"; shift 2 ;;
            --add-link-tag) add_link_tag="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    
    local json="{{"
    local first=true
    if [[ -n "$title" ]]; then
        json="$json\\"title\\": \\"$title\\""; first=false
    fi
    if [[ -n "$description" ]]; then
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"description\\": \\"$description\\""; first=false
    fi
    if [[ -n "$status" ]]; then
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"status\\": \\"$status\\""; first=false
    fi
    if [[ -n "$priority" ]]; then
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"priority\\": \\"$priority\\""; first=false
    fi
    if [[ -n "$add_link" ]]; then
        # Fetch existing links and append
        local existing=$(curl -s "$API_BASE/api/tasks/$id" | jq -c '.links // []')
        local new_link="{{\\"url\\": \\"$add_link\\"}}"
        if [[ -n "$add_link_tag" ]]; then
            new_link="{{\\"url\\": \\"$add_link\\", \\"tag\\": \\"$add_link_tag\\"}}"
        fi
        local updated_links=$(echo "$existing" | jq -c ". + [$new_link]")
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"links\\": $updated_links"; first=false
    fi
    json="$json}}"
    
    curl -s -X PATCH "$API_BASE/api/tasks/$id" \\
        -H 'Content-Type: application/json' \\
        -d "$json" | pp
}}

cmd_assign() {{
    local task_id="$1"
    local worker_id="$2"
    if [[ -z "$task_id" ]] || [[ -z "$worker_id" ]]; then
        echo "Error: Both task ID and worker ID required" >&2
        echo "Usage: orch-tasks assign <task-id> <worker-id>" >&2
        exit 1
    fi
    
    curl -s -X PATCH "$API_BASE/api/tasks/$task_id" \\
        -H 'Content-Type: application/json' \\
        -d "{{\\"assigned_session_id\\": \\"$worker_id\\"}}" | pp
}}

cmd_unassign() {{
    local task_id="$1"
    if [[ -z "$task_id" ]]; then
        echo "Error: Task ID required" >&2
        exit 1
    fi
    
    curl -s -X PATCH "$API_BASE/api/tasks/$task_id" \\
        -H 'Content-Type: application/json' \\
        -d '{{"assigned_session_id": null}}' | pp
}}

case "$1" in
    list) shift; cmd_list "$@" ;;
    show) shift; cmd_show "$@" ;;
    create) shift; cmd_create "$@" ;;
    update) shift; cmd_update "$@" ;;
    assign) shift; cmd_assign "$@" ;;
    unassign) shift; cmd_unassign "$@" ;;
    -h|--help|"") show_help ;;
    *) echo "Unknown command: $1" >&2; show_help; exit 1 ;;
esac
'''

# ============================================================================
# orch-ctx: Manage context items
# ============================================================================
BRAIN_CONTEXT_SCRIPT = BRAIN_SCRIPT_HEADER + '''
show_help() {{
    echo "Usage: orch-ctx <command> [options]"
    echo ""
    echo "Commands:"
    echo "  list [filters]                List context items (titles + descriptions)"
    echo "  read <id> [id2 ...]           Read full content of context item(s)"
    echo "  create [options]              Create a new context item"
    echo "  update <id> [options]         Update a context item"
    echo "  delete <id>                   Delete a context item"
    echo ""
    echo "List Filters:"
    echo "  --scope SCOPE                 Filter by scope (global|brain|project)"
    echo "  --project-id ID               Filter by project ID"
    echo "  --category CAT                Filter by category"
    echo "  --search TEXT                 Search in title/content"
    echo ""
    echo "Create/Update Options:"
    echo "  --title TITLE                 Context title (required for create)"
    echo "  --content CONTENT             Context content (required for create)"
    echo "  --description DESC            Brief description"
    echo "  --scope SCOPE                 Scope: global|brain|project (default: global)"
    echo "  --project-id ID               Project ID (required if scope=project)"
    echo "  --category CAT                Category: instruction|requirement|convention|reference|note"
    echo ""
    echo "Examples:"
    echo "  orch-ctx list --scope global"
    echo "  orch-ctx read abc123"
    echo "  orch-ctx create --title \\"Coding style\\" --content \\"Use 2-space indent\\" --scope global"
    echo "  orch-ctx create --title \\"Strategy\\" --content \\"Worker-1 handles API\\" --scope brain"
}}

cmd_list() {{
    local scope="" project_id="" category="" search=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --scope) scope="$2"; shift 2 ;;
            --project-id) project_id="$2"; shift 2 ;;
            --category) category="$2"; shift 2 ;;
            --search) search="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    
    local url="$API_BASE/api/context"
    local sep="?"
    [[ -n "$scope" ]] && url="$url${{sep}}scope=$scope" && sep="&"
    [[ -n "$project_id" ]] && url="$url${{sep}}project_id=$project_id" && sep="&"
    [[ -n "$category" ]] && url="$url${{sep}}category=$category" && sep="&"
    [[ -n "$search" ]] && url="$url${{sep}}search=$search"
    
    # Return only id, title, description, category for listing
    curl -s "$url" | jq '.[] | {{id: .id, title: .title, description: .description, category: .category, scope: .scope}}'
}}

cmd_read() {{
    if [[ $# -eq 0 ]]; then
        echo "Error: At least one context item ID required" >&2
        exit 1
    fi
    
    for id in "$@"; do
        echo "=== Context: $id ==="
        curl -s "$API_BASE/api/context/$id" | pp
        echo ""
    done
}}

cmd_create() {{
    local title="" content="" description="" scope="global" project_id="" category=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --title) title="$2"; shift 2 ;;
            --content) content="$2"; shift 2 ;;
            --description) description="$2"; shift 2 ;;
            --scope) scope="$2"; shift 2 ;;
            --project-id) project_id="$2"; shift 2 ;;
            --category) category="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    
    if [[ -z "$title" ]]; then
        echo "Error: --title is required" >&2
        exit 1
    fi
    if [[ -z "$content" ]]; then
        echo "Error: --content is required" >&2
        exit 1
    fi
    if [[ "$scope" == "project" ]] && [[ -z "$project_id" ]]; then
        echo "Error: --project-id is required when scope=project" >&2
        exit 1
    fi
    
    # Escape content for JSON (handle newlines and quotes)
    local escaped_content=$(echo "$content" | sed 's/\\/\\\\/g' | sed 's/"/\\"/g' | sed ':a;N;$!ba;s/\\n/\\\\n/g')
    
    local json="{{\\"title\\": \\"$title\\", \\"content\\": \\"$escaped_content\\", \\"scope\\": \\"$scope\\", \\"source\\": \\"brain\\""
    [[ -n "$description" ]] && json="$json, \\"description\\": \\"$description\\""
    [[ -n "$project_id" ]] && json="$json, \\"project_id\\": \\"$project_id\\""
    [[ -n "$category" ]] && json="$json, \\"category\\": \\"$category\\""
    json="$json}}"
    
    curl -s -X POST "$API_BASE/api/context" \\
        -H 'Content-Type: application/json' \\
        -d "$json" | pp
}}

cmd_update() {{
    local id="$1"
    shift
    if [[ -z "$id" ]]; then
        echo "Error: Context item ID required" >&2
        exit 1
    fi
    
    local title="" content="" description="" category=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --title) title="$2"; shift 2 ;;
            --content) content="$2"; shift 2 ;;
            --description) description="$2"; shift 2 ;;
            --category) category="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    
    local json="{{"
    local first=true
    if [[ -n "$title" ]]; then
        json="$json\\"title\\": \\"$title\\""; first=false
    fi
    if [[ -n "$content" ]]; then
        local escaped_content=$(echo "$content" | sed 's/\\/\\\\/g' | sed 's/"/\\"/g' | sed ':a;N;$!ba;s/\\n/\\\\n/g')
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"content\\": \\"$escaped_content\\""; first=false
    fi
    if [[ -n "$description" ]]; then
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"description\\": \\"$description\\""; first=false
    fi
    if [[ -n "$category" ]]; then
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"category\\": \\"$category\\""
    fi
    json="$json}}"
    
    curl -s -X PATCH "$API_BASE/api/context/$id" \\
        -H 'Content-Type: application/json' \\
        -d "$json" | pp
}}

cmd_delete() {{
    local id="$1"
    if [[ -z "$id" ]]; then
        echo "Error: Context item ID required" >&2
        exit 1
    fi
    curl -s -X DELETE "$API_BASE/api/context/$id" | pp
}}

case "$1" in
    list) shift; cmd_list "$@" ;;
    read) shift; cmd_read "$@" ;;
    create) shift; cmd_create "$@" ;;
    update) shift; cmd_update "$@" ;;
    delete) shift; cmd_delete "$@" ;;
    -h|--help|"") show_help ;;
    *) echo "Unknown command: $1" >&2; show_help; exit 1 ;;
esac
'''

# ============================================================================
# orch-send: Send messages to workers
# ============================================================================
BRAIN_SEND_SCRIPT = BRAIN_SCRIPT_HEADER + '''
show_help() {{
    echo "Usage: orch-send <worker-id> <message>"
    echo ""
    echo "Send a message to a worker's terminal."
    echo ""
    echo "Examples:"
    echo "  orch-send abc123 \\"Please focus on the API endpoint first\\""
}}

if [[ $# -lt 2 ]]; then
    show_help
    exit 1
fi

worker_id="$1"
shift
message="$*"

# Get worker's tmux session info
worker_info=$(curl -s "$API_BASE/api/sessions/$worker_id")
tmux_session=$(echo "$worker_info" | jq -r '.name // empty')
tmux_window=$(echo "$worker_info" | jq -r '.tmux_window // empty')

if [[ -z "$tmux_session" ]]; then
    echo "Error: Could not find worker $worker_id" >&2
    exit 1
fi

# Send message via orchestrator API
curl -s -X POST "$API_BASE/api/sessions/$worker_id/send" \\
    -H 'Content-Type: application/json' \\
    -d "{{\\"message\\": \\"$message\\"}}" | pp
'''


def generate_brain_scripts(
    brain_dir: str,
    api_base: str = "http://127.0.0.1:8093",
) -> str:
    """Generate CLI scripts for the brain and return the bin directory path.
    
    Args:
        brain_dir: Base directory for brain scripts (e.g., /tmp/orchestrator/brain)
        api_base: API base URL
        
    Returns:
        Path to the bin directory containing the scripts
    """
    bin_dir = os.path.join(brain_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    
    scripts = {
        "orch-workers": (BRAIN_WORKERS_SCRIPT, "Manage worker sessions"),
        "orch-projects": (BRAIN_PROJECTS_SCRIPT, "Manage projects"),
        "orch-tasks": (BRAIN_TASKS_SCRIPT, "Manage tasks"),
        "orch-ctx": (BRAIN_CONTEXT_SCRIPT, "Manage context items"),
        "orch-send": (BRAIN_SEND_SCRIPT, "Send messages to workers"),
    }
    
    for script_name, (template, description) in scripts.items():
        script_path = os.path.join(bin_dir, script_name)
        
        content = template.format(
            script_name=script_name,
            script_description=description,
            api_base=api_base,
        )
        
        with open(script_path, "w") as f:
            f.write(content)
        
        # Make executable
        os.chmod(script_path, os.stat(script_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    
    return bin_dir


def get_brain_path_export(bin_dir: str) -> str:
    """Get the shell command to add brain bin_dir to PATH."""
    return f'export PATH="{bin_dir}:$PATH"'


def generate_brain_hooks(
    brain_dir: str,
    api_base: str = "http://127.0.0.1:8093",
) -> str:
    """Generate Claude Code hooks settings.json for the brain.
    
    The brain hooks inject current dashboard URL into each prompt submission,
    so the brain knows what page the user is viewing.
    
    Args:
        brain_dir: Directory to generate hooks in
        api_base: API base URL
        
    Returns:
        Path to the settings.json file
    """
    hooks_dir = os.path.join(brain_dir, "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    
    # Create hook script that injects current URL
    hook_script_path = os.path.join(hooks_dir, "inject-focus.sh")
    hook_script = f'''#!/bin/bash
# Hook script to inject current dashboard URL into brain prompts

API_BASE="{api_base}"

# Read JSON input from stdin
INPUT=$(cat)

# Only process UserPromptSubmit events
EVENT=$(echo "$INPUT" | jq -r '.hook_event_name // empty')
if [[ "$EVENT" != "UserPromptSubmit" ]]; then
    exit 0
fi

# Fetch and output current URL
URL=$(curl -s "$API_BASE/api/brain/focus" | jq -r '.url // empty')
if [[ -n "$URL" && "$URL" != "null" ]]; then
    echo "[Dashboard: $URL]"
fi

exit 0
'''
    
    with open(hook_script_path, "w") as f:
        f.write(hook_script)
    
    # Make executable
    os.chmod(hook_script_path, os.stat(hook_script_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    
    # Create settings.json with hooks configuration
    import json
    settings = {
        "hooks": {
            "UserPromptSubmit": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": hook_script_path
                        }
                    ]
                }
            ]
        }
    }
    
    settings_path = os.path.join(brain_dir, "settings.json")
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
    
    return settings_path
