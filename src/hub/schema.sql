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

-- Schema v3 (2026-05-03): memory layer. RETIRED 2026-05-22 — JARVIS
-- memory is now file-backed (MEMORY.md/USER.md); the events:memory →
-- memories-table path was removed. The version marker stays so existing
-- deployed state.db files (which ran the v3 migration) remain consistent;
-- the `memories` table + its indexes are intentionally no longer created.
INSERT OR IGNORE INTO schema_version (version) VALUES (3);
