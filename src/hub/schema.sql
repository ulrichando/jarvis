CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
INSERT OR IGNORE INTO schema_version (version) VALUES (1);

CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    title         TEXT,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    ended_at      INTEGER
);

CREATE TABLE IF NOT EXISTS messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT NOT NULL REFERENCES sessions(id),
    source            TEXT NOT NULL,
    source_event_id   TEXT NOT NULL,
    role              TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text              TEXT NOT NULL,
    tool_calls_json   TEXT,
    ts                INTEGER NOT NULL,
    UNIQUE (source, source_event_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, ts);
CREATE INDEX IF NOT EXISTS idx_messages_source  ON messages (source, ts);
