-- Add provider identity to sessions. Existing rows default to Claude.
ALTER TABLE sessions ADD COLUMN provider TEXT NOT NULL DEFAULT 'claude';

UPDATE sessions SET provider = 'claude' WHERE provider IS NULL;

INSERT INTO schema_version (version, description)
VALUES (39, 'Add provider to sessions');
