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

# Helper: JSON-encode a string (handles newlines, quotes, backslashes, etc.)
json_encode() {{
    if command -v jq &> /dev/null; then
        printf '%s' "$1" | jq -Rs . | sed 's/^"//;s/"$//'
    else
        python3 -c "import json,sys; print(json.dumps(sys.stdin.read())[1:-1])" <<< "$1"
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
            # Escape string value for JSON safety
            local escaped_value=$(json_encode "$value")
            json="$json\\"$key\\": \\"$escaped_value\\""
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
    echo "  list [options]                List all workers"
    echo "  rdevs [--refresh]             List available rdev instances"
    echo "  show <id>                     Show worker details"
    echo "  create [options]              Create a new worker"
    echo "  delete <id>                   Delete a worker"
    echo "  stop <id>                     Stop worker: Escape, /clear, unassign task, set idle"
    echo "  reconnect <id>                Reconnect a disconnected worker"
    echo ""
    echo "List Options:"
    echo "  --status STATUS               Filter by status (idle, working, waiting, error, etc.)"
    echo ""
    echo "Create Options:"
    echo "  --name NAME                   Worker name (required)"
    echo "  --host HOST                   Host: 'localhost' or rdev path like 'subs-mt/name' (default: localhost)"
    echo "  --work-dir DIR                Working directory for the worker"
    echo "  --task-id ID                  Task ID to assign to the worker"
    echo ""
    echo "Examples:"
    echo "  orch-workers list"
    echo "  orch-workers list --status waiting"
    echo "  orch-workers rdevs                # List available rdev VMs"
    echo "  orch-workers rdevs --refresh     # Force refresh rdev list"
    echo "  orch-workers show abc123"
    echo "  orch-workers create --name api-worker"
    echo "  orch-workers create --name ui-worker --host localhost --work-dir /path/to/repo"
    echo "  orch-workers create --name rdev-worker --host subs-mt/sleepy-franklin"
    echo "  orch-workers delete abc123"
}}

cmd_list() {{
    local status=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --status) status="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    
    local url="$API_BASE/api/sessions?session_type=worker"
    [[ -n "$status" ]] && url="$url&status=$status"
    
    curl -s "$url" | pp
}}

cmd_rdevs() {{
    local refresh=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --refresh) refresh="?refresh=true"; shift ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    
    # Fetch rdevs and format output
    curl -s "$API_BASE/api/rdevs$refresh" | jq -r '.[] | "\\(.name)\\t\\(.state)\\t\\(if .in_use then "IN USE (\\(.worker_name // "unknown"))" else "available" end)"' | column -t -s $'\\t'
}}

cmd_show() {{
    local id="$1"
    if [[ -z "$id" ]]; then
        echo "Error: Worker ID required" >&2
        exit 1
    fi
    curl -s "$API_BASE/api/sessions/$id" | pp
}}

cmd_create() {{
    local name="" host="localhost" work_dir="" task_id=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --name) name="$2"; shift 2 ;;
            --host) host="$2"; shift 2 ;;
            --work-dir) work_dir="$2"; shift 2 ;;
            --task-id) task_id="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    
    if [[ -z "$name" ]]; then
        echo "Error: --name is required" >&2
        exit 1
    fi
    
    local escaped_name=$(json_encode "$name")
    local escaped_host=$(json_encode "$host")
    local json="{{\\"name\\": \\"$escaped_name\\", \\"host\\": \\"$escaped_host\\""
    if [[ -n "$work_dir" ]]; then
        local escaped_work_dir=$(json_encode "$work_dir")
        json="$json, \\"work_dir\\": \\"$escaped_work_dir\\""
    fi
    if [[ -n "$task_id" ]]; then
        local escaped_task_id=$(json_encode "$task_id")
        json="$json, \\"task_id\\": \\"$escaped_task_id\\""
    fi
    json="$json}}"
    
    curl -s -X POST "$API_BASE/api/sessions" \\
        -H 'Content-Type: application/json' \\
        -d "$json" | pp
}}

cmd_delete() {{
    local id="$1"
    if [[ -z "$id" ]]; then
        echo "Error: Worker ID required" >&2
        exit 1
    fi
    curl -s -X DELETE "$API_BASE/api/sessions/$id" | pp
}}

cmd_stop() {{
    local id="$1"
    if [[ -z "$id" ]]; then
        echo "Error: Worker ID required" >&2
        exit 1
    fi
    curl -s -X POST "$API_BASE/api/sessions/$id/stop" | pp
}}

cmd_reconnect() {{
    local id="$1"
    if [[ -z "$id" ]]; then
        echo "Error: Worker ID required" >&2
        exit 1
    fi
    curl -s -X POST "$API_BASE/api/sessions/$id/reconnect" | pp
}}

case "$1" in
    list) shift; cmd_list "$@" ;;
    rdevs) shift; cmd_rdevs "$@" ;;
    show) shift; cmd_show "$@" ;;
    create) shift; cmd_create "$@" ;;
    delete) shift; cmd_delete "$@" ;;
    stop) shift; cmd_stop "$@" ;;
    reconnect) shift; cmd_reconnect "$@" ;;
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
    echo "  list [options]                List all projects"
    echo "  show <id>                     Show project details"
    echo "  create [options]              Create a new project"
    echo "  update <id> [options]         Update a project"
    echo ""
    echo "List Options:"
    echo "  --status STATUS               Filter by status (active|completed|archived)"
    echo "  --no-stats                    Exclude task/worker stats from output"
    echo ""
    echo "Create/Update Options:"
    echo "  --name NAME                   Project name (required for create)"
    echo "  --description DESC            Project description"
    echo "  --status STATUS               Project status (active|completed|archived)"
    echo "  --task-prefix PREFIX          Task key prefix (e.g., UTI)"
    echo ""
    echo "Examples:"
    echo "  orch-projects list"
    echo "  orch-projects list --status active"
    echo "  orch-projects create --name \\"Auth Migration\\" --description \\"Migrate to OAuth 2.0\\""
    echo "  orch-projects update abc123 --status completed"
}}

cmd_list() {{
    local status="" include_stats="true"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --status) status="$2"; shift 2 ;;
            --no-stats) include_stats="false"; shift ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    
    local url="$API_BASE/api/projects?include_stats=$include_stats"
    [[ -n "$status" ]] && url="$url&status=$status"
    
    curl -s "$url" | pp
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
    
    local escaped_name=$(json_encode "$name")
    local json="{{\\"name\\": \\"$escaped_name\\""
    if [[ -n "$description" ]]; then
        local escaped_desc=$(json_encode "$description")
        json="$json, \\"description\\": \\"$escaped_desc\\""
    fi
    if [[ -n "$status" ]]; then
        local escaped_status=$(json_encode "$status")
        json="$json, \\"status\\": \\"$escaped_status\\""
    fi
    if [[ -n "$task_prefix" ]]; then
        local escaped_prefix=$(json_encode "$task_prefix")
        json="$json, \\"task_prefix\\": \\"$escaped_prefix\\""
    fi
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
        local escaped_name=$(json_encode "$name")
        json="$json\\"name\\": \\"$escaped_name\\""; first=false
    fi
    if [[ -n "$description" ]]; then
        local escaped_desc=$(json_encode "$description")
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"description\\": \\"$escaped_desc\\""; first=false
    fi
    if [[ -n "$status" ]]; then
        local escaped_status=$(json_encode "$status")
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"status\\": \\"$escaped_status\\""; first=false
    fi
    if [[ -n "$task_prefix" ]]; then
        local escaped_prefix=$(json_encode "$task_prefix")
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"task_prefix\\": \\"$escaped_prefix\\""
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
    echo "  list [options]                        List tasks"
    echo "  show <id>                             Show task details"
    echo "  create [options]                      Create a new task"
    echo "  update <id> [options]                 Update a task"
    echo "  delete <id>                           Delete a task"
    echo "  assign <task-id> <worker-id>          Assign task to worker"
    echo "  unassign <task-id>                    Unassign task from worker"
    echo ""
    echo "List Options:"
    echo "  --project-id ID               Filter by project ID"
    echo "  --status STATUS               Filter by status (comma-separated: todo,in_progress)"
    echo "  --exclude-status STATUS       Exclude status (comma-separated: done,blocked)"
    echo "  --assigned ID                 Filter by assigned worker ID"
    echo "  --format FORMAT               Output format: json (default) or table"
    echo "  --stats                       Show status counts only"
    echo ""
    echo "Create/Update Options:"
    echo "  --project-id ID               Project ID (required for create)"
    echo "  --title TITLE                 Task title (required for create)"
    echo "  --description DESC            Task description"
    echo "  --description-stdin           Read description from stdin (for multi-line)"
    echo "  --notes NOTES                 Task notes (findings, progress, observations)"
    echo "  --notes-stdin                 Read notes from stdin (for multi-line)"
    echo "  --status STATUS               Task status (todo|in_progress|done|blocked)"
    echo "  --priority PRIORITY           Priority (high|medium|low)"
    echo "  --parent-id ID                Parent task ID (for subtasks)"
    echo "  --add-link URL                Add a link to the task"
    echo "  --add-link-tag TAG            Tag for the link (e.g., PR, PRD, DOC)"
    echo "  --clear-links                 Remove all links (can combine with --add-link to replace)"
    echo ""
    echo "Examples:"
    echo "  orch-tasks list --status todo,in_progress"
    echo "  orch-tasks list --exclude-status done"
    echo "  orch-tasks list --project-id abc123 --format table"
    echo "  orch-tasks list --stats"
    echo "  orch-tasks create --project-id abc123 --title \\"Add OAuth callback\\" --priority high"
    echo "  orch-tasks assign task123 worker456"
    echo "  orch-tasks update task123 --status done"
}}

cmd_list() {{
    local project_id="" status="" exclude_status="" assigned="" format="json" stats=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --project-id) project_id="$2"; shift 2 ;;
            --status) status="$2"; shift 2 ;;
            --exclude-status) exclude_status="$2"; shift 2 ;;
            --assigned) assigned="$2"; shift 2 ;;
            --format) format="$2"; shift 2 ;;
            --stats) stats="1"; shift ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    
    local url="$API_BASE/api/tasks"
    local sep="?"
    [[ -n "$project_id" ]] && url="$url${{sep}}project_id=$project_id" && sep="&"
    [[ -n "$status" ]] && url="$url${{sep}}status=$status" && sep="&"
    [[ -n "$exclude_status" ]] && url="$url${{sep}}exclude_status=$exclude_status" && sep="&"
    [[ -n "$assigned" ]] && url="$url${{sep}}assigned_session_id=$assigned"
    
    local result=$(curl -s "$url")
    
    if [[ -n "$stats" ]]; then
        # Show status counts only
        echo "$result" | jq -r 'group_by(.status) | map({{status: .[0].status, count: length}}) | .[] | "\\(.status): \\(.count)"'
    elif [[ "$format" == "table" ]]; then
        # Compact table output
        echo "$result" | jq -r '["KEY", "STATUS", "ASSIGNED", "TITLE"], ["---", "------", "--------", "-----"], (.[] | [(.task_key // .id[0:8]), .status, (.assigned_session_id[0:8] // "-"), .title[0:50]]) | @tsv' | column -t -s $'\\t'
    else
        # Default JSON output
        echo "$result" | pp
    fi
}}

cmd_show() {{
    local id="$1"
    if [[ -z "$id" ]]; then
        echo "Error: Task ID required" >&2
        exit 1
    fi
    curl -s "$API_BASE/api/tasks/$id" | pp
}}

# Helper: JSON-encode a string (handles newlines, quotes, backslashes, etc.)
json_encode() {{
    if command -v jq &> /dev/null; then
        # jq -Rs reads raw input, outputs JSON string (with quotes) - strip the quotes
        printf '%s' "$1" | jq -Rs . | sed 's/^"//;s/"$//'
    else
        # Fallback to Python if jq not available
        python3 -c "import json,sys; print(json.dumps(sys.stdin.read())[1:-1])" <<< "$1"
    fi
}}

cmd_create() {{
    local project_id="" title="" description="" description_stdin="" status="" priority="" parent_id=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --project-id) project_id="$2"; shift 2 ;;
            --title) title="$2"; shift 2 ;;
            --description) description="$2"; shift 2 ;;
            --description-stdin) description_stdin="1"; shift ;;
            --status) status="$2"; shift 2 ;;
            --priority) priority="$2"; shift 2 ;;
            --parent-id) parent_id="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    
    # Read description from stdin if specified
    if [[ -n "$description_stdin" ]]; then
        description=$(cat)
    fi
    
    if [[ -z "$project_id" ]]; then
        echo "Error: --project-id is required" >&2
        exit 1
    fi
    if [[ -z "$title" ]]; then
        echo "Error: --title is required" >&2
        exit 1
    fi
    
    local escaped_title=$(json_encode "$title")
    local json="{{\\"project_id\\": \\"$project_id\\", \\"title\\": \\"$escaped_title\\""
    if [[ -n "$description" ]]; then
        local escaped_desc=$(json_encode "$description")
        json="$json, \\"description\\": \\"$escaped_desc\\""
    fi
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
    
    local title="" description="" description_stdin="" notes="" notes_stdin="" status="" priority="" add_link="" add_link_tag="" clear_links=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --title) title="$2"; shift 2 ;;
            --description) description="$2"; shift 2 ;;
            --description-stdin) description_stdin="1"; shift ;;
            --notes) notes="$2"; shift 2 ;;
            --notes-stdin) notes_stdin="1"; shift ;;
            --status) status="$2"; shift 2 ;;
            --priority) priority="$2"; shift 2 ;;
            --add-link) add_link="$2"; shift 2 ;;
            --add-link-tag) add_link_tag="$2"; shift 2 ;;
            --clear-links) clear_links="1"; shift ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    
    # Read from stdin if specified
    if [[ -n "$description_stdin" ]]; then
        description=$(cat)
    fi
    if [[ -n "$notes_stdin" ]]; then
        notes=$(cat)
    fi
    
    local json="{{"
    local first=true
    if [[ -n "$title" ]]; then
        local escaped_title=$(json_encode "$title")
        json="$json\\"title\\": \\"$escaped_title\\""; first=false
    fi
    if [[ -n "$description" ]]; then
        local escaped_desc=$(json_encode "$description")
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"description\\": \\"$escaped_desc\\""; first=false
    fi
    if [[ -n "$notes" ]]; then
        local escaped_notes=$(json_encode "$notes")
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"notes\\": \\"$escaped_notes\\""; first=false
    fi
    if [[ -n "$status" ]]; then
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"status\\": \\"$status\\""; first=false
    fi
    if [[ -n "$priority" ]]; then
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"priority\\": \\"$priority\\""; first=false
    fi
    if [[ -n "$clear_links" || -n "$add_link" ]]; then
        local updated_links="[]"
        if [[ -z "$clear_links" && -n "$add_link" ]]; then
            # Just adding - fetch existing links first
            updated_links=$(curl -s "$API_BASE/api/tasks/$id" | jq -c '.links // []')
        fi
        if [[ -n "$add_link" ]]; then
            local escaped_link=$(json_encode "$add_link")
            local new_link="{{\\"url\\": \\"$escaped_link\\"}}"
            if [[ -n "$add_link_tag" ]]; then
                local escaped_tag=$(json_encode "$add_link_tag")
                new_link="{{\\"url\\": \\"$escaped_link\\", \\"tag\\": \\"$escaped_tag\\"}}"
            fi
            updated_links=$(echo "$updated_links" | jq -c ". + [$new_link]")
        fi
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

cmd_delete() {{
    local id="$1"
    if [[ -z "$id" ]]; then
        echo "Error: Task ID required" >&2
        exit 1
    fi
    curl -s -X DELETE "$API_BASE/api/tasks/$id" | pp
}}

case "$1" in
    list) shift; cmd_list "$@" ;;
    show) shift; cmd_show "$@" ;;
    create) shift; cmd_create "$@" ;;
    update) shift; cmd_update "$@" ;;
    delete) shift; cmd_delete "$@" ;;
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
    echo "  --content-file FILE           Read content from file (safer for special chars)"
    echo "  --content-stdin               Read content from stdin (best for LLM agents)"
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

# Helper: JSON-encode a string (handles newlines, quotes, backslashes, etc.)
json_encode() {{
    if command -v jq &> /dev/null; then
        # jq -Rs reads raw input, outputs JSON string (with quotes) - strip the quotes
        printf '%s' "$1" | jq -Rs . | sed 's/^"//;s/"$//'
    else
        # Fallback to Python if jq not available
        python3 -c "import json,sys; print(json.dumps(sys.stdin.read())[1:-1])" <<< "$1"
    fi
}}

cmd_create() {{
    local title="" content="" content_file="" content_stdin="" description="" scope="global" project_id="" category=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --title) title="$2"; shift 2 ;;
            --content) content="$2"; shift 2 ;;
            --content-file) content_file="$2"; shift 2 ;;
            --content-stdin) content_stdin="1"; shift ;;
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
    # Read content from stdin if specified
    if [[ -n "$content_stdin" ]]; then
        content=$(cat)
    # Read content from file if specified
    elif [[ -n "$content_file" ]]; then
        if [[ ! -f "$content_file" ]]; then
            echo "Error: Content file not found: $content_file" >&2
            exit 1
        fi
        content=$(cat "$content_file")
    fi
    
    if [[ -z "$content" ]]; then
        echo "Error: --content, --content-file, or --content-stdin is required" >&2
        exit 1
    fi
    if [[ "$scope" == "project" ]] && [[ -z "$project_id" ]]; then
        echo "Error: --project-id is required when scope=project" >&2
        exit 1
    fi
    
    # JSON-encode content (handles newlines, quotes, backslashes, etc.)
    local escaped_content=$(json_encode "$content")
    local escaped_title=$(json_encode "$title")
    
    local json="{{\\"title\\": \\"$escaped_title\\", \\"content\\": \\"$escaped_content\\", \\"scope\\": \\"$scope\\", \\"source\\": \\"brain\\""
    if [[ -n "$description" ]]; then
        local escaped_desc=$(json_encode "$description")
        json="$json, \\"description\\": \\"$escaped_desc\\""
    fi
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
    
    local title="" content="" content_file="" content_stdin="" description="" category=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --title) title="$2"; shift 2 ;;
            --content) content="$2"; shift 2 ;;
            --content-file) content_file="$2"; shift 2 ;;
            --content-stdin) content_stdin="1"; shift ;;
            --description) description="$2"; shift 2 ;;
            --category) category="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    
    local json="{{"
    local first=true
    if [[ -n "$title" ]]; then
        local escaped_title=$(json_encode "$title")
        json="$json\\"title\\": \\"$escaped_title\\""; first=false
    fi
    # Read content from stdin if specified
    if [[ -n "$content_stdin" ]]; then
        content=$(cat)
    # Read content from file if specified
    elif [[ -n "$content_file" ]]; then
        if [[ ! -f "$content_file" ]]; then
            echo "Error: Content file not found: $content_file" >&2
            exit 1
        fi
        content=$(cat "$content_file")
    fi
    if [[ -n "$content" ]]; then
        local escaped_content=$(json_encode "$content")
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"content\\": \\"$escaped_content\\""; first=false
    fi
    if [[ -n "$description" ]]; then
        local escaped_desc=$(json_encode "$description")
        [[ "$first" != true ]] && json="$json, "
        json="$json\\"description\\": \\"$escaped_desc\\""; first=false
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
escaped_message=$(json_encode "$message")
curl -s -X POST "$API_BASE/api/sessions/$worker_id/send" \\
    -H 'Content-Type: application/json' \\
    -d "{{\\"message\\": \\"$escaped_message\\"}}" | pp
'''

# ============================================================================
# orch-notifications: Manage notifications
# ============================================================================
BRAIN_NOTIFICATIONS_SCRIPT = BRAIN_SCRIPT_HEADER + '''
show_help() {{
    echo "Usage: orch-notifications <command> [options]"
    echo ""
    echo "Commands:"
    echo "  list [options]                List notifications"
    echo "  dismiss <id>                  Dismiss a notification"
    echo "  dismiss-all                   Dismiss all notifications"
    echo "  delete <id>                   Permanently delete a notification"
    echo "  create [options]              Create a notification"
    echo ""
    echo "List Options:"
    echo "  --all                         Include dismissed notifications"
    echo "  --task-id ID                  Filter by task ID"
    echo "  --session-id ID               Filter by session/worker ID"
    echo "  --limit N                     Limit number of results"
    echo ""
    echo "Create Options:"
    echo "  --message MSG                 Notification message (required)"
    echo "  --task-id ID                  Link to task"
    echo "  --session-id ID               Link to session/worker"
    echo "  --type TYPE                   Type: info|pr_comment|warning (default: info)"
    echo "  --link URL                    External link"
    echo ""
    echo "Examples:"
    echo "  orch-notifications list"
    echo "  orch-notifications list --all"
    echo "  orch-notifications list --task-id abc123"
    echo "  orch-notifications list --session-id worker123 --limit 10"
    echo "  orch-notifications dismiss xyz789"
    echo "  orch-notifications dismiss-all"
    echo "  orch-notifications create --message \\"Review needed\\" --task-id abc123 --type pr_comment"
}}

cmd_list() {{
    local all="" task_id="" session_id="" limit=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --all) all="true"; shift ;;
            --task-id) task_id="$2"; shift 2 ;;
            --session-id) session_id="$2"; shift 2 ;;
            --limit) limit="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    
    local url="$API_BASE/api/notifications"
    local sep="?"
    if [[ -z "$all" ]]; then
        url="$url${{sep}}dismissed=false"
        sep="&"
    fi
    [[ -n "$task_id" ]] && url="$url${{sep}}task_id=$task_id" && sep="&"
    [[ -n "$session_id" ]] && url="$url${{sep}}session_id=$session_id" && sep="&"
    [[ -n "$limit" ]] && url="$url${{sep}}limit=$limit"
    
    curl -s "$url" | pp
}}

cmd_dismiss() {{
    local id="$1"
    if [[ -z "$id" ]]; then
        echo "Error: Notification ID required" >&2
        exit 1
    fi
    curl -s -X POST "$API_BASE/api/notifications/$id/dismiss" | pp
}}

cmd_dismiss_all() {{
    curl -s -X POST "$API_BASE/api/notifications/dismiss-all" \\
        -H 'Content-Type: application/json' \\
        -d '{{}}' | pp
}}

cmd_create() {{
    local message="" task_id="" session_id="" type="info" link=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --message) message="$2"; shift 2 ;;
            --task-id) task_id="$2"; shift 2 ;;
            --session-id) session_id="$2"; shift 2 ;;
            --type) type="$2"; shift 2 ;;
            --link) link="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done
    
    if [[ -z "$message" ]]; then
        echo "Error: --message is required" >&2
        exit 1
    fi
    
    local escaped_message=$(json_encode "$message")
    local json="{{\"message\": \"$escaped_message\", \"notification_type\": \"$type\""
    [[ -n "$task_id" ]] && json="$json, \"task_id\": \"$task_id\""
    [[ -n "$session_id" ]] && json="$json, \"session_id\": \"$session_id\""
    if [[ -n "$link" ]]; then
        local escaped_link=$(json_encode "$link")
        json="$json, \"link_url\": \"$escaped_link\""
    fi
    json="$json}}"
    
    curl -s -X POST "$API_BASE/api/notifications" \\
        -H 'Content-Type: application/json' \\
        -d "$json" | pp
}}

cmd_delete() {{
    local id="$1"
    if [[ -z "$id" ]]; then
        echo "Error: Notification ID required" >&2
        exit 1
    fi
    curl -s -X DELETE "$API_BASE/api/notifications/$id" | pp
}}

case "$1" in
    list) shift; cmd_list "$@" ;;
    dismiss) shift; cmd_dismiss "$@" ;;
    dismiss-all) shift; cmd_dismiss_all "$@" ;;
    delete) shift; cmd_delete "$@" ;;
    create) shift; cmd_create "$@" ;;
    -h|--help|"") show_help ;;
    *) echo "Unknown command: $1" >&2; show_help; exit 1 ;;
esac
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
        "orch-notifications": (BRAIN_NOTIFICATIONS_SCRIPT, "Manage notifications"),
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
    
    # Create settings.json with hooks configuration and command allowlist
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
        },
        # Auto-approve all orch- CLI commands and common safe commands
        # Note: Claude Code requires tool names to start with uppercase
        "permissions": {
            "allow": [
                "Bash(orch-*)",           # All orchestrator CLI commands
                "Bash(tmux capture-pane*)",  # Capture worker output
                "Bash(tmux send-keys*)",     # Send messages to workers
                "Bash(curl *127.0.0.1:8093*)",  # API calls to orchestrator
                "Bash(jq *)",             # JSON processing
            ]
        }
    }
    
    settings_path = os.path.join(brain_dir, "settings.json")
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
    
    return settings_path
