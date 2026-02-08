"""Generate CLI scripts for worker task management.

These scripts are placed in the worker's bin directory and provide a scoped
interface to the orchestrator API, preventing workers from affecting tasks
or sessions outside their scope.

Scripts use file-based caching for task info to minimize API calls.
Cache TTL is 5 minutes by default.
"""

import os
import stat

# Common header for all scripts - handles caching and task lookup
SCRIPT_HEADER = '''#!/bin/bash
# {script_name}: {script_description}
# Generated for worker: {worker_name}

SESSION_ID="{session_id}"
API_BASE="{api_base}"
WORKER_DIR="{worker_dir}"
CACHE_FILE="$WORKER_DIR/.task_cache"
CACHE_TTL=300  # 5 minutes in seconds

# Load task info from cache or API
load_task_info() {{
    local force_refresh="$1"
    local cache_valid=false
    
    # Check if cache exists and is fresh
    if [[ -f "$CACHE_FILE" && "$force_refresh" != "true" ]]; then
        local cache_age=$(($(date +%s) - $(stat -f %m "$CACHE_FILE" 2>/dev/null || stat -c %Y "$CACHE_FILE" 2>/dev/null || echo 0)))
        if [[ $cache_age -lt $CACHE_TTL ]]; then
            cache_valid=true
        fi
    fi
    
    if [[ "$cache_valid" == "true" ]]; then
        # Load from cache
        source "$CACHE_FILE"
    else
        # Fetch from API
        local session_info=$(curl -s "$API_BASE/api/sessions/$SESSION_ID")
        TASK_ID=$(echo "$session_info" | jq -r '.current_task_id // empty')
        
        if [[ -z "$TASK_ID" || "$TASK_ID" == "null" ]]; then
            echo "Error: No task assigned to this worker" >&2
            return 1
        fi
        
        local task_info=$(curl -s "$API_BASE/api/tasks/$TASK_ID")
        PROJECT_ID=$(echo "$task_info" | jq -r '.project_id // empty')
        
        # Write cache
        mkdir -p "$WORKER_DIR"
        cat > "$CACHE_FILE" << CACHEEOF
TASK_ID="$TASK_ID"
PROJECT_ID="$PROJECT_ID"
CACHEEOF
    fi
    
    return 0
}}

# Check for --refresh flag in any position
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

ORCH_TASK_SCRIPT = SCRIPT_HEADER + '''
show_help() {{
    echo "Usage: orch-task <command> [options]"
    echo ""
    echo "Commands:"
    echo "  show [--refresh]              Show task details"
    echo "  update --status STATUS        Update task status (in_progress|done|blocked)"
    echo ""
    echo "Options:"
    echo "  --refresh                     Force refresh task info from API"
    echo ""
    echo "Examples:"
    echo "  orch-task show"
    echo "  orch-task show --refresh"
    echo "  orch-task update --status in_progress"
    echo "  orch-task update --status done"
}}

cmd_show() {{
    load_task_info "$FORCE_REFRESH" || exit 1
    curl -s "$API_BASE/api/tasks/$TASK_ID" | jq .
}}

cmd_update() {{
    load_task_info "$FORCE_REFRESH" || exit 1
    
    local status=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --status)
                status="$2"
                shift 2
                ;;
            *)
                echo "Unknown option: $1" >&2
                exit 1
                ;;
        esac
    done
    
    if [[ -z "$status" ]]; then
        echo "Error: --status is required" >&2
        exit 1
    fi
    
    curl -s -X PATCH "$API_BASE/api/tasks/$TASK_ID" \\
        -H 'Content-Type: application/json' \\
        -d "{{\\"status\\": \\"$status\\"}}" | jq .
}}

case "$1" in
    show)
        shift
        cmd_show "$@"
        ;;
    update)
        shift
        cmd_update "$@"
        ;;
    -h|--help|"")
        show_help
        ;;
    *)
        echo "Unknown command: $1" >&2
        show_help
        exit 1
        ;;
esac
'''

ORCH_SUBTASK_SCRIPT = SCRIPT_HEADER + '''
show_help() {{
    echo "Usage: orch-subtask <command> [options]"
    echo ""
    echo "Commands:"
    echo "  list                      List all subtasks"
    echo "  create --title TITLE [--description DESC] [--links URL1,URL2]"
    echo "                            Create a new subtask"
    echo "  update --id ID [--status STATUS] [--links URLS] [--add-link URL]"
    echo "                            Update a subtask"
    echo ""
    echo "Examples:"
    echo "  orch-subtask list"
    echo "  orch-subtask create --title \\"Fix bug\\" --description \\"Details\\""
    echo "  orch-subtask create --title \\"Add tests\\" --links \\"http://pr1,http://doc1\\""
    echo "  orch-subtask update --id UUID --status done"
    echo "  orch-subtask update --id UUID --add-link \\"http://github.com/pr/123\\""
}}

cmd_list() {{
    load_task_info "$FORCE_REFRESH" || exit 1
    curl -s "$API_BASE/api/tasks/$TASK_ID/subtasks" | jq .
}}

cmd_create() {{
    load_task_info "$FORCE_REFRESH" || exit 1
    local title=""
    local description=""
    local links=""
    
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --title)
                title="$2"
                shift 2
                ;;
            --description)
                description="$2"
                shift 2
                ;;
            --links)
                links="$2"
                shift 2
                ;;
            *)
                echo "Unknown option: $1" >&2
                exit 1
                ;;
        esac
    done
    
    if [[ -z "$title" ]]; then
        echo "Error: --title is required" >&2
        exit 1
    fi
    
    # Build JSON payload
    local json="{{\\"project_id\\": \\"$PROJECT_ID\\", \\"parent_task_id\\": \\"$TASK_ID\\", \\"title\\": \\"$title\\""
    
    if [[ -n "$description" ]]; then
        json="$json, \\"description\\": \\"$description\\""
    fi
    
    if [[ -n "$links" ]]; then
        # Convert comma-separated URLs to JSON array of link objects
        local links_json="["
        local first=true
        IFS=',' read -ra URLS <<< "$links"
        for url in "${{URLS[@]}}"; do
            url=$(echo "$url" | xargs)  # trim whitespace
            if [[ -n "$url" ]]; then
                if [[ "$first" != true ]]; then
                    links_json="$links_json,"
                fi
                links_json="$links_json{{\\"url\\": \\"$url\\", \\"type\\": \\"reference\\", \\"title\\": \\"$url\\"}}"
                first=false
            fi
        done
        links_json="$links_json]"
        json="$json, \\"links\\": $links_json"
    fi
    
    json="$json}}"
    
    curl -s -X POST "$API_BASE/api/tasks" \\
        -H 'Content-Type: application/json' \\
        -d "$json" | jq .
}}

cmd_update() {{
    load_task_info "$FORCE_REFRESH" || exit 1
    local subtask_id=""
    local status=""
    local links=""
    local add_link=""
    
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --id)
                subtask_id="$2"
                shift 2
                ;;
            --status)
                status="$2"
                shift 2
                ;;
            --links)
                links="$2"
                shift 2
                ;;
            --add-link)
                add_link="$2"
                shift 2
                ;;
            *)
                echo "Unknown option: $1" >&2
                exit 1
                ;;
        esac
    done
    
    if [[ -z "$subtask_id" ]]; then
        echo "Error: --id is required" >&2
        exit 1
    fi
    
    # Verify subtask belongs to our task
    local parent=$(curl -s "$API_BASE/api/tasks/$subtask_id" | jq -r '.parent_task_id // empty')
    if [[ "$parent" != "$TASK_ID" ]]; then
        echo "Error: Subtask $subtask_id does not belong to task $TASK_ID" >&2
        exit 1
    fi
    
    # Build JSON payload
    local json="{{"
    local first=true
    
    if [[ -n "$status" ]]; then
        json="$json\\"status\\": \\"$status\\""
        first=false
    fi
    
    if [[ -n "$links" ]]; then
        # Replace all links
        local links_json="["
        local lfirst=true
        IFS=',' read -ra URLS <<< "$links"
        for url in "${{URLS[@]}}"; do
            url=$(echo "$url" | xargs)
            if [[ -n "$url" ]]; then
                if [[ "$lfirst" != true ]]; then
                    links_json="$links_json,"
                fi
                links_json="$links_json{{\\"url\\": \\"$url\\", \\"type\\": \\"reference\\", \\"title\\": \\"$url\\"}}"
                lfirst=false
            fi
        done
        links_json="$links_json]"
        if [[ "$first" != true ]]; then
            json="$json, "
        fi
        json="$json\\"links\\": $links_json"
        first=false
    fi
    
    if [[ -n "$add_link" ]]; then
        # Fetch existing links and append
        local existing=$(curl -s "$API_BASE/api/tasks/$subtask_id" | jq -c '.links // []')
        local new_link="{{\\"url\\": \\"$add_link\\", \\"type\\": \\"reference\\", \\"title\\": \\"$add_link\\"}}"
        local updated_links=$(echo "$existing" | jq -c ". + [$new_link]")
        if [[ "$first" != true ]]; then
            json="$json, "
        fi
        json="$json\\"links\\": $updated_links"
        first=false
    fi
    
    json="$json}}"
    
    if [[ "$json" == "{{}}" ]]; then
        echo "Error: No updates specified" >&2
        exit 1
    fi
    
    curl -s -X PATCH "$API_BASE/api/tasks/$subtask_id" \\
        -H 'Content-Type: application/json' \\
        -d "$json" | jq .
}}

case "$1" in
    list)
        shift
        cmd_list "$@"
        ;;
    create)
        shift
        cmd_create "$@"
        ;;
    update)
        shift
        cmd_update "$@"
        ;;
    -h|--help|"")
        show_help
        ;;
    *)
        echo "Unknown command: $1" >&2
        show_help
        exit 1
        ;;
esac
'''

# Worker script doesn't need task_id - it only manages session status
ORCH_WORKER_SCRIPT = '''#!/bin/bash
# orch-worker: Manage worker session status
# Generated for worker: {worker_name}

SESSION_ID="{session_id}"
API_BASE="{api_base}"

show_help() {{
    echo "Usage: orch-worker <command> [options]"
    echo ""
    echo "Commands:"
    echo "  update --status STATUS    Update worker status (working|idle|waiting)"
    echo ""
    echo "Examples:"
    echo "  orch-worker update --status working"
    echo "  orch-worker update --status idle"
    echo "  orch-worker update --status waiting"
}}

cmd_update() {{
    local status=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --status)
                status="$2"
                shift 2
                ;;
            *)
                echo "Unknown option: $1" >&2
                exit 1
                ;;
        esac
    done
    
    if [[ -z "$status" ]]; then
        echo "Error: --status is required" >&2
        exit 1
    fi
    
    curl -s -X PATCH "$API_BASE/api/sessions/$SESSION_ID" \\
        -H 'Content-Type: application/json' \\
        -d "{{\\"status\\": \\"$status\\"}}" | jq .
}}

case "$1" in
    update)
        shift
        cmd_update "$@"
        ;;
    -h|--help|"")
        show_help
        ;;
    *)
        echo "Unknown command: $1" >&2
        show_help
        exit 1
        ;;
esac
'''

# Context script needs project_id from task - uses same caching mechanism
ORCH_CONTEXT_SCRIPT = SCRIPT_HEADER + '''
show_help() {{
    echo "Usage: orch-context <command> [options]"
    echo ""
    echo "Commands:"
    echo "  show --scope SCOPE        Show context (project|global)"
    echo "  tasks                     List all project tasks"
    echo ""
    echo "Options:"
    echo "  --refresh                 Force refresh task info from API"
    echo ""
    echo "Examples:"
    echo "  orch-context show --scope project"
    echo "  orch-context show --scope global"
    echo "  orch-context tasks"
}}

cmd_show() {{
    local scope=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --scope)
                scope="$2"
                shift 2
                ;;
            *)
                echo "Unknown option: $1" >&2
                exit 1
                ;;
        esac
    done
    
    if [[ -z "$scope" ]]; then
        echo "Error: --scope is required (project|global)" >&2
        exit 1
    fi
    
    case "$scope" in
        project)
            load_task_info "$FORCE_REFRESH" || exit 1
            curl -s "$API_BASE/api/context?project_id=$PROJECT_ID" | jq .
            ;;
        global)
            curl -s "$API_BASE/api/context?scope=global" | jq .
            ;;
        *)
            echo "Error: Invalid scope. Use 'project' or 'global'" >&2
            exit 1
            ;;
    esac
}}

cmd_tasks() {{
    load_task_info "$FORCE_REFRESH" || exit 1
    curl -s "$API_BASE/api/tasks?project_id=$PROJECT_ID" | jq .
}}

case "$1" in
    show)
        shift
        cmd_show "$@"
        ;;
    tasks)
        shift
        cmd_tasks "$@"
        ;;
    -h|--help|"")
        show_help
        ;;
    *)
        echo "Unknown command: $1" >&2
        show_help
        exit 1
        ;;
esac
'''


def generate_worker_scripts(
    worker_dir: str,
    worker_name: str,
    session_id: str,
    api_base: str = "http://127.0.0.1:8093",
) -> str:
    """Generate CLI scripts for a worker and return the bin directory path.
    
    Scripts fetch task_id and project_id dynamically from the API with caching.
    This allows workers to be created before task assignment.
    
    Args:
        worker_dir: Base directory for the worker (e.g., /tmp/orchestrator/workers/worker1)
        worker_name: Name of the worker
        session_id: Worker's session ID
        api_base: API base URL
        
    Returns:
        Path to the bin directory containing the scripts
    """
    bin_dir = os.path.join(worker_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    
    # Scripts with their descriptions (for header comment)
    scripts = {
        "orch-task": (ORCH_TASK_SCRIPT, "Manage the assigned task"),
        "orch-subtask": (ORCH_SUBTASK_SCRIPT, "Manage subtasks under the assigned task"),
        "orch-worker": (ORCH_WORKER_SCRIPT, "Manage worker session status"),
        "orch-context": (ORCH_CONTEXT_SCRIPT, "Read project and global context"),
    }
    
    for script_name, (template, description) in scripts.items():
        script_path = os.path.join(bin_dir, script_name)
        
        format_vars = {
            "script_name": script_name,
            "script_description": description,
            "worker_name": worker_name,
            "worker_dir": worker_dir,
            "session_id": session_id,
            "api_base": api_base,
        }
        
        content = template.format(**format_vars)
        
        with open(script_path, "w") as f:
            f.write(content)
        
        # Make executable
        os.chmod(script_path, os.stat(script_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    
    return bin_dir


def get_path_export_command(bin_dir: str) -> str:
    """Get the shell command to add bin_dir to PATH."""
    return f'export PATH="{bin_dir}:$PATH"'
