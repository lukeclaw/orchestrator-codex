ALTER TABLE sessions ADD COLUMN claude_session_id TEXT;

-- Backfill: for existing workers, set claude_session_id = id (the orchestrator
-- ID was passed as --session-id to Claude, so they match at initial launch).
UPDATE sessions SET claude_session_id = id WHERE claude_session_id IS NULL;

INSERT INTO schema_version (version, description)
VALUES (26, 'Add claude_session_id to track Claude internal session identity');
