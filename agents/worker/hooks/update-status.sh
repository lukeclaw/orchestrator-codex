#!/bin/bash
# Hook script to update worker status in orchestrator

SESSION_ID="{{SESSION_ID}}"
API_BASE="{{API_BASE}}"

INPUT=$(cat)
EVENT=$(echo "$INPUT" | jq -r '.hook_event_name // empty')

case "$EVENT" in
    SessionStart)
        STATUS="idle"
        ;;
    UserPromptSubmit|PreToolUse)
        STATUS="working"
        ;;
    Stop|Notification)
        STATUS="waiting"
        ;;
    SessionEnd)
        STATUS="disconnected"
        ;;
    *)
        exit 0
        ;;
esac

curl -s -X PATCH "$API_BASE/api/sessions/$SESSION_ID" \
    -H 'Content-Type: application/json' \
    -d "{\"status\": \"$STATUS\"}" > /dev/null 2>&1

exit 0
