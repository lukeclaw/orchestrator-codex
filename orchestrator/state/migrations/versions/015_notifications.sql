-- Notifications table for non-blocking information from workers
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
    session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
    message TEXT NOT NULL,
    notification_type TEXT DEFAULT 'info',
    link_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    dismissed INTEGER DEFAULT 0,
    dismissed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_notifications_task ON notifications(task_id);
CREATE INDEX IF NOT EXISTS idx_notifications_session ON notifications(session_id);
CREATE INDEX IF NOT EXISTS idx_notifications_dismissed ON notifications(dismissed);

INSERT INTO schema_version (version, description) VALUES (15, 'Add notifications table');
