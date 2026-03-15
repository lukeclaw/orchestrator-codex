INSERT INTO schema_version (version, description) VALUES (36, 'Add task_id and task_title to status_events');

ALTER TABLE status_events ADD COLUMN task_id TEXT;
ALTER TABLE status_events ADD COLUMN task_title TEXT;
