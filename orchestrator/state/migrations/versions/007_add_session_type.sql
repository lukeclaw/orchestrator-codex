-- 007_add_session_type.sql: Add session_type to distinguish worker vs brain sessions

ALTER TABLE sessions ADD COLUMN session_type TEXT DEFAULT 'worker';

-- Update schema version
INSERT INTO schema_version (version, description)
VALUES (7, 'Add session_type column to sessions table');
