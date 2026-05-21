"""Tests for the wired session_search tool.

Proves:
  (a) session_search self-registers in the registry.
  (b) check_fn returns True when a temp state.db exists, False when absent.
  (c) DISCOVERY shape returns matching turns from a seeded hub DB.
  (d) BROWSE shape returns session summaries when no query is given.
  (e) SESSION shape returns messages for a specific session.
  (f) Missing DB returns a clean error (no exception propagation).
  (g) Zero hermes tokens in the tool file.

No network.  Uses a minimal in-process SQLite DB that matches the hub schema.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

_VA_ROOT = Path(__file__).resolve().parent.parent
if str(_VA_ROOT) not in sys.path:
    sys.path.insert(0, str(_VA_ROOT))

# Also make the hub SDK importable so we can reuse bootstrap_schema if needed.
_HUB_ROOT = _VA_ROOT.parent.parent / "src" / "hub"
if _HUB_ROOT.exists() and str(_HUB_ROOT) not in sys.path:
    sys.path.insert(0, str(_HUB_ROOT))


# ---------------------------------------------------------------------------
# Helpers — minimal hub schema seeder
# ---------------------------------------------------------------------------

def _bootstrap_db(path: Path) -> None:
    """Create the hub messages + sessions tables (schema v1)."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            title TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            ended_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            source TEXT NOT NULL,
            source_event_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            text TEXT NOT NULL,
            tool_calls_json TEXT,
            ts INTEGER NOT NULL,
            UNIQUE (source, source_event_id)
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, ts);
    """)
    conn.commit()
    conn.close()


def _seed(path: Path, rows: list[tuple]) -> None:
    """
    Seed rows as (session_id, role, text, ts_ms).
    Sessions are auto-created as needed.
    """
    _bootstrap_db(path)
    conn = sqlite3.connect(str(path))
    sessions_seen: set = set()
    for i, (sid, role, text, ts) in enumerate(rows):
        if sid not in sessions_seen:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, source, created_at, updated_at) VALUES (?,?,?,?)",
                (sid, "voice", ts, ts),
            )
            sessions_seen.add(sid)
        conn.execute(
            "INSERT INTO messages (session_id, source, source_event_id, role, text, ts) "
            "VALUES (?,?,?,?,?,?)",
            (sid, "voice", f"evt-{i}", role, text, ts),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# (a) self-registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_session_search_registered(self):
        import tools.session_search  # noqa: F401 — side effect
        from tools.registry import registry
        assert registry.get_entry("session_search") is not None

    def test_toolset_is_session_search(self):
        import tools.session_search  # noqa: F401
        from tools.registry import registry
        entry = registry.get_entry("session_search")
        assert entry.toolset == "session_search"


# ---------------------------------------------------------------------------
# (b) check_fn behaviour
# ---------------------------------------------------------------------------

class TestCheckFn:
    def test_enabled_when_db_exists(self, tmp_path, monkeypatch):
        db = tmp_path / "state.db"
        _bootstrap_db(db)
        monkeypatch.setenv("JARVIS_HUB_DB", str(db))

        import tools.session_search as mod
        from tools.registry import invalidate_check_fn_cache
        invalidate_check_fn_cache()
        assert mod._check_session_search() is True

    def test_disabled_when_db_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_HUB_DB", str(tmp_path / "nope.db"))

        import tools.session_search as mod
        from tools.registry import invalidate_check_fn_cache
        invalidate_check_fn_cache()
        assert mod._check_session_search() is False


# ---------------------------------------------------------------------------
# (c) DISCOVERY shape
# ---------------------------------------------------------------------------

class TestDiscovery:
    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        path = tmp_path / "state.db"
        _seed(path, [
            ("s1", "user", "hello world how are you", 1000),
            ("s1", "assistant", "I am doing great, hello back", 2000),
            ("s2", "user", "what is the weather today", 3000),
            ("s2", "assistant", "The weather in Yaoundé is sunny", 4000),
            ("s3", "user", "please recall the auth token we discussed", 5000),
        ])
        monkeypatch.setenv("JARVIS_HUB_DB", str(path))
        return path

    def _call(self, args: dict) -> dict:
        from tools.session_search import _handle_session_search
        return json.loads(_handle_session_search(args))

    def test_keyword_match_returns_result(self, db):
        result = self._call({"query": "weather"})
        assert result["success"] is True
        assert result["mode"] == "discover"
        assert result["count"] >= 1
        texts = [r["snippet"] for r in result["results"]]
        assert any("weather" in t.lower() for t in texts)

    def test_no_match_returns_empty(self, db):
        result = self._call({"query": "xyzzy_not_here"})
        assert result["success"] is True
        assert result["count"] == 0
        assert result["results"] == []

    def test_limit_is_respected(self, db):
        result = self._call({"query": "the", "limit": 2})
        assert result["count"] <= 2

    def test_case_insensitive(self, db):
        result_lower = self._call({"query": "yaound"})
        result_upper = self._call({"query": "YAOUND"})
        assert result_lower["count"] == result_upper["count"]

    def test_result_has_expected_keys(self, db):
        result = self._call({"query": "hello"})
        assert result["success"] is True
        for r in result["results"]:
            assert "when" in r
            assert "role" in r
            assert "snippet" in r
            assert "session_id" in r


# ---------------------------------------------------------------------------
# (d) BROWSE shape
# ---------------------------------------------------------------------------

class TestBrowse:
    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        path = tmp_path / "state.db"
        _seed(path, [
            ("sess-a", "user", "first session message", 100),
            ("sess-a", "assistant", "first session reply", 200),
            ("sess-b", "user", "second session message", 300),
        ])
        monkeypatch.setenv("JARVIS_HUB_DB", str(path))
        return path

    def _call(self, args: dict) -> dict:
        from tools.session_search import _handle_session_search
        return json.loads(_handle_session_search(args))

    def test_browse_returns_sessions(self, db):
        result = self._call({})
        assert result["success"] is True
        assert result["mode"] == "browse"
        assert result["count"] >= 1
        ids = {r["session_id"] for r in result["results"]}
        assert "sess-a" in ids
        assert "sess-b" in ids

    def test_browse_has_preview(self, db):
        result = self._call({})
        for r in result["results"]:
            assert "preview" in r
            assert "message_count" in r
            assert "started" in r
            assert "last_active" in r


# ---------------------------------------------------------------------------
# (e) SESSION shape
# ---------------------------------------------------------------------------

class TestSession:
    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        path = tmp_path / "state.db"
        _seed(path, [
            ("sess-x", "user", "first message in x", 100),
            ("sess-x", "assistant", "reply in x", 200),
            ("sess-x", "user", "followup in x", 300),
        ])
        monkeypatch.setenv("JARVIS_HUB_DB", str(path))
        return path

    def _call(self, args: dict) -> dict:
        from tools.session_search import _handle_session_search
        return json.loads(_handle_session_search(args))

    def test_session_returns_messages(self, db):
        result = self._call({"session_id": "sess-x"})
        assert result["success"] is True
        assert result["mode"] == "session"
        assert result["count"] == 3

    def test_session_oldest_first(self, db):
        result = self._call({"session_id": "sess-x"})
        roles = [r["role"] for r in result["results"]]
        assert roles[0] == "user"
        assert roles[1] == "assistant"

    def test_session_not_found_returns_empty(self, db):
        result = self._call({"session_id": "no-such-session"})
        assert result["success"] is True
        assert result["count"] == 0


# ---------------------------------------------------------------------------
# (f) missing DB returns clean error
# ---------------------------------------------------------------------------

class TestMissingDb:
    def test_no_db_returns_error_json(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_HUB_DB", str(tmp_path / "nope.db"))
        from tools.session_search import _handle_session_search
        raw = _handle_session_search({"query": "anything"})
        result = json.loads(raw)
        assert "error" in result


# ---------------------------------------------------------------------------
# (g) no hermes tokens
# ---------------------------------------------------------------------------

class TestNoHermesTokens:
    def test_no_hermes_in_session_search(self):
        path = _VA_ROOT / "tools" / "session_search.py"
        lines = path.read_text(encoding="utf-8").splitlines()
        bad = []
        for lineno, line in enumerate(lines, 1):
            if "hermes" in line.lower():
                stripped = line.lstrip()
                if stripped.startswith("#") or '"""' in line:
                    continue
                bad.append((lineno, line.rstrip()))
        assert not bad, f"session_search.py has non-comment 'hermes' tokens: {bad}"
