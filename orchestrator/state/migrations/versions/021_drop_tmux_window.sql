-- Drop the redundant tmux_window column.
-- The tmux target is always deterministic: "orchestrator:{session_name}".
ALTER TABLE sessions DROP COLUMN tmux_window;

INSERT INTO schema_version (version, description)
VALUES (21, 'Drop redundant tmux_window column');
