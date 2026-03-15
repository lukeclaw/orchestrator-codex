-- Soft-delete column for sessions (preserves row for historical queries)
ALTER TABLE sessions ADD COLUMN deleted_at TEXT;

INSERT INTO schema_version (version, description)
VALUES (32, 'Add soft-delete for sessions');
