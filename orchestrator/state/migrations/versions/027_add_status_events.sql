-- Status events table for tracking historical status transitions
CREATE TABLE IF NOT EXISTS status_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,        -- 'task' or 'session'
    entity_id TEXT NOT NULL,
    old_status TEXT,                   -- NULL for initial/backfilled
    new_status TEXT NOT NULL,
    is_subtask INTEGER DEFAULT 0,     -- denormalized from tasks.parent_task_id
    session_type TEXT,                -- denormalized from sessions.session_type
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_status_events_entity ON status_events (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_status_events_timestamp ON status_events (timestamp);
CREATE INDEX IF NOT EXISTS idx_status_events_agg ON status_events (entity_type, new_status, timestamp);

-- Backfill: insert events for tasks that are already done
INSERT INTO status_events (entity_type, entity_id, old_status, new_status, is_subtask, timestamp)
SELECT
    'task',
    t.id,
    NULL,
    t.status,
    CASE WHEN t.parent_task_id IS NOT NULL THEN 1 ELSE 0 END,
    t.updated_at
FROM tasks t
WHERE t.status = 'done';

-- Backfill: insert events for current session states
INSERT INTO status_events (entity_type, entity_id, old_status, new_status, session_type, timestamp)
SELECT
    'session',
    s.id,
    NULL,
    s.status,
    s.session_type,
    COALESCE(s.last_status_changed_at, s.created_at)
FROM sessions s;

INSERT INTO schema_version (version, description)
VALUES (27, 'Add status_events table for trends tracking');
