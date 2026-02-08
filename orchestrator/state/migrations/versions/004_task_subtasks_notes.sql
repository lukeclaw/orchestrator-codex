-- Add parent_task_id for subtask hierarchy and notes for worker findings.

ALTER TABLE tasks ADD COLUMN parent_task_id TEXT REFERENCES tasks(id);
ALTER TABLE tasks ADD COLUMN notes TEXT;

CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);

INSERT INTO schema_version (version, description)
VALUES (4, 'Add parent_task_id and notes to tasks');
