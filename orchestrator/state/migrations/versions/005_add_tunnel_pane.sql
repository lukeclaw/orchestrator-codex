-- 005: Add tunnel_pane to sessions for tracking rdev reverse SSH tunnels.

ALTER TABLE sessions ADD COLUMN tunnel_pane TEXT;

INSERT INTO schema_version (version, description)
VALUES (5, 'Add tunnel_pane to sessions for rdev tunnel tracking');
