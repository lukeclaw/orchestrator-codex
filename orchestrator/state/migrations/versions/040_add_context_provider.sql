-- Add optional provider scoping to context items.
-- NULL means shared across all providers.

ALTER TABLE context_items ADD COLUMN provider TEXT;

CREATE INDEX IF NOT EXISTS idx_context_provider ON context_items(provider);

INSERT INTO schema_version (version, description) VALUES (40, 'Add context provider');
