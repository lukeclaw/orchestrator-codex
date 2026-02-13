-- Add last_viewed_at column to sessions table to track when user last viewed the worker detail page
ALTER TABLE sessions ADD COLUMN last_viewed_at TEXT;
