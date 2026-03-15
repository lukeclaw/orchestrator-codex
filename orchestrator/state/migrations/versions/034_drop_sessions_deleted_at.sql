-- Remove unused deleted_at column from sessions (soft-delete was abandoned
-- in favor of storing session_name directly in status_events)
ALTER TABLE sessions DROP COLUMN deleted_at;

INSERT INTO schema_version (version, description)
VALUES (34, 'Drop unused deleted_at from sessions');
