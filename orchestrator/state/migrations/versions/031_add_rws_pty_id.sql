-- Add rws_pty_id column to sessions for RWS PTY-based remote workers.
-- NULL = legacy screen-based session, non-null = RWS PTY session.
ALTER TABLE sessions ADD COLUMN rws_pty_id TEXT;
INSERT INTO schema_version (version, description) VALUES (31, 'Add rws_pty_id for RWS PTY sessions');
