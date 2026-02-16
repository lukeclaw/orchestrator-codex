-- 022_drop_dead_tables.sql: Remove legacy tables and dead columns.
-- Python models and repository code were already removed in commit 958398d.

-- ============================================================
-- Step 1: Rebuild tasks table to remove dead columns
--   - blocked_by_decision_id (references decisions table being dropped)
--   - started_at, completed_at (deprecated in migration 014)
-- ============================================================

CREATE TABLE tasks_new (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'todo',
    priority TEXT DEFAULT 'M',
    assigned_session_id TEXT REFERENCES sessions(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    parent_task_id TEXT REFERENCES tasks_new(id),
    notes TEXT,
    links TEXT,
    task_index INTEGER
);

INSERT INTO tasks_new (id, project_id, title, description, status, priority,
    assigned_session_id, created_at, updated_at, parent_task_id, notes, links, task_index)
SELECT id, project_id, title, description, status, priority,
    assigned_session_id, created_at, updated_at, parent_task_id, notes, links, task_index
FROM tasks;

DROP TABLE tasks;
ALTER TABLE tasks_new RENAME TO tasks;

-- Recreate indexes for the rebuilt tasks table
CREATE INDEX idx_tasks_project ON tasks(project_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_session ON tasks(assigned_session_id);
CREATE INDEX idx_tasks_parent ON tasks(parent_task_id);
CREATE INDEX idx_tasks_project_index ON tasks(project_id, task_index);

-- ============================================================
-- Step 2: Drop 11 legacy tables with no application code
-- ============================================================

DROP TABLE IF EXISTS task_dependencies;
DROP TABLE IF EXISTS task_requirements;
DROP TABLE IF EXISTS activities;
DROP TABLE IF EXISTS decision_history;
DROP TABLE IF EXISTS decisions;
DROP TABLE IF EXISTS worker_capabilities;
DROP TABLE IF EXISTS session_snapshots;
DROP TABLE IF EXISTS comm_events;
DROP TABLE IF EXISTS learned_patterns;
DROP TABLE IF EXISTS prompt_templates;
DROP TABLE IF EXISTS project_workers;

INSERT OR REPLACE INTO schema_version (version, description)
VALUES (22, 'Rebuild tasks (drop dead columns), drop 11 legacy tables');
