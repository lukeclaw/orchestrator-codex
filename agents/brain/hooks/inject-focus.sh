#!/bin/bash
# Hook script to inject current dashboard URL into brain prompts

API_BASE="http://127.0.0.1:8093"

INPUT=$(cat)
EVENT=$(echo "$INPUT" | jq -r '.hook_event_name // empty')
if [[ "$EVENT" != "UserPromptSubmit" ]]; then
    exit 0
fi

URL=$(curl -s "$API_BASE/api/brain/focus" | jq -r '.url // empty')
if [[ -n "$URL" && "$URL" != "null" ]]; then
    echo "[Dashboard: $URL]"
fi

exit 0
