#!/bin/bash
# Worker CLI library - shared functions for worker scripts
# Source this file: source "$(dirname "$0")/lib.sh"

# Required environment variables
: "${ORCH_SESSION_ID:?ORCH_SESSION_ID required}"
: "${ORCH_API_BASE:=http://127.0.0.1:8093}"
: "${ORCH_WORKER_DIR:?ORCH_WORKER_DIR required}"

# Aliases for cleaner script code
SESSION_ID="$ORCH_SESSION_ID"
API_BASE="$ORCH_API_BASE"
WORKER_DIR="$ORCH_WORKER_DIR"
CACHE_FILE="$WORKER_DIR/.task_cache"
CACHE_TTL=300  # 5 minutes in seconds

# Get file modification time (cross-platform: macOS and Linux)
get_file_mtime() {
    local file="$1"
    if [[ "$(uname)" == "Darwin" ]]; then
        stat -f %m "$file" 2>/dev/null
    else
        stat -c %Y "$file" 2>/dev/null
    fi
}

# Load task info from cache or API
load_task_info() {
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
        source "$CACHE_FILE"
    else
        local http_code
        local tasks_json
        tasks_json=$(curl -s -w "\n%{http_code}" --connect-timeout 5 "$API_BASE/api/tasks?assigned_session_id=$SESSION_ID")
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
}

# Helper: JSON-encode a string (handles newlines, quotes, backslashes, etc.)
json_encode() {
    if command -v jq &> /dev/null; then
        printf '%s' "$1" | jq -Rs . | sed 's/^"//;s/"$//'
    else
        python3 -c "import json,sys; print(json.dumps(sys.stdin.read())[1:-1])" <<< "$1"
    fi
}

# Check for --refresh flag in any position and filter it out
FORCE_REFRESH="false"
FILTERED_ARGS=()
for arg in "$@"; do
    if [[ "$arg" == "--refresh" ]]; then
        FORCE_REFRESH="true"
    else
        FILTERED_ARGS+=("$arg")
    fi
done
set -- "${FILTERED_ARGS[@]}"
