#!/bin/bash
# Hook: refresh brain files and re-arm heartbeat after /clear or /compact.
# Fires on SessionStart. Skips the initial startup (handled by brain.py start).

API_BASE="http://127.0.0.1:8093"

INPUT=$(cat)
EVENT=$(echo "$INPUT" | jq -r '.hook_event_name // empty')
if [[ "$EVENT" != "SessionStart" ]]; then
    exit 0
fi

# Skip initial startup — brain.py start_brain() handles that path
SOURCE=$(echo "$INPUT" | jq -r '.session_start_source // empty')
if [[ "$SOURCE" == "startup" ]]; then
    exit 0
fi

# Re-deploy brain files (refreshes CLAUDE.md with latest wisdom) and re-arm heartbeat
curl -s -X POST "$API_BASE/api/brain/redeploy" > /dev/null 2>&1 &

exit 0
