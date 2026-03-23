#!/bin/bash
# Safety gate for worker Bash commands.
#
# This hook runs on PreToolUse events and checks for known-dangerous commands.
# Instead of hard-blocking, it returns an "ask" decision so the user can
# approve or deny the command interactively. This works even when Claude Code
# runs with --dangerously-skip-permissions.
#
# Tier 1 checks (catastrophic / irreversible):
#   1. Broad recursive deletion (rm -rf /, ~, ..)
#   2. SQL: DROP TABLE/DATABASE/SCHEMA
#   3. SQL: DELETE FROM without WHERE
#   4. Disk/filesystem destruction (mkfs, dd to device)
#   5. Fork bombs
#   6. chmod 777 on system paths
#   7. kubectl delete/apply targeting production namespace
#   8. PR creation (gh pr create) without --draft flag

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')

# Only gate Bash commands
if [ "$TOOL_NAME" != "Bash" ]; then
    exit 0
fi

CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
REASON=""

# 1. Broad recursive deletion at root, home, or parent directory
if echo "$CMD" | grep -qE '\brm\b' && echo "$CMD" | grep -qE '\s-[a-zA-Z]*[rR]'; then
    if echo "$CMD" | grep -qE '\s(/\*?|~/?\*?|\.\.)\s*($|[;&|])'; then
        REASON="Recursive delete targeting root (/), home (~), or parent (..) directory"
    fi
fi

# 2. SQL: DROP TABLE/DATABASE/SCHEMA
if [ -z "$REASON" ] && echo "$CMD" | grep -qiE 'DROP\s+(TABLE|DATABASE|SCHEMA)'; then
    REASON="DROP TABLE/DATABASE/SCHEMA is a destructive SQL operation"
fi

# 3. SQL: DELETE FROM without WHERE
if [ -z "$REASON" ] && echo "$CMD" | grep -qiE 'DELETE\s+FROM'; then
    if ! echo "$CMD" | grep -qiE 'WHERE'; then
        REASON="DELETE FROM without WHERE clause"
    fi
fi

# 4. Disk/filesystem destruction (dd to device, mkfs, redirect to block device)
if [ -z "$REASON" ] && echo "$CMD" | grep -qE '\bmkfs\.|\bdd\s+.*of=/dev/|>\s*/dev/sd'; then
    REASON="Disk or filesystem destructive command"
fi

# 5. Fork bomb
if [ -z "$REASON" ] && echo "$CMD" | grep -qE ':\(\)\s*\{.*\|.*&'; then
    REASON="Fork bomb pattern detected"
fi

# 6. chmod 777 on system paths
if [ -z "$REASON" ] && echo "$CMD" | grep -qE '\bchmod\b.*\b777\b.*\s/'; then
    REASON="chmod 777 on system path"
fi

# 7. kubectl delete/apply targeting production namespace
if [ -z "$REASON" ] && echo "$CMD" | grep -qE '\bkubectl\s+(delete|apply)\b'; then
    if echo "$CMD" | grep -qiE '(-n\s+prod|--namespace[= ]prod)\b'; then
        REASON="kubectl destructive operation targeting production namespace"
    fi
fi

# 8. PR creation without --draft flag
if [ -z "$REASON" ] && echo "$CMD" | grep -qE '\bgh\s+pr\s+create\b'; then
    if ! echo "$CMD" | grep -qE '\-\-draft\b'; then
        REASON="PR creation must use --draft flag (gh pr create --draft)"
    fi
fi

if [ -n "$REASON" ]; then
    jq -n --arg reason "$REASON" '{
        hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "ask",
            permissionDecisionReason: $reason
        }
    }'
fi

exit 0
