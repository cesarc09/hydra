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
