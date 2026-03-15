INSERT INTO schema_version (version, description) VALUES (35, 'Add human activity events table');

CREATE TABLE IF NOT EXISTS human_activity_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time TEXT NOT NULL,
    end_time TEXT
);
CREATE INDEX IF NOT EXISTS idx_human_activity_start ON human_activity_events (start_time);
CREATE INDEX IF NOT EXISTS idx_human_activity_end ON human_activity_events (end_time);
