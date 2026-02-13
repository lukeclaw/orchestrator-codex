-- 017_add_last_status_changed_at.sql: Remove last_activity, add last_status_changed_at
-- The new field auto-updates whenever session status changes

CREATE TABLE sessions_new (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    host TEXT NOT NULL,
    work_dir TEXT,
    tmux_window TEXT,
    tunnel_pane TEXT,
    status TEXT DEFAULT 'idle',
    takeover_mode BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_status_changed_at TIMESTAMP,
    session_type TEXT DEFAULT 'worker',
    last_viewed_at TIMESTAMP
);

INSERT INTO sessions_new (id, name, host, work_dir, tmux_window, tunnel_pane, status, takeover_mode, created_at, last_status_changed_at, session_type, last_viewed_at)
SELECT id, name, host, work_dir, tmux_window, tunnel_pane, status, takeover_mode, created_at, last_activity, session_type, last_viewed_at
FROM sessions;

DROP TABLE sessions;
ALTER TABLE sessions_new RENAME TO sessions;

INSERT OR REPLACE INTO schema_version (version, description)
VALUES (17, 'Remove last_activity, add last_status_changed_at');
