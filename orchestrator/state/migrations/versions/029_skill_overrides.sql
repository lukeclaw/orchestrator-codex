-- Enable/disable for custom skills
ALTER TABLE skills ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1;

-- Overrides for built-in skills (no DB representation otherwise).
-- A row with enabled=0 means the built-in skill is disabled.
-- No row = default (enabled). Re-enabling deletes the row.
CREATE TABLE IF NOT EXISTS skill_overrides (
    name TEXT NOT NULL,
    target TEXT NOT NULL CHECK(target IN ('brain', 'worker')),
    enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY(name, target)
);
