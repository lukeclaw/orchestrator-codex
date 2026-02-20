#!/bin/bash
# Hook script to update worker status in orchestrator

SESSION_ID="{{SESSION_ID}}"
API_BASE="{{API_BASE}}"

INPUT=$(cat)
EVENT=$(echo "$INPUT" | jq -r '.hook_event_name // empty')
CLAUDE_SID=$(echo "$INPUT" | jq -r '.session_id // empty')

case "$EVENT" in
    SessionStart)
        SOURCE=$(echo "$INPUT" | jq -r '.source // empty')
        if [ "$SOURCE" = "startup" ]; then
            STATUS="idle"
        elif [ -n "$CLAUDE_SID" ]; then
            # /clear, /compact, /resume — report claude_session_id only (no status change)
            curl -s -X PATCH "$API_BASE/api/sessions/$SESSION_ID" \
                -H 'Content-Type: application/json' \
                -d "{\"claude_session_id\": \"$CLAUDE_SID\"}" > /dev/null 2>&1
            exit 0
        else
            exit 0
        fi
        ;;
    UserPromptSubmit|PreToolUse)
        STATUS="working"
        ;;
    Stop)
        STATUS="waiting"
        ;;
    Notification)
        # Only set waiting for notification types that indicate Claude needs input
        # idle_prompt = Claude waiting for user, permission_prompt = needs permission
        NTYPE=$(echo "$INPUT" | jq -r '.notification_type // empty')
        if [ "$NTYPE" = "idle_prompt" ] || [ "$NTYPE" = "permission_prompt" ]; then
            STATUS="waiting"
        else
            exit 0
        fi
        ;;
    SessionEnd)
        STATUS="disconnected"
        ;;
    *)
        exit 0
        ;;
esac

# Build JSON payload — always include claude_session_id for redundant tracking
if [ -n "$CLAUDE_SID" ]; then
    curl -s -X PATCH "$API_BASE/api/sessions/$SESSION_ID" \
        -H 'Content-Type: application/json' \
        -d "{\"status\": \"$STATUS\", \"claude_session_id\": \"$CLAUDE_SID\"}" \
        > /dev/null 2>&1
else
    curl -s -X PATCH "$API_BASE/api/sessions/$SESSION_ID" \
        -H 'Content-Type: application/json' \
        -d "{\"status\": \"$STATUS\"}" > /dev/null 2>&1
fi

exit 0
