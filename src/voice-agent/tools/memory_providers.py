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
