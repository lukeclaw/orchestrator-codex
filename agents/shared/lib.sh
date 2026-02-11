#!/bin/bash
# Shared library for orchestrator CLI scripts
# Source this file in scripts: source "$(dirname "$0")/../../shared/lib.sh"

# Helper: JSON-encode a string (handles newlines, quotes, backslashes, etc.)
json_encode() {
    if command -v jq &> /dev/null; then
        printf '%s' "$1" | jq -Rs . | sed 's/^"//;s/"$//'
    else
        python3 -c "import json,sys; print(json.dumps(sys.stdin.read())[1:-1])" <<< "$1"
    fi
}

# Helper to pretty-print JSON if jq is available
pp() {
    if command -v jq &> /dev/null; then
        jq .
    else
        cat
    fi
}

# Get file modification time (cross-platform: macOS and Linux)
get_file_mtime() {
    local file="$1"
    if [[ "$(uname)" == "Darwin" ]]; then
        stat -f %m "$file" 2>/dev/null
    else
        stat -c %Y "$file" 2>/dev/null
    fi
}

# Helper to build JSON payload from key=value pairs
build_json() {
    local json="{"
    local first=true
    for arg in "$@"; do
        local key="${arg%%=*}"
        local value="${arg#*=}"
        if [[ "$first" != true ]]; then
            json="$json,"
        fi
        if [[ "$value" =~ ^[0-9]+$ ]] || [[ "$value" == "true" ]] || [[ "$value" == "false" ]] || [[ "$value" == "null" ]]; then
            json="$json\"$key\": $value"
        else
            local escaped_value=$(json_encode "$value")
            json="$json\"$key\": \"$escaped_value\""
        fi
        first=false
    done
    json="$json}"
    echo "$json"
}
