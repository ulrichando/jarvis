"""LangContext — per-session most-recent-detected user language.

Default "en". Updated by the STT result hook in jarvis_agent.py.
Read by the TTS dispatcher at pick() time in turn_dispatcher.py
and turn_graph.py.

Single asyncio loop per session, plain attribute access is
thread-safe enough — no locks needed.
"""
from __future__ import annotations


__all__ = ["LangContext"]


# Confidence floor — short utterances ("hi" / "merci") often produce
# low-confidence language IDs that flip-flop. Below this floor the
# update is silently dropped, keeping the voice steady.
_CONFIDENCE_FLOOR = 0.6


class LangContext:
    """Per-session most-recent-detected user language.

    Construct one per agent session and stash it on the session
    (e.g., `session.lang_ctx = LangContext()`). The STT result
    handler calls `set(lang, confidence)` on each transcript; the
    TTS dispatcher calls `get()` at pick() time.
    """

    def __init__(self, default: str = "en") -> None:
        self._lang = default

    def set(self, lang: str, confidence: float = 1.0) -> None:
        if confidence < _CONFIDENCE_FLOOR:
            return
        self._lang = lang

    def get(self) -> str:
        return self._lang
