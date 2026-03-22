#!/bin/bash
# Pre-compaction hook: instructs the brain to save useful learnings before context is compacted.
# Output is injected as instructions before compaction runs.

API_BASE="http://127.0.0.1:8093"

INPUT=$(cat)
EVENT=$(echo "$INPUT" | jq -r '.hook_event_name // empty')
if [[ "$EVENT" != "PreCompact" ]]; then
    exit 0
fi

cat <<'FLUSH'
Before compaction, save any useful learnings from this session to persistent memory.

DO NOT write a summary of what happened. Instead, store specific, actionable learnings:
- Patterns that would help you or workers in the future (error fixes, repo quirks, timing insights)
- Decisions made and their rationale (why approach X was chosen over Y)
- Worker management lessons (what worked to unblock, what didn't)

Also check if workers have written useful project context you should learn from:
  orch-ctx list --scope global

Write each learning as a separate log entry:
  orch-memory log "<the learning>" --title "<specific title>"

After saving logs, consider curating your wisdom document if you have enough new material:
  orch-memory wisdom
  orch-memory wisdom-update <<'EOF'
  (updated curated learnings)
  EOF

Skip if nothing notable happened this session. Do not store routine observations.
Reply with just "Memory flushed." or "Nothing to store." when done.
FLUSH

exit 0
