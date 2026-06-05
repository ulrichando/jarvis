"""Conversation recall tool — search persisted voice conversations.

Provides a ``recall_conversation`` tool the supervisor can call to search
across past sessions for topics, phrases, or user details. Queries the
``messages`` table in ``~/.jarvis/conversations.db`` (written by
``pipeline.conversation_store`` on every voice turn).

Self-registering: discovered by ``tools._adapter.load_all_livekit_tools``.

Design:
  - Read-only SQLite queries (``?mode=ro`` URI) — never writes.
  - ``LIKE %query%`` search — no FTS5; turn volume is low enough that a
    full scan completes in <100ms.
  - Returns JSON with session context per match (title, created, role,
    text, timestamp, turn_sequence).
  - Optional ``session_id`` filter for narrowing results.
  - Not available when ``JARVIS_CONVERSATION_PATH`` points to a
    non-existent DB (check_fn returns False → tool not offered).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from tools.registry import registry, tool_error

logger = logging.getLogger("jarvis.conversation_recall")

# ── DB path resolution ──────────────────────────────────────────────────

_CONVERSATION_DB: Path | None = None


def _get_db_path() -> Path:
    """Resolve conversations.db path, cached at module level."""
    global _CONVERSATION_DB
    if _CONVERSATION_DB is not None:
        return _CONVERSATION_DB
    env_path = os.environ.get("JARVIS_CONVERSATION_PATH", "").strip()
    if env_path:
        _CONVERSATION_DB = Path(env_path).expanduser().resolve()
    else:
        _CONVERSATION_DB = Path.home() / ".jarvis" / "conversations.db"
    return _CONVERSATION_DB


# ── Check ───────────────────────────────────────────────────────────────

def is_available() -> bool:
    """Tool is available when the conversations DB exists."""
    return _get_db_path().exists()


# ── Schema ──────────────────────────────────────────────────────────────

RECALL_SCHEMA: dict[str, Any] = {
    "name": "recall_conversation",
    "description": (
        "Search your conversation history across past sessions. "
        "Use this when the user references something you discussed before "
        "(\"what did we talk about yesterday?\"), when they ask if you "
        "remember a past topic, or when you need context from a prior "
        "session that isn't in your current chat or memory files. "
        "Returns matching messages with session context (title, when it "
        "happened, who said what). Results are newest-first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "The search term or phrase to find in past conversations. "
                    "Searches both user and assistant messages. "
                    "Case-insensitive. Use short, distinctive terms "
                    "(e.g. 'deploy' rather than 'how do I deploy')."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results (1-20). Default 5.",
                "default": 5,
            },
            "session_id": {
                "type": "string",
                "description": (
                    "Optional: restrict search to a specific session ID. "
                    "Use this when following up on a prior search result."
                ),
            },
        },
        "required": ["query"],
    },
}


# ── Handler ─────────────────────────────────────────────────────────────

def _handle_recall(args: dict) -> str:
    """Execute a recall_conversation search against conversations.db."""
    query = (args.get("query") or "").strip() if isinstance(args, dict) else ""
    if not query:
        return tool_error("recall_conversation requires a 'query' (what to search for).")

    limit = int(args.get("limit", 5)) if isinstance(args, dict) else 5
    limit = max(1, min(limit, 20))

    session_id = None
    if isinstance(args, dict):
        raw_sid = args.get("session_id")
        if raw_sid and str(raw_sid).strip():
            session_id = str(raw_sid).strip()

    db_path = _get_db_path()
    if not db_path.exists():
        return tool_error(
            "No conversation history found — conversations.db doesn't exist yet. "
            "This means no voice turns have been persisted.",
            success=False,
        )

    try:
        from pipeline.conversation_store import recall_conversation

        results = recall_conversation(
            query=query, db_path=db_path, limit=limit, session_id=session_id
        )
    except Exception as e:
        logger.warning("[recall_conversation] search failed: %s", e)
        return tool_error(f"Search failed: {e}")

    if not results:
        return json.dumps(
            {
                "success": True,
                "query": query,
                "results": [],
                "message": f"No matches found for '{query}' in past conversations.",
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "success": True,
            "query": query,
            "result_count": len(results),
            "results": results,
        },
        ensure_ascii=False,
    )


# ── Registration ────────────────────────────────────────────────────────

registry.register(
    name="recall_conversation",
    schema=RECALL_SCHEMA,
    handler=lambda args, **_kw: _handle_recall(args),
    toolset="memory",
    check_fn=is_available,
    is_async=False,
    emoji="💬",
    max_result_size_chars=8_000,
)
