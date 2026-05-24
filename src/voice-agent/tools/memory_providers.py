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
    from pipeline import memory_provider
    import asyncio
    res = await asyncio.to_thread(memory_provider.recall_for_query, query)
    return res or "No relevant memory found."


_RECALL_SCHEMA = {
    "name": "recall",
    "description": (
        "Look up what you know about the user from past conversations (cross-session "
        "memory). Use for 'what did I tell you about X', 'remember when…', or when you "
        "need durable context the current chat doesn't contain. Returns a synthesized "
        "answer; may take a moment. For facts in the current chat, just answer directly."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language question about the user or past context.",
            }
        },
        "required": ["query"],
    },
}

from tools.registry import registry as _registry  # noqa: E402 — module-level registration

_registry.register(
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
