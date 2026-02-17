#!/bin/bash
# Safety gate for worker Bash commands.
#
# This hook runs on PreToolUse events and blocks known-dangerous commands
# by outputting {"decision": "block", "reason": "..."} to stdout.
#
# Tier 1 checks (catastrophic / irreversible):
#   1. Broad recursive deletion (rm -rf /, ~, ..)
#   2. SQL: DROP TABLE/DATABASE/SCHEMA
#   3. SQL: DELETE FROM without WHERE
#   4. Disk/filesystem destruction (mkfs, dd to device)
#   5. Fork bombs
#   6. chmod 777 on system paths
#   7. kubectl delete/apply targeting production namespace

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')

# Only gate Bash commands
if [ "$TOOL_NAME" != "Bash" ]; then
    exit 0
fi

CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
BLOCK=""

# 1. Broad recursive deletion at root, home, or parent directory
if echo "$CMD" | grep -qE '\brm\b' && echo "$CMD" | grep -qE '\s-[a-zA-Z]*[rR]'; then
    if echo "$CMD" | grep -qE '\s(/\*?|~/?\*?|\.\.)\s*($|[;&|])'; then
        BLOCK="Blocked: recursive delete targeting root (/), home (~), or parent (..) directory"
    fi
fi

# 2. SQL: DROP TABLE/DATABASE/SCHEMA
if [ -z "$BLOCK" ] && echo "$CMD" | grep -qiE 'DROP\s+(TABLE|DATABASE|SCHEMA)'; then
    BLOCK="Blocked: DROP TABLE/DATABASE/SCHEMA is a destructive SQL operation"
fi

# 3. SQL: DELETE FROM without WHERE
if [ -z "$BLOCK" ] && echo "$CMD" | grep -qiE 'DELETE\s+FROM'; then
    if ! echo "$CMD" | grep -qiE 'WHERE'; then
        BLOCK="Blocked: DELETE FROM without WHERE clause"
    fi
fi

# 4. Disk/filesystem destruction (dd to device, mkfs, redirect to block device)
if [ -z "$BLOCK" ] && echo "$CMD" | grep -qE '\bmkfs\.|\bdd\s+.*of=/dev/|>\s*/dev/sd'; then
    BLOCK="Blocked: disk or filesystem destructive command"
fi

# 5. Fork bomb
if [ -z "$BLOCK" ] && echo "$CMD" | grep -qE ':\(\)\s*\{.*\|.*&'; then
    BLOCK="Blocked: fork bomb pattern detected"
fi

# 6. chmod 777 on system paths
if [ -z "$BLOCK" ] && echo "$CMD" | grep -qE '\bchmod\b.*\b777\b.*\s/'; then
    BLOCK="Blocked: chmod 777 on system path"
fi

# 7. kubectl delete/apply targeting production namespace
if [ -z "$BLOCK" ] && echo "$CMD" | grep -qE '\bkubectl\s+(delete|apply)\b'; then
    if echo "$CMD" | grep -qiE '(-n\s+prod|--namespace[= ]prod)\b'; then
        BLOCK="Blocked: kubectl destructive operation targeting production namespace"
    fi
fi

if [ -n "$BLOCK" ]; then
    echo "{\"decision\": \"block\", \"reason\": \"$BLOCK\"}"
fi

exit 0
