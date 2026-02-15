-- Drop the tunnel_pane column (legacy tmux-based tunnel management).
-- Reverse tunnels are now managed as direct subprocesses via tunnel_pid.
ALTER TABLE sessions DROP COLUMN tunnel_pane;

INSERT INTO schema_version (version, description)
VALUES (19, 'Drop tunnel_pane — tunnels now use subprocess PID');
