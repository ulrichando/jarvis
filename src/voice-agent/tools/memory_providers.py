"""JARVIS-native memory provider base + active-provider gate.

Cloud user-modeling memory backends (honcho, mem0, hindsight, ...) register
here under the provider-registry kind "memory". The voice agent's turn loop
currently uses file-backed memory (pipeline/file_memory.py). The cloud provider
layer is wired in by later tasks (pipeline/memory_provider.py) and is off by
default — with JARVIS_MEMORY_PROVIDER unset, nothing changes.
"""
from __future__ import annotations

import abc
import os
from typing import Optional

PROVIDER_KIND = "memory"


class MemoryProvider(abc.ABC):
    """Cloud memory backend. Duck-typed for the provider registry.

    recall/recall_context take a natural-language string and return an opaque
    text block (Honcho returns prose; mem0 concatenates rows). All methods have
    safe defaults so a partial backend never breaks a turn.
    """
    name: str = ""

    @abc.abstractmethod
    def is_available(self) -> bool: ...

    def initialize(self, session_id: str) -> None:
        return None

    def recall(self, query: str) -> str:
        """Deep recall (e.g. Honcho dialectic peer.chat). NL-in, text-out."""
        return ""

    def recall_context(self, hint: str = "") -> str:
        """Cheap recent-context recall (e.g. Honcho session.get_context)."""
        return ""

    def search(self, query: str, limit: int = 8) -> str:
        """Semantic message retrieval (e.g. Honcho embedding search). Returns
        the actual matching past messages as text, or "". Distinct from recall
        (a synthesized answer) — this returns the real messages, ranked by
        meaning, so it finds turns the keyword tools miss."""
        return ""

    def sync_message(self, role: str, text: str) -> None:
        """Ingest one message (role: 'user'|'assistant'). Background-called."""
        return None

    def end_session(self) -> None:
        return None


def active_provider_name() -> Optional[str]:
    """The backend named by JARVIS_MEMORY_PROVIDER, or None (layer off)."""
    name = os.environ.get("JARVIS_MEMORY_PROVIDER", "").strip()
    return name or None


# ---------------------------------------------------------------------------
# recall() registry tool
# ---------------------------------------------------------------------------


def check_recall_available() -> bool:
    """check_fn: a memory provider is configured + available."""
    from pipeline import memory_provider  # lazy — avoids import cycle at module load
    return memory_provider.active_provider() is not None


async def _handle_recall(args: dict) -> str:
    query = (args.get("query") or "").strip() if isinstance(args, dict) else ""
    if not query:
        from tools.registry import tool_error
        return tool_error("recall requires a 'query' (what to look up about the user/past).")
    mode = str(args.get("mode") or "answer").strip().lower() if isinstance(args, dict) else "answer"
    from pipeline import memory_provider
    import asyncio
    if mode == "search":
        res = await asyncio.to_thread(memory_provider.search_for_query, query)
        # Non-authoritative empty result: an empty return can mean "no close
        # match" OR "backend offline" — never let the model treat it as proof
        # the topic was never discussed (that persists a denial via dual-sync).
        return res or (
            "No close semantic matches surfaced for that query. This does NOT "
            "mean the topic was never discussed — semantic search only returns "
            "close matches; try recall mode='answer' or reword the query."
        )
    res = await asyncio.to_thread(memory_provider.recall_for_query, query)
    return res or "No relevant memory found."


_RECALL_SCHEMA = {
    "name": "recall",
    "description": (
        "Look up what you know about the user from past conversations (cross-session "
        "memory). Two modes:\n"
        "- mode='answer' (default): a synthesized answer that reasons over the user "
        "model — for 'what did I tell you about X', 'remember when…'. May take a moment.\n"
        "- mode='search': the actual past MESSAGES most related in MEANING to the query "
        "(semantic, not keyword) — fast, verbatim quotes with speaker labels. Use when you "
        "want the real wording of what was said, or when the keyword tools (session_search) "
        "miss it because the user phrased it differently now.\n"
        "For facts already in the current chat, just answer directly."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language question or topic about the user or past context.",
            },
            "mode": {
                "type": "string",
                "enum": ["answer", "search"],
                "description": "'answer' = synthesized reasoning (default); 'search' = verbatim semantic message matches.",
                "default": "answer",
            },
        },
        "required": ["query"],
    },
}

# NOTE: must be literally `registry.register(...)` — the AST discovery in
# tools/registry.py::_is_registry_register_call only matches that exact shape,
# and an aliased name left this module invisible to discover_builtin_tools
# (recall then only existed via the honcho plugin's import side-effect).
from tools.registry import registry  # noqa: E402 — module-level registration

registry.register(
    name="recall",
    schema=_RECALL_SCHEMA,
    handler=_handle_recall,
    toolset="memory",
    check_fn=check_recall_available,
    requires_env=["JARVIS_MEMORY_PROVIDER"],
    is_async=True,
    emoji="🧠",
    max_result_size_chars=8_000,
)
