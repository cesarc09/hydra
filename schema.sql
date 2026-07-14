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
    end_reason TEXT,
    archived_at TEXT,
    remote_control_url TEXT
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

-- Server-distributed slash commands. One row per command; name is the
-- slash-command name without ".md" (e.g. "sync", "finish"). Content is an
-- opaque markdown blob - the server never interprets it. Clients pull these
-- into ~/.claude/commands/<name>.md.
CREATE TABLE IF NOT EXISTS config_commands (
    name       TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Project registry. Paths live in project_paths so the same project can
-- exist at different filesystem paths on different machines.
CREATE TABLE IF NOT EXISTS projects (
    slug TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    -- Set when the project was created by the auto-register endpoint; cleared
    -- by the dashboard "Confirm" action. NULL means manually registered.
    auto_registered_at TEXT
);

-- One canonical path per (slug, instance_id). Re-registering the same slug
-- from a machine updates that row; a new machine adds a new row.
CREATE TABLE IF NOT EXISTS project_paths (
    slug TEXT NOT NULL REFERENCES projects(slug) ON DELETE CASCADE,
    instance_id TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    -- Set when this path was attached by auto-register; cleared by Confirm.
    auto_registered_at TEXT,
    PRIMARY KEY (slug, instance_id)
);

CREATE INDEX IF NOT EXISTS idx_project_paths_path ON project_paths(path);

-- Cross-machine memory store. project_slug NULL => global; otherwise pinned
-- to a project. Names are globally unique, scope-independent: one name = one
-- memory. Legacy DBs used two partial unique indexes instead, which let a
-- global row and a pinned row share a name - that is what let a stale mirror
-- file re-insert a deleted memory as a second row (the duplicate-memory bug).
--
-- The UNIQUE lives inline on the column rather than as a CREATE UNIQUE INDEX
-- below because db.get_db() runs this script BEFORE db._migrate(): a bare
-- CREATE UNIQUE INDEX would abort startup on a legacy DB that still holds
-- duplicates. Fresh DBs (and tests/conftest.py, which only runs this file) get
-- uniqueness from here; db._ensure_unique_memory_names() collapses duplicates
-- and installs idx_memories_name on pre-existing DBs.
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL CHECK (type IN ('user', 'feedback', 'project', 'reference')),
    body TEXT NOT NULL DEFAULT '',
    project_slug TEXT REFERENCES projects(slug) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
