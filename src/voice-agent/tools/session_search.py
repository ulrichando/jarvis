"""Session search tool — recall past conversations by keyword.

Full logic (FTS5 discovery, scroll, browse) is preserved from the upstream
design. The tool is registered with a check_fn that currently returns False
because JARVIS has no wired session DB yet.

To enable:
  1. Wire a JARVIS-native conversation DB that exposes:
     search_messages, get_anchored_view, get_messages_around,
     get_session, list_sessions_rich.
  2. Remove or replace the check_fn so is_available() returns True.
  3. Pass ``db=<your_db_instance>`` through the handler kwargs or wire it
     via a module-level factory in check_fn.

Until wired, load_all_livekit_tools() will log a warning and skip this
tool. No import errors, no surface breakage — just "not yet available".

Upstream simplifications:
  - No external session store import (check_fn gates on unavailability).
  - _HIDDEN_SESSION_SOURCES = ("tool",) preserved as-is.
  - Unavailability message is inline (no upstream-specific import needed).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Union

from .registry import registry, tool_error

logger = logging.getLogger(__name__)

_HIDDEN_SESSION_SOURCES = ("tool",)


# ---------------------------------------------------------------------------
# Helper utilities (upstream logic verbatim, no hermes references)
# ---------------------------------------------------------------------------

def _format_timestamp(ts: Union[int, float, str, None]) -> str:
    """Convert a Unix timestamp or ISO string to a human-readable date."""
    if ts is None:
        return "unknown"
    try:
        if isinstance(ts, (int, float)):
            from datetime import datetime
            dt = datetime.fromtimestamp(ts)
            return dt.strftime("%B %d, %Y at %I:%M %p")
        if isinstance(ts, str):
            if ts.replace(".", "").replace("-", "").isdigit():
                from datetime import datetime
                dt = datetime.fromtimestamp(float(ts))
                return dt.strftime("%B %d, %Y at %I:%M %p")
            return ts
    except (ValueError, OSError, OverflowError) as e:
        logger.debug("Failed to format timestamp %s: %s", ts, e, exc_info=True)
    except Exception as e:
        logger.debug("Unexpected error formatting timestamp %s: %s", ts, e, exc_info=True)
    return str(ts)


def _resolve_to_parent(db, session_id: str) -> str:
    """Walk parent_session_id chain to the lineage root."""
    if not session_id:
        return session_id
    visited = set()
    cur = session_id
    while cur and cur not in visited:
        visited.add(cur)
        try:
            s = db.get_session(cur)
            if not s:
                break
            parent = s.get("parent_session_id")
            if not parent:
                break
            cur = parent
        except Exception as e:
            logger.debug("Error resolving parent for %s: %s", cur, e, exc_info=True)
            break
    return cur


def _shape_message(m: Dict[str, Any], anchor_id: Optional[int] = None) -> Dict[str, Any]:
    """Slim a message row for the tool response."""
    entry = {
        "id": m.get("id"),
        "role": m.get("role"),
        "content": m.get("content"),
        "timestamp": m.get("timestamp"),
    }
    if m.get("tool_name"):
        entry["tool_name"] = m.get("tool_name")
    if m.get("tool_calls"):
        entry["tool_calls"] = m.get("tool_calls")
    if m.get("tool_call_id"):
        entry["tool_call_id"] = m.get("tool_call_id")
    if anchor_id is not None and m.get("id") == anchor_id:
        entry["anchor"] = True
    return {k: v for k, v in entry.items() if v is not None or k in ("content",)}


def _list_recent_sessions(db, limit: int, current_session_id: str = None) -> str:
    """Return metadata for the most recent sessions (BROWSE shape)."""
    try:
        sessions = db.list_sessions_rich(
            limit=limit + 5,
            exclude_sources=list(_HIDDEN_SESSION_SOURCES),
            order_by_last_active=True,
        )

        current_root = _resolve_to_parent(db, current_session_id) if current_session_id else None

        results = []
        for s in sessions:
            sid = s.get("id", "")
            if current_root and (sid == current_root or sid == current_session_id):
                continue
            if s.get("parent_session_id"):
                continue
            results.append({
                "session_id": sid,
                "title": s.get("title") or None,
                "source": s.get("source", ""),
                "started_at": s.get("started_at", ""),
                "last_active": s.get("last_active", ""),
                "message_count": s.get("message_count", 0),
                "preview": s.get("preview", ""),
            })
            if len(results) >= limit:
                break

        return json.dumps({
            "success": True,
            "mode": "browse",
            "results": results,
            "count": len(results),
            "message": (
                f"Showing {len(results)} most recent sessions. "
                "Pass a query= to search, or session_id+around_message_id to scroll."
            ),
        }, ensure_ascii=False)
    except Exception as e:
        logger.error("Error listing recent sessions: %s", e, exc_info=True)
        return tool_error(f"Failed to list recent sessions: {e}", success=False)


def _scroll(
    db,
    session_id: str,
    around_message_id: int,
    window: int = 5,
    current_session_id: str = None,
) -> str:
    """SCROLL shape: return a window of messages centered on an anchor."""
    if not isinstance(session_id, str) or not session_id.strip():
        return tool_error("scroll requires session_id", success=False)
    session_id = session_id.strip()

    try:
        around_message_id = int(around_message_id)
    except (TypeError, ValueError):
        return tool_error("scroll requires integer around_message_id", success=False)

    window = max(1, min(int(window) if isinstance(window, (int, float)) else 5, 20))

    if current_session_id:
        a_root = _resolve_to_parent(db, session_id)
        c_root = _resolve_to_parent(db, current_session_id)
        if a_root and c_root and a_root == c_root:
            return tool_error(
                "scroll rejected: anchor lives in the current session lineage "
                "(already in your active context)",
                success=False,
            )

    try:
        session_meta = db.get_session(session_id) or {}
    except Exception as e:
        logger.debug("get_session failed for %s: %s", session_id, e, exc_info=True)
        session_meta = {}
    if not session_meta:
        return tool_error(f"session_id not found: {session_id}", success=False)

    try:
        view = db.get_messages_around(session_id, around_message_id, window=window)
    except Exception as e:
        logger.error("get_messages_around failed: %s", e, exc_info=True)
        return tool_error(f"failed to load messages: {e}", success=False)

    messages = view.get("window") or []

    # Lineage rebind — caller may have paired a parent session_id with a
    # message id that lives in a child session.
    rebind_warning = None
    if not messages:
        owning = None
        try:
            conn = getattr(db, "_conn", None)
            if conn is not None:
                row = conn.execute(
                    "SELECT session_id FROM messages WHERE id = ?",
                    (around_message_id,),
                ).fetchone()
                owning = row[0] if row else None
        except Exception as e:
            logger.debug("owning-session lookup failed: %s", e, exc_info=True)
        if owning and owning != session_id:
            a_root = _resolve_to_parent(db, session_id)
            o_root = _resolve_to_parent(db, owning)
            if a_root and o_root and a_root == o_root:
                try:
                    rebind_view = db.get_messages_around(owning, around_message_id, window=window)
                    messages = rebind_view.get("window") or []
                    if messages:
                        view = rebind_view
                        rebind_warning = (
                            f"around_message_id {around_message_id} lives in {owning} "
                            f"(child of {session_id}); rebound transparently"
                        )
                        try:
                            session_meta = db.get_session(owning) or session_meta
                        except Exception:
                            pass
                        session_id = owning
                except Exception as e:
                    logger.debug("rebind get_messages_around failed: %s", e, exc_info=True)

    if not messages:
        return tool_error(
            f"around_message_id {around_message_id} not in session_id {session_id}",
            success=False,
        )

    response = {
        "success": True,
        "mode": "scroll",
        "session_id": session_id,
        "around_message_id": around_message_id,
        "session_meta": {
            "when": _format_timestamp(session_meta.get("started_at")),
            "source": session_meta.get("source"),
            "model": session_meta.get("model"),
            "title": session_meta.get("title"),
        },
        "window": window,
        "messages": [_shape_message(m, anchor_id=around_message_id) for m in messages],
        "messages_before": view.get("messages_before", 0),
        "messages_after": view.get("messages_after", 0),
    }
    if rebind_warning:
        response["warning"] = rebind_warning
    return json.dumps(response, ensure_ascii=False)


def _discover(
    db,
    query: str,
    role_filter: Optional[List[str]],
    limit: int,
    sort: Optional[str],
    current_session_id: str = None,
) -> str:
    """DISCOVERY shape: FTS5 + anchored window + bookends per hit."""
    role_list = role_filter if role_filter else ["user", "assistant"]

    try:
        raw_results = db.search_messages(
            query=query,
            role_filter=role_list,
            exclude_sources=list(_HIDDEN_SESSION_SOURCES),
            limit=50,
            offset=0,
            sort=sort,
        )
    except Exception as e:
        logger.error("FTS5 search failed: %s", e, exc_info=True)
        return tool_error(f"Search failed: {e}", success=False)

    if not raw_results:
        return json.dumps({
            "success": True,
            "mode": "discover",
            "query": query,
            "results": [],
            "count": 0,
            "message": "No matching sessions found.",
        }, ensure_ascii=False)

    current_lineage_root = (
        _resolve_to_parent(db, current_session_id) if current_session_id else None
    )

    seen_sessions: Dict[str, Any] = {}
    for r in raw_results:
        raw_sid = r["session_id"]
        resolved_sid = _resolve_to_parent(db, raw_sid)
        if current_lineage_root and resolved_sid == current_lineage_root:
            continue
        if current_session_id and raw_sid == current_session_id:
            continue
        if resolved_sid not in seen_sessions:
            row = dict(r)
            row["_lineage_root"] = resolved_sid
            seen_sessions[resolved_sid] = row
        if len(seen_sessions) >= limit:
            break

    results = []
    for lineage_root, match_info in seen_sessions.items():
        hit_sid = match_info.get("session_id") or lineage_root
        msg_id = match_info.get("id")
        try:
            view = db.get_anchored_view(hit_sid, msg_id, window=5, bookend=3)
        except Exception as e:
            logger.warning(
                "get_anchored_view failed for %s/%s: %s", hit_sid, msg_id, e, exc_info=True
            )
            continue

        try:
            session_meta = db.get_session(lineage_root) or {}
        except Exception:
            session_meta = {}

        entry = {
            "session_id": hit_sid,
            "when": _format_timestamp(
                session_meta.get("started_at") or match_info.get("session_started")
            ),
            "source": session_meta.get("source") or match_info.get("source", "unknown"),
            "model": session_meta.get("model") or match_info.get("model") or "unknown",
            "title": session_meta.get("title") or None,
            "matched_role": match_info.get("role"),
            "match_message_id": msg_id,
            "snippet": match_info.get("snippet") or "",
            "bookend_start": [_shape_message(m) for m in (view.get("bookend_start") or [])],
            "messages": [
                _shape_message(m, anchor_id=msg_id) for m in (view.get("window") or [])
            ],
            "bookend_end": [_shape_message(m) for m in (view.get("bookend_end") or [])],
            "messages_before": view.get("messages_before", 0),
            "messages_after": view.get("messages_after", 0),
        }
        if lineage_root and lineage_root != hit_sid:
            entry["parent_session_id"] = lineage_root
        results.append(entry)

    return json.dumps({
        "success": True,
        "mode": "discover",
        "query": query,
        "results": results,
        "count": len(results),
        "sessions_searched": len(seen_sessions),
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def session_search(
    query: str = "",
    role_filter: str = None,
    limit: int = 3,
    db=None,
    current_session_id: str = None,
    session_id: str = None,
    around_message_id: int = None,
    window: int = 5,
    sort: str = None,
) -> str:
    """Single-shape entry point. Mode inferred from which args are set.

    Scroll wins over discovery when both are set.
    """
    if db is None:
        return tool_error(
            "session_search is not yet wired to a JARVIS conversation DB. "
            "This tool is registered but disabled (check_fn=False). "
            "To enable, provide a db= argument or wire a JARVIS session store.",
            success=False,
        )

    # Scroll shape takes precedence.
    if (isinstance(session_id, str) and session_id.strip()) and around_message_id is not None:
        return _scroll(
            db=db,
            session_id=session_id,
            around_message_id=around_message_id,
            window=window,
            current_session_id=current_session_id,
        )

    # Limit clamp [1, 10]
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 3
    limit = max(1, min(limit, 10))

    # Browse shape: no query → recent sessions.
    if not query or not isinstance(query, str) or not query.strip():
        return _list_recent_sessions(db, limit, current_session_id)

    # Parse role_filter
    role_list: Optional[List[str]] = None
    if isinstance(role_filter, str) and role_filter.strip():
        role_list = [r.strip() for r in role_filter.split(",") if r.strip()]

    sort_norm: Optional[str] = None
    if isinstance(sort, str):
        candidate = sort.strip().lower()
        if candidate in ("newest", "oldest"):
            sort_norm = candidate

    return _discover(
        db=db,
        query=query.strip(),
        role_filter=role_list,
        limit=limit,
        sort=sort_norm,
        current_session_id=current_session_id,
    )


# ---------------------------------------------------------------------------
# check_fn — returns False until a JARVIS session DB is wired
# ---------------------------------------------------------------------------

def _check_session_search() -> bool:
    """Disabled until JARVIS wires a conversation DB for this tool.

    To enable: implement a JARVIS-native session store compatible with the
    upstream SessionDB interface (search_messages, get_anchored_view,
    get_messages_around, get_session, list_sessions_rich) and update this
    function to return True when that store is available.
    """
    return False


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = {
    "name": "session_search",
    "description": (
        "Search past conversations stored in the local session DB, or scroll "
        "inside one. FTS5-backed retrieval over the SQLite message store. No LLM "
        "calls — every shape returns actual messages from the DB.\n\n"
        "THREE CALLING SHAPES\n\n"
        "  1) DISCOVERY — pass `query`:\n"
        "     session_search(query=\"auth refactor\", limit=3)\n"
        "     Runs FTS5, dedupes by session lineage, returns top N sessions each "
        "with snippet, ±5 message window, and bookend start/end.\n\n"
        "  2) SCROLL — pass `session_id` + `around_message_id`:\n"
        "     session_search(session_id=\"...\", around_message_id=12345, window=10)\n"
        "     Returns a window of ±window messages centered on the anchor. Use to "
        "read more context after a discovery call.\n\n"
        "  3) BROWSE — no args:\n"
        "     session_search()\n"
        "     Returns recent sessions chronologically: titles, previews, timestamps.\n\n"
        "FTS5 SYNTAX: AND is default; use OR for broader recall, quoted phrases "
        "for exact match, boolean NOT, or prefix wildcards (deploy*).\n\n"
        "NOTE: This tool is currently disabled (no JARVIS session DB wired)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search query (discovery shape). Keywords, phrases, or boolean "
                    "expressions. Omit to browse recent sessions."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Discovery shape only. Max sessions to return (default 3, max 10).",
                "default": 3,
            },
            "sort": {
                "type": "string",
                "enum": ["newest", "oldest"],
                "description": (
                    "Discovery shape only. 'newest' for recency-shaped questions, "
                    "'oldest' for origin-shaped questions."
                ),
            },
            "session_id": {
                "type": "string",
                "description": "Scroll shape. Session to read inside. Must be paired with around_message_id.",
            },
            "around_message_id": {
                "type": "integer",
                "description": "Scroll shape. Message id to center the window on.",
            },
            "window": {
                "type": "integer",
                "description": "Scroll shape only. Messages on each side of anchor. Clamped [1,20]. Default 5.",
                "default": 5,
            },
            "role_filter": {
                "type": "string",
                "description": (
                    "Optional. Comma-separated roles to include. Discovery defaults to "
                    "'user,assistant'."
                ),
            },
        },
        "required": [],
    },
}

registry.register(
    name="session_search",
    schema=_SCHEMA,
    handler=lambda args, **kw: session_search(
        query=args.get("query") or "",
        role_filter=args.get("role_filter"),
        limit=args.get("limit", 3),
        session_id=args.get("session_id"),
        around_message_id=args.get("around_message_id"),
        window=args.get("window", 5),
        sort=args.get("sort"),
        db=kw.get("db"),
        current_session_id=kw.get("current_session_id"),
    ),
    toolset="session_search",
    check_fn=_check_session_search,
    is_async=False,
    emoji="🔍",
)
