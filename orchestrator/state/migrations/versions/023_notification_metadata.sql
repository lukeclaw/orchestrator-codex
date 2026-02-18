-- Add metadata column to notifications for structured data (e.g., PR comment details)
ALTER TABLE notifications ADD COLUMN metadata TEXT;

INSERT INTO schema_version (version, description) VALUES (23, 'Add metadata column to notifications');
