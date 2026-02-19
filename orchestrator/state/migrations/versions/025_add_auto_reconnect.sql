-- Add auto_reconnect flag to sessions table
-- When enabled, health checks will automatically trigger reconnect for disconnected workers
ALTER TABLE sessions ADD COLUMN auto_reconnect BOOLEAN NOT NULL DEFAULT 0;

INSERT INTO schema_version (version, description) VALUES (25, 'Add auto_reconnect flag to sessions');
