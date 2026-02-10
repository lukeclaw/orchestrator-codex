"""Generate CLI scripts for worker task management.

These scripts are placed in the worker's bin directory and provide a scoped
interface to the orchestrator API, preventing workers from affecting tasks
or sessions outside their scope.

Scripts use file-based caching for task info to minimize API calls.
Cache TTL is 5 minutes by default.
"""

import json
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

# Get file modification time (cross-platform: macOS and Linux)
get_file_mtime() {{
    local file="$1"
    if [[ "$(uname)" == "Darwin" ]]; then
        # macOS: stat -f %m returns modification time
        stat -f %m "$file" 2>/dev/null
    else
        # Linux: stat -c %Y returns modification time
        stat -c %Y "$file" 2>/dev/null
    fi
}}

# Load task info from cache or API
load_task_info() {{
    local force_refresh="$1"
    local cache_valid=false
    
    # Check if cache exists and is fresh
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
        # Load from cache
        source "$CACHE_FILE"
    else
        # Fetch tasks assigned to this session
        local http_code
        local tasks_json
        tasks_json=$(curl -s -w "\n%{{http_code}}" --connect-timeout 5 "$API_BASE/api/tasks?assigned_session_id=$SESSION_ID")
        http_code=$(echo "$tasks_json" | tail -n1)
        tasks_json=$(echo "$tasks_json" | sed '$d')
        
        # Check for connection errors (curl returns 000 for connection failures)
        if [[ "$http_code" == "000" || -z "$http_code" ]]; then
            echo "Error: Connection failed - cannot reach orchestrator API at $API_BASE" >&2
            return 1
        fi
        
        # Check for HTTP errors
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

# Helper: JSON-encode a string (handles newlines, quotes, backslashes, etc.)
json_encode() {{
    if command -v jq &> /dev/null; then
        printf '%s' "$1" | jq -Rs . | sed 's/^"//;s/"$//'
    else
        python3 -c "import json,sys; print(json.dumps(sys.stdin.read())[1:-1])" <<< "$1"
    fi
}}
'''

ORCH_TASK_SCRIPT = SCRIPT_HEADER + '''
show_help() {{
    echo "Usage: orch-task <command> [options]"
    echo ""
    echo "Commands:"
    echo "  show [--refresh]              Show task details"
    echo "  update [options]              Update task"
    echo ""
    echo "Update Options:"
    echo "  --status STATUS               Update status (in_progress|blocked) - workers cannot set done"
    echo "  --notes NOTES                 Update notes (findings, progress, observations)"
    echo "  --notes-stdin                 Read notes from stdin (for multi-line content)"
    echo ""
    echo "Options:"
    echo "  --refresh                     Force refresh task info from API"
    echo ""
    echo "Examples:"
    echo "  orch-task show"
    echo "  orch-task show --refresh"
    echo "  orch-task update --status in_progress"
    echo "  orch-task update --status blocked"
    echo "  orch-task update --notes \\"Found issue in auth module\\""
}}

cmd_show() {{
    load_task_info "$FORCE_REFRESH" || exit 1
    curl -s "$API_BASE/api/tasks/$TASK_ID" | jq .
}}

cmd_update() {{
    load_task_info "$FORCE_REFRESH" || exit 1
    
    local status="" notes="" notes_stdin=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --status)
                status="$2"
                shift 2
                ;;
            --notes)
                notes="$2"
                shift 2
                ;;
            --notes-stdin)
                notes_stdin="1"
                shift
                ;;
            *)
                echo "Unknown option: $1" >&2
                exit 1
                ;;
        esac
    done
    
    # Read notes from stdin if specified
    if [[ -n "$notes_stdin" ]]; then
        notes=$(cat)
    fi
    
    if [[ -z "$status" && -z "$notes" ]]; then
        echo "Error: --status or --notes is required" >&2
        exit 1
    fi
    
    # Workers cannot mark their own task as done - only the orchestrator brain can
    if [[ "$status" == "done" ]]; then
        echo "Error: Workers cannot mark tasks as done. Signal completion in your response and the orchestrator brain will review and mark done." >&2
        exit 1
    fi
    
    local json="{{"
    local first=true
    if [[ -n "$status" ]]; then
        json="$json\\"status\\": \\"$status\\""; first=false
    fi
    if [[ -n "$notes" ]]; then
        local escaped_notes=$(json_encode "$notes")
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"notes\\": \\"$escaped_notes\\""; first=false
    fi
    json="$json}}"
    
    curl -s -X PATCH "$API_BASE/api/tasks/$TASK_ID" \\
        -H 'Content-Type: application/json' \\
        -d "$json" | jq .
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
    echo "  update --id ID [options]  Update a subtask"
    echo ""
    echo "Create Options:"
    echo "  --description DESC        Subtask description"
    echo "  --description-stdin       Read description from stdin (for multi-line)"
    echo "  --links URLS              Comma-separated URLs"
    echo ""
    echo "Update Options:"
    echo "  --status STATUS           Update status"
    echo "  --notes NOTES             Update notes (findings, progress)"
    echo "  --notes-stdin             Read notes from stdin (for multi-line)"
    echo "  --links URLS              Replace all links (comma-separated)"
    echo "  --add-link URL            Add a link"
    echo "  --add-link-tag TAG        Tag for the link (e.g., PR, DOC)"
    echo ""
    echo "Examples:"
    echo "  orch-subtask list"
    echo "  orch-subtask create --title \\"Fix bug\\" --description \\"Details\\""
    echo "  orch-subtask create --title \\"Add tests\\" --links \\"http://pr1,http://doc1\\""
    echo "  orch-subtask update --id UUID --status done"
    echo "  orch-subtask update --id UUID --notes \\"Root cause found in config\\""
    echo "  orch-subtask update --id UUID --add-link \\"http://github.com/pr/123\\" --add-link-tag PR"
}}

cmd_list() {{
    load_task_info "$FORCE_REFRESH" || exit 1
    curl -s "$API_BASE/api/tasks/$TASK_ID/subtasks" | jq .
}}

cmd_create() {{
    load_task_info "$FORCE_REFRESH" || exit 1
    local title=""
    local description=""
    local description_stdin=""
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
            --description-stdin)
                description_stdin="1"
                shift
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
    
    # Read description from stdin if specified
    if [[ -n "$description_stdin" ]]; then
        description=$(cat)
    fi
    
    if [[ -z "$title" ]]; then
        echo "Error: --title is required" >&2
        exit 1
    fi
    
    # Build JSON payload
    local json="{{\\"project_id\\": \\"$PROJECT_ID\\", \\"parent_task_id\\": \\"$TASK_ID\\", \\"title\\": \\"$title\\""
    
    if [[ -n "$description" ]]; then
        local escaped_desc=$(json_encode "$description")
        json="$json, \\"description\\": \\"$escaped_desc\\""
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
                links_json="$links_json{{\\"url\\": \\"$url\\"}}"
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
    local notes=""
    local notes_stdin=""
    local links=""
    local add_link=""
    local add_link_tag=""
    
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
            --notes)
                notes="$2"
                shift 2
                ;;
            --notes-stdin)
                notes_stdin="1"
                shift
                ;;
            --links)
                links="$2"
                shift 2
                ;;
            --add-link)
                add_link="$2"
                shift 2
                ;;
            --add-link-tag)
                add_link_tag="$2"
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
    
    # Read notes from stdin if specified
    if [[ -n "$notes_stdin" ]]; then
        notes=$(cat)
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
    
    if [[ -n "$notes" ]]; then
        local escaped_notes=$(json_encode "$notes")
        if [[ "$first" != true ]]; then
            json="$json, "
        fi
        json="$json\\"notes\\": \\"$escaped_notes\\""
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
                links_json="$links_json{{\\"url\\": \\"$url\\"}}"
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
        local new_link="{{\\"url\\": \\"$add_link\\"}}"
        if [[ -n "$add_link_tag" ]]; then
            new_link="{{\\"url\\": \\"$add_link\\", \\"tag\\": \\"$add_link_tag\\"}}"
        fi
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
ORCH_NOTIFY_SCRIPT = SCRIPT_HEADER + '''
show_help() {{
    echo "Usage: orch-notify <message> [options]"
    echo ""
    echo "Create a non-blocking notification for the user."
    echo ""
    echo "Options:"
    echo "  --type TYPE             Notification type (info|pr_comment|warning), default: info"
    echo "  --link URL              Optional external link (e.g., PR URL)"
    echo ""
    echo "Examples:"
    echo "  orch-notify \\"PR merged, but reviewer asked about error handling\\""
    echo "  orch-notify \\"Found potential issue in config\\" --type warning"
    echo "  orch-notify \\"PR #123 merged with comments\\" --type pr_comment --link \\"https://github.com/...\\""
    echo ""
    echo "Guidelines:"
    echo "  - Use sparingly for valuable information that doesn't block your work"
    echo "  - Good: PR merged but reviewer left a question, found issue but proceeded safely"
    echo "  - Bad: Routine progress updates, status changes (use orch-task update instead)"
}}

# Main logic
if [[ $# -eq 0 || "$1" == "-h" || "$1" == "--help" ]]; then
    show_help
    exit 0
fi

# Parse arguments
MESSAGE=""
NOTIFY_TYPE="info"
LINK_URL=""

# First positional argument is the message
MESSAGE="$1"
shift

while [[ $# -gt 0 ]]; do
    case "$1" in
        --type)
            NOTIFY_TYPE="$2"
            shift 2
            ;;
        --link)
            LINK_URL="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1" >&2
            show_help
            exit 1
            ;;
    esac
done

if [[ -z "$MESSAGE" ]]; then
    echo "Error: Message is required" >&2
    show_help
    exit 1
fi

# Load task info to get task_id (notification will be linked to current task)
load_task_info "$FORCE_REFRESH"
# Note: We don't exit on failure - notification can be created without task_id

# Build JSON payload
escaped_message=$(json_encode "$MESSAGE")
json="{{\\"message\\": \\"$escaped_message\\", \\"session_id\\": \\"$SESSION_ID\\", \\"notification_type\\": \\"$NOTIFY_TYPE\\""

if [[ -n "$TASK_ID" && "$TASK_ID" != "null" ]]; then
    json="$json, \\"task_id\\": \\"$TASK_ID\\""
fi

if [[ -n "$LINK_URL" ]]; then
    json="$json, \\"link_url\\": \\"$LINK_URL\\""
fi

json="$json}}"

# Create notification
result=$(curl -s -X POST "$API_BASE/api/notifications" \\
    -H 'Content-Type: application/json' \\
    -d "$json")

echo "$result" | jq .

# Confirm to user
echo ""
echo "Notification created. User will see this in the dashboard."
'''

ORCH_CONTEXT_SCRIPT = SCRIPT_HEADER + '''
show_help() {{
    echo "Usage: orch-context <command> [options]"
    echo ""
    echo "Commands:"
    echo "  list --scope SCOPE        List context items with titles and descriptions (no full content)"
    echo "  read ID [ID2 ...]         Read full content of specific context item(s)"
    echo "  tasks                     List all project tasks"
    echo ""
    echo "Scopes:"
    echo "  project                   Context for assigned project"
    echo "  global                    Shared context across all projects"
    echo ""
    echo "Options:"
    echo "  --refresh                 Force refresh task info from API"
    echo ""
    echo "Examples:"
    echo "  orch-context list --scope project    # List project context (titles + descriptions)"
    echo "  orch-context list --scope global     # List global context (titles + descriptions)"
    echo "  orch-context read abc123             # Read full content of item abc123"
    echo "  orch-context read abc123 def456      # Read multiple items"
    echo "  orch-context tasks                   # List all project tasks"
    echo ""
    echo "Workflow: Use 'list' first to see available context, then 'read' to fetch full content"
    echo "          of relevant items. This saves your context window."
}}

cmd_list() {{
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
            # List without content (default behavior)
            curl -s "$API_BASE/api/context?project_id=$PROJECT_ID" | jq '.[] | {{id: .id, title: .title, description: .description, category: .category}}'
            ;;
        global)
            # List without content (default behavior)
            curl -s "$API_BASE/api/context?scope=global" | jq '.[] | {{id: .id, title: .title, description: .description, category: .category}}'
            ;;
        *)
            echo "Error: Invalid scope. Use 'project' or 'global'" >&2
            exit 1
            ;;
    esac
}}

cmd_read() {{
    if [[ $# -eq 0 ]]; then
        echo "Error: At least one context item ID is required" >&2
        echo "Usage: orch-context read ID [ID2 ...]" >&2
        exit 1
    fi
    
    # Fetch full content for each ID
    for item_id in "$@"; do
        echo "=== Context: $item_id ==="
        curl -s "$API_BASE/api/context/$item_id" | jq .
        echo ""
    done
}}

cmd_tasks() {{
    load_task_info "$FORCE_REFRESH" || exit 1
    curl -s "$API_BASE/api/tasks?project_id=$PROJECT_ID" | jq .
}}

case "$1" in
    list)
        shift
        cmd_list "$@"
        ;;
    read)
        shift
        cmd_read "$@"
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

# Single source of truth for worker CLI scripts
# Any code that needs to list/copy worker scripts should use this
WORKER_SCRIPTS = {
    "orch-task": (ORCH_TASK_SCRIPT, "Manage the assigned task"),
    "orch-subtask": (ORCH_SUBTASK_SCRIPT, "Manage subtasks under the assigned task"),
    "orch-worker": (ORCH_WORKER_SCRIPT, "Manage worker session status"),
    "orch-context": (ORCH_CONTEXT_SCRIPT, "Read project and global context"),
    "orch-notify": (ORCH_NOTIFY_SCRIPT, "Create non-blocking notification for user"),
}

# Export just the script names for code that only needs to iterate over names
WORKER_SCRIPT_NAMES = list(WORKER_SCRIPTS.keys())


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
    
    for script_name, (template, description) in WORKER_SCRIPTS.items():
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


def generate_hooks_settings(
    worker_dir: str,
    session_id: str,
    api_base: str = "http://127.0.0.1:8093",
) -> str:
    """Generate Claude Code hooks settings.json for automatic status management.
    
    Creates hooks/update-status.sh and settings.json in the given directory.
    The settings.json is meant to be loaded via `claude --settings <path>`.
    
    Directory structure created:
        worker_dir/
            hooks/
                update-status.sh
            settings.json
    
    Args:
        worker_dir: Directory to generate hooks in (e.g., tmp_dir/configs/)
        session_id: Worker's session ID
        api_base: API base URL
        
    Returns:
        Path to the directory containing settings.json
    """
    hooks_dir = os.path.join(worker_dir, "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    
    # Create hook script that updates worker status
    hook_script_path = os.path.join(hooks_dir, "update-status.sh")
    hook_script = f'''#!/bin/bash
# Hook script to update worker status in orchestrator
# Generated for session: {session_id}

SESSION_ID="{session_id}"
API_BASE="{api_base}"

# Read JSON input from stdin
INPUT=$(cat)

# Get the hook event name
EVENT=$(echo "$INPUT" | jq -r '.hook_event_name // empty')

# Determine status based on event
case "$EVENT" in
    SessionStart)
        STATUS="idle"
        ;;
    UserPromptSubmit)
        STATUS="working"
        ;;
    Stop|Notification)
        STATUS="waiting"
        ;;
    SessionEnd)
        # Claude Code exited - mark as disconnected
        STATUS="disconnected"
        ;;
    *)
        # Unknown event, do nothing
        exit 0
        ;;
esac

# Update worker status via API
curl -s -X PATCH "$API_BASE/api/sessions/$SESSION_ID" \\
    -H 'Content-Type: application/json' \\
    -d "{{\\"status\\": \\"$STATUS\\"}}" > /dev/null 2>&1

exit 0
'''
    
    with open(hook_script_path, "w") as f:
        f.write(hook_script)
    
    # Make executable
    os.chmod(hook_script_path, os.stat(hook_script_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    
    # Create settings.json with hooks configuration
    settings = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": hook_script_path
                        }
                    ]
                }
            ],
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
            ],
            "Stop": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": hook_script_path
                        }
                    ]
                }
            ],
            "Notification": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": hook_script_path
                        }
                    ]
                }
            ],
            "SessionEnd": [
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
    
    settings_path = os.path.join(worker_dir, "settings.json")
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
    
    return worker_dir
