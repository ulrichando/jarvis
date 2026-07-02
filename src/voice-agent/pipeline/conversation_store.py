"""Automatic conversation persistence for the voice agent.

Every voice turn is persisted to a SQLite store (default
``~/.jarvis/conversations.db``, overridable via ``JARVIS_CONVERSATION_PATH``).
Two tables — ``sessions`` and ``messages`` — capture the full conversation
history with per-turn idempotency and fire-and-forget writes.

Design (revived Phase 13 — was retired in Phase 12):
  - session lifecycle: ``begin_session`` / ``end_session`` called from
    ``jarvis_agent.on_enter`` / ``on_exit``; auto-title from first utterance.
  - per-turn persistence: both user and assistant messages are written
    from the ``_on_item`` handler after each turn completes.
  - cross-session recall: ``get_recent_sessions()`` returns a compact
    block injected into the system prompt at session start (volatile
    suffix, so the stable prefix cache stays warm). A ``recall_conversation``
    tool (``tools/conversation_recall.py``) provides deep search.
  - idempotency: UNIQUE(session_id, role, turn_sequence) — a given
    turn in a given session can only have one user row and one assistant
    row. Replay/retry is silently absorbed.
  - fire-and-forget: all writes are wrapped in try/except and never
    raise — a locked/full/corrupt DB must never block voice.

This module is stdlib-only and import-safe at module scope.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jarvis.conversation_store")

# ── DB path ────────────────────────────────────────────────────────────
DEFAULT_DB_PATH = Path(
    os.environ.get(
        "JARVIS_CONVERSATION_PATH",
        Path.home() / ".jarvis" / "conversations.db",
    )
).expanduser()

# ── Base schema ────────────────────────────────────────────────────────
_BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text TEXT NOT NULL,
    tool_calls_json TEXT,
    turn_sequence INTEGER NOT NULL,
    ts TEXT NOT NULL,
    UNIQUE (session_id, role, turn_sequence)
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
"""


# ── init_db ────────────────────────────────────────────────────────────

def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Create tables and indexes if they don't exist. Idempotent.

    Called once at session start from ``entrypoint()``. Follows the same
    ``CREATE TABLE IF NOT EXISTS`` + ``PRAGMA table_info`` migration
    skeleton as ``pipeline.turn_telemetry.init_db``.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_BASE_SCHEMA)
        # Migration skeleton — add columns here with PRAGMA table_info +
        # ALTER TABLE ADD COLUMN in try/except when the schema evolves.
        _run_migrations(conn)


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply online migrations. Each migration checks PRAGMA table_info
    before ALTER TABLE ADD COLUMN — SQLite has no IF NOT EXISTS for DDL.
    Failures are silently ignored (column already exists is harmless)."""
    # Future migrations go here. Example:
    # cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    # if "new_column" not in cols:
    #     try:
    #         conn.execute("ALTER TABLE messages ADD COLUMN new_column TEXT")
    #     except sqlite3.OperationalError:
    #         pass
    pass


# ── Session lifecycle ──────────────────────────────────────────────────

def begin_session(session_id: str, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Insert a new session row. Fire-and-forget — never raises."""
    if not session_id:
        return
    try:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO sessions (id, created_at, updated_at) VALUES (?, ?, ?)",
                (session_id, ts, ts),
            )
    except Exception:
        return  # silent — fire-and-forget


def end_session(session_id: str, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Stamp the session's ``ended_at``. Fire-and-forget — never raises."""
    if not session_id:
        return
    try:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE sessions SET ended_at = ?, updated_at = ? WHERE id = ?",
                (ts, ts, session_id),
            )
    except Exception:
        return


def auto_title(session_id: str, title: str, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Set the session title from the first user utterance.

    Truncates to 100 chars. The ``AND title IS NULL`` guard ensures only
    the first caller wins — idempotent across retries. Fire-and-forget."""
    if not session_id or not title:
        return
    title = title.strip()[:100]
    if not title:
        return
    try:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? "
                "WHERE id = ? AND title IS NULL",
                (title, ts, session_id),
            )
    except Exception:
        return


# ── Per-turn message logging ───────────────────────────────────────────

def log_turn(
    *,
    session_id: str,
    role: str,
    text: str,
    turn_sequence: int,
    tool_calls_json: Optional[str] = None,
    ts_utc: Optional[str] = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Persist one message (user or assistant) for a turn.

    The UNIQUE(session_id, role, turn_sequence) constraint provides
    idempotency — a second write with the same key is silently dropped
    via ``except sqlite3.IntegrityError``. All other exceptions are also
    swallowed so the DB can never block voice.
    """
    if not session_id or not text or not text.strip():
        return
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO messages "
                "(session_id, role, text, tool_calls_json, turn_sequence, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    role,
                    text.strip(),
                    tool_calls_json,
                    turn_sequence,
                    ts_utc or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                ),
            )
    except sqlite3.IntegrityError:
        return  # duplicate — idempotent, silently skip
    except Exception:
        return  # silent — fire-and-forget


# ── Prompt injection — recent-sessions block ───────────────────────────

def _relative_time(iso_ts: str) -> str:
    """Convert ISO UTC timestamp to a compact human-readable relative time.

    Returns strings like "just now", "5m ago", "2h ago", "3d ago", or
    "May 30" for older dates. Never raises — returns "" on unparseable input.
    """
    try:
        dt = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        now = datetime.now(timezone.utc)
        delta = now - dt
        mins = delta.total_seconds() / 60
        if mins < 1:
            return "just now"
        if mins < 60:
            return f"{int(mins)}m ago"
        hours = mins / 60
        if hours < 24:
            return f"{int(hours)}h ago"
        days = hours / 24
        if days < 7:
            return f"{int(days)}d ago"
        return dt.strftime("%b %d")
    except Exception:
        return ""


# Header reframed 2026-07-02: the old "your past sessions with the user"
# framing taught the LLM to narrate ambient-chatter titles as social
# events ("just finished a session with Zhaleh — she was watching
# football" — a fabricated person from room audio). Titles come from the
# FIRST mic utterance, which is often bystanders/TV, not conversation.
_RECENT_SESSIONS_HEADER = (
    "═══ RECENT SESSION TITLES — topic hints, NOT a social log ═══\n"
    "Titles are auto-taken from the first utterance the always-on mic "
    "heard — often background chatter or TV, not Ulrich and not anyone "
    "talking to you. Topic hints for recall_conversation only. NEVER "
    "narrate one as something you did or a person you talked to.\n"
)
_MAX_TITLE_CHARS = 80
# Budget covers the header (~370 chars) + ~5 title lines.
_MAX_BLOCK_CHARS = 1000


def get_recent_sessions(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    limit: int = 5,
) -> str:
    """Return a compact recent-sessions block for system-prompt injection.

    Includes recently-ended sessions AND the current active session (if
    any). Each entry is one line with relative time, title, and turn count.
    Returns "" when no sessions exist or the DB is unavailable.
    Total output is capped at ~600 chars so it never balloons the prompt.
    """
    if not db_path.exists():
        return ""
    try:
        with sqlite3.connect(db_path) as conn:
            rows = list(
                conn.execute(
                    "SELECT s.id, s.title, s.created_at, s.ended_at, "
                    "(SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS turn_count "
                    "FROM sessions s "
                    "WHERE (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) > 0 "
                    "  AND (s.ended_at IS NOT NULL "
                    "       OR s.updated_at > datetime('now', '-7 days')) "
                    "ORDER BY s.updated_at DESC "
                    "LIMIT ?",
                    (limit,),
                )
            )
    except Exception:
        return ""

    if not rows:
        return ""

    lines: list[str] = [_RECENT_SESSIONS_HEADER.strip()]
    accumulated = len(lines[0])

    for _sid, title, created_at, _ended_at, turn_count in rows:
        rel = _relative_time(created_at)
        title_str = (title or "(untitled)").strip()
        if len(title_str) > _MAX_TITLE_CHARS:
            title_str = title_str[:_MAX_TITLE_CHARS] + "…"
        entry = f"  [{rel}] \"{title_str}\" ({turn_count or 0} turns)"
        accumulated += len(entry) + 1  # +1 for newline
        if accumulated > _MAX_BLOCK_CHARS:
            omitted = len(rows) - len(lines) + 1
            if omitted > 0:
                lines.append(f"  (+{omitted} older sessions omitted)")
            break
        lines.append(entry)

    return "\n".join(lines)


# ── Deep recall — search past messages ─────────────────────────────────

_RECALL_LIMIT_DEFAULT = 10
_RECALL_LIMIT_MAX = 20

# Stop words to filter out when extracting keywords from a recall query.
# Keep only contentful words that could match stored message text.
_RECALL_STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "can", "shall", "i", "me",
    "my", "we", "us", "our", "you", "your", "he", "she", "it",
    "they", "them", "their", "what", "when", "where", "which",
    "who", "whom", "why", "how", "about", "for", "with", "from",
    "that", "this", "these", "those", "then", "than", "just",
    "also", "very", "really", "only", "now", "not", "but", "and",
    "or", "if", "of", "to", "in", "on", "at", "by", "as", "so",
    "no", "up", "out", "all", "any", "some", "there", "here",
    "thing", "things", "tell", "said", "say", "talk", "talking",
    "talked", "discuss", "discussed", "remember", "recall",
    "search", "look", "check", "find", "know", "get", "got",
})


def _extract_keywords(text: str) -> list[str]:
    """Extract contentful keywords from a recall query, filtering stop words
    and keeping only words >= 3 chars that could meaningfully match."""
    import re as _re
    words = _re.findall(r"[a-z0-9]+", text.lower())
    return [
        w for w in words
        if len(w) >= 3 and w not in _RECALL_STOP_WORDS
    ]


def recall_conversation(
    *,
    query: str,
    db_path: Path = DEFAULT_DB_PATH,
    limit: int = _RECALL_LIMIT_DEFAULT,
    session_id: Optional[str] = None,
) -> list[dict]:
    """Search past messages for keywords extracted from ``query``.

    Splits the query into contentful words (filtering stop words), then
    builds an OR-based LIKE search so individual keywords match. Results
    are ranked by number of matching keywords (most relevant first), then
    newest first within each rank.

    Returns a list of dicts with keys: session_title, session_created,
    role, text, ts, turn_sequence. Returns [] on any error or when the
    DB is missing.
    """
    if not query or not query.strip():
        return []
    limit = max(1, min(limit, _RECALL_LIMIT_MAX))
    if not db_path.exists():
        return []

    keywords = _extract_keywords(query)
    if not keywords:
        # Fall back to original LIKE search if no keywords extracted
        keywords = [query.strip().lower()]

    try:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            # Build OR-based LIKE clauses with match-count scoring.
            like_clauses = " OR ".join(
                ["m.text LIKE ?" for _ in keywords]
            )
            like_params = [f"%{kw}%" for kw in keywords]

            # Score: count how many keywords matched, then sort by score
            # DESC, ts DESC for relevance-ranked results.
            score_expr = " + ".join(
                [f"(CASE WHEN m.text LIKE ? THEN 1 ELSE 0 END)" for _ in keywords]
            )
            score_params = [f"%{kw}%" for kw in keywords]

            if session_id:
                sql = (
                    "SELECT s.title, s.created_at, m.role, m.text, m.ts, "
                    "m.turn_sequence, (" + score_expr + ") AS score "
                    "FROM messages m "
                    "JOIN sessions s ON s.id = m.session_id "
                    "WHERE m.session_id = ? AND (" + like_clauses + ") "
                    "ORDER BY score DESC, m.ts DESC "
                    "LIMIT ?"
                )
                params = score_params + [session_id] + like_params + [limit]
            else:
                sql = (
                    "SELECT s.title, s.created_at, m.role, m.text, m.ts, "
                    "m.turn_sequence, m.session_id, (" + score_expr + ") AS score "
                    "FROM messages m "
                    "JOIN sessions s ON s.id = m.session_id "
                    "WHERE " + like_clauses + " "
                    "ORDER BY score DESC, m.ts DESC "
                    "LIMIT ?"
                )
                params = score_params + like_params + [limit]

            rows = list(conn.execute(sql, params))
    except Exception:
        return []

    results: list[dict] = []
    for row in rows:
        if session_id:
            title, created, role, text, ts, turn_seq, score = row
            sid_val = session_id
        else:
            title, created, role, text, ts, turn_seq, sid_val, score = row
        results.append(
            {
                "session_title": title or "(untitled)",
                "session_created": created,
                "session_id": sid_val,
                "role": role,
                "text": text,
                "ts": ts,
                "turn_sequence": turn_seq,
                "score": score,
            }
        )
    return results
