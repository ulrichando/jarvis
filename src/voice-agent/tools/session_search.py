"""Session search tool — recall past conversations by keyword.

Queries the JARVIS hub conversation store at ``~/.jarvis/hub/state.db``
(SQLite WAL).  The hub daemon materialises every voice/web/cli turn there
via the ``events:conversation`` Redis stream.

Data source: ``messages`` table, schema v1+:
    id INTEGER PK, session_id TEXT, source TEXT, role TEXT,
    text TEXT, ts INTEGER (Unix ms)

Three calling shapes
--------------------
DISCOVERY (default) — pass ``query``:
    session_search(query="auth refactor", limit=5)
    Substring-LIKE search over recent turns.  Returns matched turns
    (role, snippet, when) ordered newest-first, capped at ``limit``.

BROWSE — no ``query``:
    session_search()
    Lists the most recent N distinct session_ids with their first/last
    timestamp and a short preview of the last message.

SESSION — pass ``session_id``:
    session_search(session_id="abc123", limit=20)
    Returns the messages in that session, oldest-first.

check_fn: enabled when ``~/.jarvis/hub/state.db`` exists.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from .registry import registry, tool_error

logger = logging.getLogger(__name__)

# Maximum chars returned per turn text — keeps voice responses compact.
_SNIPPET_MAX = 200
# Default and hard ceiling for result counts.
_DEFAULT_LIMIT = 5
_MAX_LIMIT = 20
# Search limit for LIKE query (scan more rows than we return to allow dedup).
_LIKE_SCAN_LIMIT = 100


def _state_db_path() -> Path:
    return Path(os.environ.get(
        "JARVIS_HUB_DB",
        str(Path.home() / ".jarvis" / "hub" / "state.db"),
    ))


def _snippet(text: str) -> str:
    """Trim text to voice-friendly length."""
    text = (text or "").strip().replace("\n", " ")
    if len(text) > _SNIPPET_MAX:
        return text[:_SNIPPET_MAX] + "…"
    return text


def _format_ts(ts_ms) -> str:
    """Convert Unix milliseconds to a human-readable string."""
    if ts_ms is None:
        return "unknown"
    try:
        return time.strftime("%b %d %H:%M", time.localtime(int(ts_ms) / 1000))
    except Exception:
        return str(ts_ms)


def _open_db(db_path: Path) -> Optional[sqlite3.Connection]:
    """Open state.db read-only (URI mode).  Returns None on failure."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as exc:
        logger.warning("[session_search] could not open state.db: %s", exc)
        return None


# ---------------------------------------------------------------------------
# BROWSE shape — recent sessions
# ---------------------------------------------------------------------------

def _browse(db_path: Path, limit: int) -> str:
    conn = _open_db(db_path)
    if conn is None:
        return tool_error("hub state.db not accessible", success=False)
    try:
        rows = conn.execute(
            """
            SELECT
                m.session_id,
                MIN(m.ts) AS started_ts,
                MAX(m.ts) AS last_ts,
                COUNT(*) AS msg_count,
                (SELECT text FROM messages m2
                 WHERE m2.session_id = m.session_id
                 ORDER BY m2.ts DESC, m2.id DESC LIMIT 1) AS last_text
            FROM messages m
            WHERE m.role IN ('user', 'assistant')
            GROUP BY m.session_id
            ORDER BY last_ts DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except Exception as exc:
        logger.warning("[session_search] browse query failed: %s", exc)
        return tool_error(f"Browse query failed: {exc}", success=False)
    finally:
        conn.close()

    sessions = []
    for r in rows:
        sessions.append({
            "session_id": r["session_id"],
            "started": _format_ts(r["started_ts"]),
            "last_active": _format_ts(r["last_ts"]),
            "message_count": r["msg_count"],
            "preview": _snippet(r["last_text"] or ""),
        })
    return json.dumps({
        "success": True,
        "mode": "browse",
        "count": len(sessions),
        "results": sessions,
        "hint": (
            "Pass query= to search by keyword, or session_id= to read a specific session."
        ),
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# SESSION shape — messages in one session
# ---------------------------------------------------------------------------

def _session(db_path: Path, session_id: str, limit: int) -> str:
    conn = _open_db(db_path)
    if conn is None:
        return tool_error("hub state.db not accessible", success=False)
    try:
        rows = conn.execute(
            "SELECT role, text, ts FROM messages "
            "WHERE session_id = ? AND role IN ('user', 'assistant') "
            "ORDER BY ts ASC, id ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    except Exception as exc:
        logger.warning("[session_search] session query failed: %s", exc)
        return tool_error(f"Session query failed: {exc}", success=False)
    finally:
        conn.close()

    if not rows:
        return json.dumps({
            "success": True,
            "mode": "session",
            "session_id": session_id,
            "count": 0,
            "results": [],
            "hint": "Session not found or has no user/assistant messages.",
        }, ensure_ascii=False)

    messages = [
        {"when": _format_ts(r["ts"]), "role": r["role"], "text": _snippet(r["text"])}
        for r in rows
    ]
    return json.dumps({
        "success": True,
        "mode": "session",
        "session_id": session_id,
        "count": len(messages),
        "results": messages,
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# DISCOVERY shape — substring keyword search
# ---------------------------------------------------------------------------

def _discover(db_path: Path, query: str, limit: int) -> str:
    conn = _open_db(db_path)
    if conn is None:
        return tool_error("hub state.db not accessible", success=False)
    try:
        rows = conn.execute(
            "SELECT id, session_id, role, text, ts FROM messages "
            "WHERE role IN ('user', 'assistant') "
            "AND lower(text) LIKE ? "
            "ORDER BY ts DESC, id DESC LIMIT ?",
            (f"%{query.lower()}%", _LIKE_SCAN_LIMIT),
        ).fetchall()
    except Exception as exc:
        logger.warning("[session_search] discover query failed: %s", exc)
        return tool_error(f"Search query failed: {exc}", success=False)
    finally:
        conn.close()

    if not rows:
        return json.dumps({
            "success": True,
            "mode": "discover",
            "query": query,
            "count": 0,
            "results": [],
            "hint": f"No prior turns mention {query!r}. Try a different keyword.",
        }, ensure_ascii=False)

    results = []
    seen_sessions: set = set()
    for r in rows:
        if len(results) >= limit:
            break
        results.append({
            "when": _format_ts(r["ts"]),
            "session_id": r["session_id"],
            "role": r["role"],
            "snippet": _snippet(r["text"]),
        })
        seen_sessions.add(r["session_id"])

    return json.dumps({
        "success": True,
        "mode": "discover",
        "query": query,
        "count": len(results),
        "sessions_matched": len(seen_sessions),
        "results": results,
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _handle_session_search(args: dict) -> str:
    query: str = (args.get("query") or "").strip()
    session_id: Optional[str] = (args.get("session_id") or "").strip() or None
    raw_limit = args.get("limit", _DEFAULT_LIMIT)
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT
    limit = max(1, min(limit, _MAX_LIMIT))

    db_path = _state_db_path()
    if not db_path.exists():
        return tool_error(
            "hub state.db not found — no conversation history recorded yet.",
            success=False,
        )

    if session_id:
        return _session(db_path, session_id, limit)
    if query:
        return _discover(db_path, query, limit)
    return _browse(db_path, limit)


# ---------------------------------------------------------------------------
# check_fn — enabled when state.db exists
# ---------------------------------------------------------------------------

def _check_session_search() -> bool:
    """Return True when the hub conversation DB exists and is readable."""
    db_path = _state_db_path()
    return db_path.exists()


# ---------------------------------------------------------------------------
# Schema + registration
# ---------------------------------------------------------------------------

_SCHEMA = {
    "name": "session_search",
    "description": (
        "Search or browse past JARVIS conversations stored in the local hub DB.\n\n"
        "THREE CALLING SHAPES\n\n"
        "  1) DISCOVERY — pass query= (keyword search):\n"
        "     session_search(query=\"weather api\", limit=5)\n"
        "     Returns matched turns (role, snippet, timestamp) newest-first.\n\n"
        "  2) BROWSE — no args (recent sessions):\n"
        "     session_search()\n"
        "     Lists recent session IDs with previews and timestamps.\n\n"
        "  3) SESSION — pass session_id= (read a session):\n"
        "     session_search(session_id=\"abc123\", limit=20)\n"
        "     Returns messages for that session, oldest-first.\n\n"
        "Use this for 'what did we talk about yesterday', 'remember that X from before', "
        "'find our conversation about Y'. NOT for stable user facts — those are in memory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Keyword or phrase to search for in past turns. "
                    "Simple substring match — pick distinctive words."
                ),
            },
            "session_id": {
                "type": "string",
                "description": "Session ID to read in full (from a browse result).",
            },
            "limit": {
                "type": "integer",
                "description": f"Max results to return (default {_DEFAULT_LIMIT}, max {_MAX_LIMIT}).",
                "default": _DEFAULT_LIMIT,
            },
        },
        "required": [],
    },
}

registry.register(
    name="session_search",
    schema=_SCHEMA,
    handler=_handle_session_search,
    toolset="session_search",
    check_fn=_check_session_search,
    is_async=False,
    emoji="🔍",
)
