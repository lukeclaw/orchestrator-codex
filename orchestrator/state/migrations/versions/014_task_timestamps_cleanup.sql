-- Simplify task timestamps: keep only created_at and add updated_at
-- Remove started_at and completed_at as they are not needed

-- Add updated_at column (default to created_at for existing rows)
ALTER TABLE tasks ADD COLUMN updated_at TEXT;
UPDATE tasks SET updated_at = created_at WHERE updated_at IS NULL;

-- SQLite doesn't support DROP COLUMN in older versions, but since 3.35.0 it does
-- We'll leave started_at and completed_at columns in the DB but stop using them
-- They will be ignored by the application

INSERT INTO schema_version (version, description)
VALUES (14, 'Add updated_at to tasks, deprecate started_at and completed_at');
