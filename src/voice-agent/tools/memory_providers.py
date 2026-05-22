"""JARVIS-native memory provider base + recall-bridge gate.

Cloud user-modeling memory backends (honcho, mem0, hindsight, ...) register
here under the provider-registry kind "memory". The voice agent's turn loop
currently uses file-backed memory (pipeline/file_memory.py), so there is NO
consumer for these providers' recall/sync operations yet — they register (so
the family is present and is_available() reflects configured keys), and a
future memory-architecture restructuring will wire prefetch/sync into the turn
loop. That work is gated behind JARVIS_MEMORY_PROVIDER (default off → zero
behavior change today).
"""
from __future__ import annotations

import abc
import os
from typing import Any, Dict, List

PROVIDER_KIND = "memory"
_TRUE = {"1", "true", "yes", "on"}


def memory_bridge_enabled() -> bool:
    """True only when JARVIS_MEMORY_PROVIDER opts in (default off)."""
    return os.environ.get("JARVIS_MEMORY_PROVIDER", "").strip().lower() in _TRUE


class MemoryProvider(abc.ABC):
    name: str = ""

    @abc.abstractmethod
    def is_available(self) -> bool: ...

    # Recall/sync are present but have no turn-loop consumer yet (deferred).
    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return ""

    def sync_turn(
        self, user_content: str, assistant_content: str, *, session_id: str = ""
    ) -> None:
        return None

    def system_prompt_block(self) -> str:
        return ""
