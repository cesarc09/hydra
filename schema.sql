CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    cwd TEXT,
    model TEXT,
    started_at TEXT NOT NULL,
    last_event_at TEXT NOT NULL,
    last_tool TEXT,
    last_tool_input_summary TEXT,
    files_changed TEXT DEFAULT '[]',
    end_reason TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    event_name TEXT NOT NULL,
    tool_name TEXT,
    tool_input_summary TEXT,
    received_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_received ON events(received_at);

-- Personal CLAUDE.md content (single-row table)
CREATE TABLE IF NOT EXISTS claude_md (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    content TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

-- Project registry
CREATE TABLE IF NOT EXISTS projects (
    slug TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Cross-machine memory store. project_slug NULL => global; otherwise pinned
-- to a project. Partial unique indexes enforce that global names are unique
-- and (name, project_slug) pairs are unique within a project.
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL CHECK (type IN ('user', 'feedback', 'project', 'reference')),
    body TEXT NOT NULL DEFAULT '',
    project_slug TEXT REFERENCES projects(slug) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_global
    ON memories(name) WHERE project_slug IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_project
    ON memories(name, project_slug) WHERE project_slug IS NOT NULL;
