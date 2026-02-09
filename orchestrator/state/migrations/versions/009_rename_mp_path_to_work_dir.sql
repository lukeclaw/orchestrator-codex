-- Migration: Rename mp_path to work_dir in sessions table
-- This is a simple column rename, SQLite requires table recreation

CREATE TABLE sessions_new (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    host TEXT NOT NULL,
    work_dir TEXT,
    tmux_window TEXT,
    tunnel_pane TEXT,
    status TEXT DEFAULT 'idle',
    takeover_mode BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_activity TIMESTAMP,
    session_type TEXT DEFAULT 'worker'
);

INSERT INTO sessions_new (id, name, host, work_dir, tmux_window, tunnel_pane, status, takeover_mode, created_at, last_activity, session_type)
SELECT id, name, host, mp_path, tmux_window, tunnel_pane, status, takeover_mode, created_at, last_activity, session_type
FROM sessions;

DROP TABLE sessions;
ALTER TABLE sessions_new RENAME TO sessions;

-- Update schema version
INSERT INTO schema_version (version, description)
VALUES (9, 'Rename mp_path to work_dir in sessions table');
