-- Add links field to tasks table for storing related URLs
-- Links are stored as JSON array: [{"url": "...", "title": "...", "type": "..."}]

-- SQLite doesn't support IF NOT EXISTS for ADD COLUMN, so we use a workaround
-- This will silently fail if the column already exists
ALTER TABLE tasks ADD COLUMN links TEXT;
-- Note: If this fails with "duplicate column", the column already exists which is fine
