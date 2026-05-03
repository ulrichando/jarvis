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

-- Schema v2 (2026-05-03): unified settings.
INSERT OR IGNORE INTO schema_version (version) VALUES (2);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  INTEGER NOT NULL,
    source      TEXT
);

-- Schema v3 (2026-05-03): memory layer (durable user-facts store).
-- See docs/superpowers/specs/2026-05-03-jarvis-memory-layer-design.md.
-- memory_id is sha256 of normalized content -> idempotent under stream replay.
INSERT OR IGNORE INTO schema_version (version) VALUES (3);

CREATE TABLE IF NOT EXISTS memories (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id         TEXT UNIQUE NOT NULL,
    content           TEXT NOT NULL,
    category          TEXT NOT NULL DEFAULT 'fact',
    source            TEXT NOT NULL,
    source_session_id TEXT,
    created_ts        INTEGER NOT NULL,
    updated_ts        INTEGER NOT NULL,
    last_used_ts      INTEGER,
    use_count         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_memories_updated  ON memories (updated_ts DESC);
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories (category);
