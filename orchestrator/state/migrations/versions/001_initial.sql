-- 001_initial.sql: Full schema from PRD Section 8.6

-- ============================================================
-- CORE ENTITIES
-- ============================================================

CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'active',  -- active, paused, completed, archived
    target_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    host TEXT NOT NULL,              -- SSH host or 'local'
    mp_path TEXT,                    -- Working directory
    tmux_window TEXT,                -- tmux target (e.g., orchestrator:0)
    status TEXT DEFAULT 'idle',      -- idle, working, waiting, error, disconnected
    takeover_mode BOOLEAN DEFAULT FALSE,
    current_task_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_activity TIMESTAMP
);

CREATE TABLE project_workers (
    project_id TEXT REFERENCES projects(id),
    session_id TEXT REFERENCES sessions(id),
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, session_id)
);

-- ============================================================
-- TASK MANAGEMENT
-- ============================================================

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'todo',      -- todo, in_progress, done, blocked
    priority INTEGER DEFAULT 0,
    assigned_session_id TEXT REFERENCES sessions(id),
    blocked_by_decision_id TEXT REFERENCES decisions(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE task_dependencies (
    task_id TEXT REFERENCES tasks(id),
    depends_on_task_id TEXT REFERENCES tasks(id),
    PRIMARY KEY (task_id, depends_on_task_id)
);

-- ============================================================
-- PR TRACKING
-- ============================================================

CREATE TABLE pull_requests (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES tasks(id),
    session_id TEXT REFERENCES sessions(id),
    url TEXT NOT NULL,
    number INTEGER,
    title TEXT,
    status TEXT DEFAULT 'open',      -- open, in_review, approved, merged, closed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    merged_at TIMESTAMP
);

-- ============================================================
-- DECISION MANAGEMENT
-- ============================================================

CREATE TABLE decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    task_id TEXT REFERENCES tasks(id),
    session_id TEXT REFERENCES sessions(id),
    question TEXT NOT NULL,
    options TEXT,                    -- JSON array of options
    context TEXT,
    urgency TEXT DEFAULT 'normal',   -- low, normal, high, critical
    status TEXT DEFAULT 'pending',   -- pending, responded, dismissed
    response TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    resolved_by TEXT
);

CREATE TABLE decision_history (
    id TEXT PRIMARY KEY,
    decision_id TEXT REFERENCES decisions(id),
    project_id TEXT,
    question TEXT,
    context TEXT,
    decision TEXT,
    user_feedback TEXT,
    was_helpful BOOLEAN,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- ACTIVITY TRACKING
-- ============================================================

CREATE TABLE activities (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    task_id TEXT REFERENCES tasks(id),
    session_id TEXT REFERENCES sessions(id),
    event_type TEXT NOT NULL,
    event_data TEXT,                 -- JSON with event-specific data
    actor TEXT,                      -- 'system', 'user', or session name
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- LEARNING & INTELLIGENCE
-- ============================================================

CREATE TABLE learned_patterns (
    id TEXT PRIMARY KEY,
    pattern_type TEXT,               -- decision, task_routing, error_handling
    pattern_key TEXT,
    pattern_value TEXT,
    confidence REAL,
    usage_count INTEGER DEFAULT 0,
    last_used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- WORKER CAPABILITIES & TASK SCHEDULING
-- ============================================================

CREATE TABLE worker_capabilities (
    session_id TEXT REFERENCES sessions(id),
    capability_type TEXT NOT NULL,
    capability_value TEXT NOT NULL,
    PRIMARY KEY (session_id, capability_type, capability_value)
);

CREATE TABLE task_requirements (
    task_id TEXT REFERENCES tasks(id),
    requirement_type TEXT NOT NULL,
    requirement_value TEXT NOT NULL,
    PRIMARY KEY (task_id, requirement_type, requirement_value)
);

CREATE TABLE pr_dependencies (
    pr_id TEXT REFERENCES pull_requests(id),
    depends_on_pr_id TEXT REFERENCES pull_requests(id),
    PRIMARY KEY (pr_id, depends_on_pr_id)
);

-- ============================================================
-- COST TRACKING
-- ============================================================

CREATE TABLE cost_events (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    source TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_cost_usd REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- SESSION RECOVERY
-- ============================================================

CREATE TABLE session_snapshots (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    task_summary TEXT,
    key_decisions TEXT,              -- JSON
    file_paths TEXT,                 -- JSON
    last_known_state TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- COMMUNICATION RELIABILITY
-- ============================================================

CREATE TABLE comm_events (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    channel TEXT NOT NULL,
    event_type TEXT NOT NULL,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- CONFIGURATION & TEMPLATES (DB-Driven)
-- ============================================================

CREATE TABLE config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,              -- JSON-encoded value
    description TEXT,
    category TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE prompt_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    template TEXT NOT NULL,
    description TEXT,
    version INTEGER DEFAULT 1,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE skill_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    version INTEGER DEFAULT 1,
    template TEXT NOT NULL,
    install_instruction TEXT,
    description TEXT,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- SCHEMA VERSION
-- ============================================================

CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

INSERT INTO schema_version (version, description)
VALUES (1, 'Initial schema — all tables from PRD v1.5 Section 8.6');

-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX idx_tasks_project ON tasks(project_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_session ON tasks(assigned_session_id);
CREATE INDEX idx_prs_task ON pull_requests(task_id);
CREATE INDEX idx_decisions_status ON decisions(status);
CREATE INDEX idx_activities_project ON activities(project_id);
CREATE INDEX idx_activities_created ON activities(created_at);
CREATE INDEX idx_cost_events_session ON cost_events(session_id);
CREATE INDEX idx_cost_events_created ON cost_events(created_at);
CREATE INDEX idx_session_snapshots_session ON session_snapshots(session_id);
CREATE INDEX idx_comm_events_session ON comm_events(session_id);
CREATE INDEX idx_worker_caps_session ON worker_capabilities(session_id);
CREATE INDEX idx_config_key ON config(key);
