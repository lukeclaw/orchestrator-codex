-- Store session name in status_events so it survives session deletion
ALTER TABLE status_events ADD COLUMN session_name TEXT;

-- Backfill existing session events with current names
UPDATE status_events
SET session_name = (SELECT s.name FROM sessions s WHERE s.id = status_events.entity_id)
WHERE entity_type = 'session' AND session_name IS NULL;

INSERT INTO schema_version (version, description)
VALUES (33, 'Add session_name to status_events for deleted worker display');
