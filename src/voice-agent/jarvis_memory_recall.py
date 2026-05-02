"""Memory recall — search past conversations stored in
~/.jarvis/conversations.db (SQLite, written by jarvis_agent.py's
conversation_item_added handler).

Pattern: ChatGPT/Claude memory tool, but local. The DB already has
hundreds of turns indexed by (session_id, ts, role, text). This
subagent exposes that history to the supervisor as a single tool:
"what did we discuss about X" / "when did I tell you about Y".

Implementation: SQLite LIKE-match (fast + dependency-free). For
semantic search we'd need embeddings — defer until the LIKE path
proves insufficient. Voice users rarely query for fuzzy semantic
similarity; they query for specific keywords.

Existing recall_conversation tool in jarvis_agent.py covers a similar
purpose but with a different shape. This subagent is callable via the
delegate(role, task) plumbing — keeps the supervisor's prompt slim.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

from livekit.agents.llm import function_tool

logger = logging.getLogger("jarvis.memory_recall")

_CONVO_DB = Path.home() / ".jarvis" / "conversations.db"


def _connect():
    """Open the conversations DB read-only. The agent process holds a
    write connection elsewhere; we use a fresh read-only handle so we
    don't fight for locks."""
    return sqlite3.connect(
        f"file:{_CONVO_DB}?mode=ro", uri=True, isolation_level=None
    )


def _format_when(ts: int) -> str:
    """Voice-friendly timestamp. 'today 2 PM', 'yesterday 5 PM',
    'last Thursday', '3 days ago'. Skip the year/seconds — the LLM
    will incorporate this verbatim into its reply."""
    when = _dt.datetime.fromtimestamp(ts)
    today = _dt.date.today()
    delta_days = (today - when.date()).days
    hour_str = when.strftime("%-I %p").lower().lstrip("0").replace(" ", "")  # "2pm"
    if delta_days == 0:
        return f"today around {hour_str}"
    if delta_days == 1:
        return f"yesterday around {hour_str}"
    if delta_days < 7:
        return when.strftime("%A").lower() + f" around {hour_str}"
    if delta_days < 30:
        return f"{delta_days} days ago"
    return when.strftime("%B %-d") + f" around {hour_str}"


def _condense_text(text: str, max_chars: int = 200) -> str:
    """Trim each turn's text to a voice-readable size. Keeps first
    sentence-ish; truncates at word boundary."""
    text = text.strip().replace("\n", " ")
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    last_space = cut.rfind(" ")
    if last_space > max_chars - 40:
        cut = cut[:last_space]
    return cut + "…"


@function_tool
async def recall(query: str, days: int = 30, limit: int = 5) -> str:
    """Search past conversations for a topic, name, or quote. Use
    when the user asks "what did we discuss about X" / "when did I
    tell you about Y" / "remind me what I decided about Z" / "have
    we talked about this before".

    Args:
        query: Natural-language search term. Best with concrete
               keywords (names, projects, dates) rather than abstract
               concepts. Multiple keywords narrow results.
        days: Look-back window in days (default 30, max 365).
        limit: Max matches to return (default 5, max 15).

    Returns:
        Voice-formatted summary like:
          "Yesterday around 4 pm you asked about X. I replied: '<gist>'.
           Last Tuesday you mentioned Y..."
        Or "no matches in the last N days" if nothing found.
    """
    if not query or not query.strip():
        return "(no search query — pass a keyword or topic)"

    days = max(1, min(int(days or 30), 365))
    limit = max(1, min(int(limit or 5), 15))
    cutoff = int(time.time()) - days * 86400

    if not _CONVO_DB.exists():
        return "(no conversation history yet — DB not created)"

    # Tokenize the query into words; require ALL non-stopword tokens
    # to appear (AND match) for tighter relevance. Falls back to the
    # whole-string match if tokenization yields nothing useful.
    raw = query.strip()
    tokens = [
        t.lower() for t in re.findall(r"[A-Za-z0-9]+", raw)
        if len(t) >= 3 and t.lower() not in _STOPWORDS
    ]
    if not tokens:
        # Short or all-stopword query — match the whole string.
        tokens = [raw.lower()]

    where_clauses = " AND ".join(["LOWER(text) LIKE ?"] * len(tokens))
    params = [f"%{t}%" for t in tokens]

    sql = (
        f"SELECT session_id, ts, role, text FROM turns "
        f"WHERE ts >= ? AND ({where_clauses}) "
        f"ORDER BY ts DESC LIMIT ?"
    )
    try:
        with _connect() as conn:
            rows = conn.execute(sql, [cutoff, *params, limit]).fetchall()
    except Exception as e:
        logger.warning("[recall] DB query failed: %s", e)
        return f"(recall failed: {e})"

    if not rows:
        return f"No matches for {query!r} in the last {days} days, sir."

    # Group by session so adjacent user→assistant turns voice as a
    # single exchange instead of two separate hits.
    out_lines = []
    seen_sessions = set()
    for sid, ts, role, text in rows:
        when = _format_when(ts)
        snippet = _condense_text(text)
        if role == "user":
            label = "You said"
        else:
            label = "I replied"
        line = f"{when}, {label}: '{snippet}'"
        # Dedupe per-session — voice doesn't need 5 quotes from one chat.
        key = (sid, role)
        if key in seen_sessions:
            continue
        seen_sessions.add(key)
        out_lines.append(line)
        if len(out_lines) >= limit:
            break

    return "Found in the conversations:\n  - " + "\n  - ".join(out_lines)


# Common English stopwords — pruned to ones that recur in voice
# transcripts. Curated rather than imported (no nltk dep needed).
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on",
    "at", "for", "with", "by", "from", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her",
    "us", "them", "my", "your", "his", "its", "our", "their",
    "this", "that", "these", "those", "what", "when", "where", "why",
    "how", "who", "which", "all", "any", "some", "if", "then", "than",
    "so", "too", "very", "just", "also", "about", "tell", "told",
    "say", "said", "ask", "asked", "did", "doing", "done",
}


def is_available() -> bool:
    """True if the conversations DB exists. Otherwise gracefully
    disables (won't show up in supervisor's tool list)."""
    return _CONVO_DB.exists()
