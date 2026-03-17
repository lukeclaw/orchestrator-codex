-- Fix auto_reconnect column default: should be 1 (True) not 0
-- The Python model defaults to True but the original migration (025) used DEFAULT 0

-- Update existing sessions that were created with the wrong default
-- Only update sessions that still have auto_reconnect=0 and were never explicitly toggled
-- (We can't distinguish, so we set all 0s to 1 — users who explicitly disabled can re-disable)
UPDATE sessions SET auto_reconnect = 1 WHERE auto_reconnect = 0;

INSERT INTO schema_version (version, description) VALUES (37, 'Fix auto_reconnect default to true');
