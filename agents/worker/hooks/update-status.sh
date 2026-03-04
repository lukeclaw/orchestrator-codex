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
            # /clear, /compact, /resume — update claude_session_id and restore idle status
            # (SessionEnd fires first and briefly sets "disconnected"; correct it here)
            curl -s -X PATCH "$API_BASE/api/sessions/$SESSION_ID" \
                -H 'Content-Type: application/json' \
                -d "{\"status\": \"idle\", \"claude_session_id\": \"$CLAUDE_SID\"}" > /dev/null 2>&1
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

# Guard: don't overwrite "idle" with "waiting".
# After a user-initiated stop, /clear causes Claude to cycle through hooks
# (SessionEnd → SessionStart → Stop/Notification) which would incorrectly
# change the status back to "waiting". Check current status and bail out.
if [ "$STATUS" = "waiting" ]; then
    CURRENT=$(curl -s "$API_BASE/api/sessions/$SESSION_ID" | jq -r '.status // empty')
    if [ "$CURRENT" = "idle" ]; then
        exit 0
    fi
fi

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
