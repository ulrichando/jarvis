"""Session search tool — search past conversations from the telemetry DB.

Provides a ``session_search`` tool the supervisor can call to find what was
discussed in previous turns. Queries the ``turns`` table in
``turn_telemetry.db`` (same DB the pipeline writes to on every turn).

Because every user utterance and every assistant reply is logged to
``turn_telemetry.db``, this tool makes JARVIS's entire conversation history
searchable without any separate indexing or embedding pipeline.

Design:
- Self-registering: discovered by ``tools._adapter.load_all_livekit_tools``.
- Read-only SQLite queries (``?mode=ro``) — never writes to the telemetry DB.
- ``LIKE %query%`` search (no FTS5 — JARVIS's turn volume is low enough that
  a full-table scan completes in <100ms on any hardware JARVIS runs on).
- Results are returned as a JSON array the supervisor can read directly.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.registry import registry, tool_error

logger = logging.getLogger("jarvis.session_search")

# ── DB path resolution ──────────────────────────────────────────────────

# Match pipeline/turn_telemetry.py's DEFAULT_DB_PATH logic without importing
# it (avoid coupling to pipeline internals from a tool module).
_TELEMETRY_DB: Path | None = None


def _get_db_path() -> Path:
    global _TELEMETRY_DB
    if _TELEMETRY_DB is not None:
        return _TELEMETRY_DB
    env_path = os.environ.get("JARVIS_TELEMETRY_PATH", "").strip()
    if env_path:
        _TELEMETRY_DB = Path(env_path).expanduser().resolve()
    else:
        _TELEMETRY_DB = (
            Path.home()
            / ".local"
            / "share"
            / "jarvis"
            / "turn_telemetry.db"
        )
    return _TELEMETRY_DB


# ── Schema ──────────────────────────────────────────────────────────────

SEARCH_SCHEMA: dict[str, Any] = {
    "name": "session_search",
    "description": "Search past conversations for a phrase or topic. "
    "Use this when the user asks about something you discussed before, "
    "or you need to recall details from an earlier conversation turn. "
    "Returns matching turns with timestamps, what the user said, and "
    "what you replied. Results are ordered newest-first.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search term or phrase to find in past conversations. "
                "Searches both what the user said and what you replied. "
                "Case-insensitive.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return (1-20). Default 5.",
                "default": 5,
            },
            "days_back": {
                "type": "integer",
                "description": "How far back to search in days. Default 30. Set to 0 for no limit.",
                "default": 30,
            },
            "focus": {
                "type": "string",
                "enum": ["user", "jarvis", "both"],
                "description": "Which side of the conversation to search. "
                '"user" = only what the user said, "jarvis" = only your replies, '
                '"both" = either. Default "both".',
                "default": "both",
            },
        },
        "required": ["query"],
    },
}


# ── Handler ─────────────────────────────────────────────────────────────


def _handle_session_search(args: dict) -> str:
    """Query the telemetry DB for matching conversation turns."""
    query = str(args.get("query", "")).strip()
    if not query:
        return tool_error("query is required", success=False)

    limit = min(max(int(args.get("limit", 5)), 1), 20)
    days_back = int(args.get("days_back", 30))
    focus = str(args.get("focus", "both")).strip().lower()

    db_path = _get_db_path()
    if not db_path.exists():
        return tool_error("Telemetry database not found — no conversation history yet.", success=False)

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Build WHERE clause.
        like_pattern = f"%{query}%"
        conditions: list[str] = []
        params: list[str] = []

        if focus == "user":
            conditions.append("user_text LIKE ?")
            params.append(like_pattern)
        elif focus == "jarvis":
            conditions.append("jarvis_text LIKE ?")
            params.append(like_pattern)
        else:
            conditions.append("(user_text LIKE ? OR jarvis_text LIKE ?)")
            params.extend([like_pattern, like_pattern])

        if days_back > 0:
            conditions.append("ts_utc >= datetime('now', ?)")
            params.append(f"-{days_back} days")

        where = " AND ".join(conditions)

        cursor.execute(
            f"SELECT ts_utc, user_text, jarvis_text, route, emotion, llm_used "
            f"FROM turns WHERE {where} "
            f"ORDER BY ts_utc DESC LIMIT ?",
            [*params, limit],
        )

        rows = cursor.fetchall()
        conn.close()

        results = []
        for row in rows:
            results.append({
                "ts_utc": row["ts_utc"],
                "user_text": row["user_text"],
                "jarvis_text": row["jarvis_text"],
                "route": row["route"],
                "emotion": row["emotion"],
                "llm_used": row["llm_used"],
            })

        return json.dumps({
            "status": "ok",
            "count": len(results),
            "results": results,
        }, ensure_ascii=False)

    except sqlite3.Error as exc:
        logger.exception("session_search query failed")
        return tool_error(f"Database query failed: {exc}", success=False)
    except Exception as exc:
        logger.exception("session_search unexpected error")
        return tool_error(f"Unexpected error: {exc}", success=False)


# ── Registration ────────────────────────────────────────────────────────

registry.register(
    name="session_search",
    schema=SEARCH_SCHEMA,
    handler=lambda args, **_kw: _handle_session_search(args),
    is_async=False,
    emoji="🔍",
    description="Search past conversations for a phrase or topic. "
    "Useful when the user asks about something you discussed before.",
)
