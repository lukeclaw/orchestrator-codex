-- 002_remove_cost.sql: Remove cost tracking tables and indexes

DROP INDEX IF EXISTS idx_cost_events_session;
DROP INDEX IF EXISTS idx_cost_events_created;
DROP TABLE IF EXISTS cost_events;

INSERT INTO schema_version (version, description)
VALUES (2, 'Remove cost tracking module');
