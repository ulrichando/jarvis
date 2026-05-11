"""Memory recall subagent registration.

Search past conversations stored in ~/.jarvis/conversations.db.
Pattern lineage: ChatGPT/Claude memory tools, but local + privacy-
preserving. SQLite LIKE-match — no embeddings, no external service.

DelegatedSubagent because recall is one-shot: input is a query string,
output is a formatted voice summary of matches.
"""
from __future__ import annotations

from .registry import DelegatedSubagent, register_subagent
from ._ack_phrases import ACK_MEMORY_RECALL


MEMORY_RECALL_INSTRUCTIONS = """\
You are JARVIS's memory subagent. Your one job: call `recall(query,
days?, limit?)` ONCE with the user's search intent, then report the
result via task_done.

The supervisor delegates a string of the form:
  <QUERY>topic, name, or keywords to search</QUERY>
  <DAYS>look-back window in days, optional (default 30)</DAYS>
  <LIMIT>max results, optional (default 5)</LIMIT>

Rules:
  - Pass the recall result text through verbatim. The supervisor
    decides how to voice it.
  - If the input is empty or malformed, return "UNCLEAR: no query".
  - Don't reason about what the user "really meant" — just search.
"""


def _memory_recall_tools() -> list:
    """Lazy import — avoids the SQLite connection setup at registry-
    load time. `task_done` is auto-attached."""
    from tools.memory_recall import recall
    return [recall]


_MEMORY_RECALL_WHEN = (
    "Use when the user asks about something from past conversations: "
    "\"what did we discuss about X\", \"when did I tell you about Y\", "
    "\"remind me what I decided about Z\", \"have we talked about this "
    "before\", \"what was my position on X\". Searches "
    "~/.jarvis/conversations.db (the local conversation log) by "
    "keyword. Returns a voice-formatted summary of matches with "
    "timestamps. Auto-disabled if the DB doesn't exist yet."
)


def register_memory_recall() -> None:
    """Register the memory-recall subagent. Auto-disables if the
    conversations DB hasn't been created (first-run scenario).

    DISABLED BY DEFAULT 2026-05-08 — opt in with `JARVIS_SUBAGENT_MEMORY_RECALL=1`.
    Disabled alongside summarize/researcher etc. while supervisor delegate
    routing is being repaired. (The 4-layer auto-extract memory layer
    handles recall on its own — see pipeline/memory_extractor.py.)
    """
    import os
    try:
        from tools.memory_recall import is_available
        enabled = is_available()
    except Exception:
        enabled = False
    enabled = enabled and (
        os.environ.get("JARVIS_SUBAGENT_MEMORY_RECALL", "0") == "1"
    )

    register_subagent(DelegatedSubagent(
        name="memory_recall",
        when_to_use=_MEMORY_RECALL_WHEN,
        instructions=MEMORY_RECALL_INSTRUCTIONS,
        tool_factory=_memory_recall_tools,
        ack_phrase=ACK_MEMORY_RECALL,
        max_history_items=4,
        enabled=enabled,
    ))
