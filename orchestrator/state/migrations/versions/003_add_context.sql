-- Context items: knowledge store for brain (global) and project-scoped context for workers.

CREATE TABLE IF NOT EXISTS context_items (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL DEFAULT 'global',
    project_id TEXT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT,
    source TEXT,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_context_scope ON context_items(scope);
CREATE INDEX IF NOT EXISTS idx_context_project ON context_items(project_id);
CREATE INDEX IF NOT EXISTS idx_context_category ON context_items(category);

INSERT INTO schema_version (version, description) VALUES (3, 'Add context_items table');
