-- 018: Add tunnel_pid to sessions for subprocess-based reverse tunnel management.
-- Replaces tmux-based tunnel_pane with direct process tracking.

ALTER TABLE sessions ADD COLUMN tunnel_pid INTEGER;

INSERT INTO schema_version (version, description)
VALUES (18, 'Add tunnel_pid for subprocess-based tunnel management');
