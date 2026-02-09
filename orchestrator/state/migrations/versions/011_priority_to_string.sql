-- Migration: Convert numeric priority to H/M/L string format
-- 0-1 -> L (Low), 2-3 -> M (Medium), 4+ -> H (High)

-- SQLite doesn't support ALTER COLUMN, so we update in place
-- The column can store both int and string, conversion happens on read

UPDATE tasks SET priority = 'L' WHERE CAST(priority AS INTEGER) <= 1;
UPDATE tasks SET priority = 'M' WHERE CAST(priority AS INTEGER) BETWEEN 2 AND 3;
UPDATE tasks SET priority = 'H' WHERE CAST(priority AS INTEGER) >= 4;

-- Set any remaining NULL or invalid values to M
UPDATE tasks SET priority = 'M' WHERE priority NOT IN ('H', 'M', 'L');
