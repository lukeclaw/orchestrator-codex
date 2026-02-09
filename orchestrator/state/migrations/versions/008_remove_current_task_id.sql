-- 008_remove_current_task_id.sql: Remove current_task_id from sessions
-- task.assigned_session_id is now the single source of truth for task-session mapping

-- SQLite doesn't support DROP COLUMN directly, so we recreate the table
-- For dev environment, this is acceptable. For prod, would need a more careful approach.

CREATE TABLE sessions_new (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    host TEXT NOT NULL,
    mp_path TEXT,
    tmux_window TEXT,
    tunnel_pane TEXT,
    status TEXT DEFAULT 'idle',
    takeover_mode BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_activity TIMESTAMP,
    session_type TEXT DEFAULT 'worker'
);

INSERT INTO sessions_new (id, name, host, mp_path, tmux_window, tunnel_pane, status, takeover_mode, created_at, last_activity, session_type)
SELECT id, name, host, mp_path, tmux_window, tunnel_pane, status, takeover_mode, created_at, last_activity, session_type
FROM sessions;

DROP TABLE sessions;
ALTER TABLE sessions_new RENAME TO sessions;

-- Update schema version
INSERT INTO schema_version (version, description)
VALUES (8, 'Remove current_task_id from sessions - task.assigned_session_id is SOT');
