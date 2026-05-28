"""Dispatching TTS wrapper. Sibling pattern of DispatchingLLM.

The integration step in jarvis_agent.py constructs four inner TTS
instances (one per route) and assembles them here. The voice_id
attribute is a duck-typed convenience for telemetry; real LiveKit
TTS instances expose the voice somewhere on themselves and can be
adapted.

Language axis (2026-05-28 spec): pick() takes a lang code in
addition to route. fr → single French inner (EdgeTTS); other →
existing English route lookup. Falling back to English on unknown
lang keeps the dispatcher safe when build_dispatching_tts couldn't
construct fr_inner (e.g., EdgeTTS network error at startup).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class DispatchingTTS:
    inners: dict[str, Any]
    fallback: Any
    fr_inner: Optional[Any] = None
    last_route: Optional[str] = None
    last_voice_id: Optional[str] = None

    def pick(self, route: str, lang: str = "en") -> Any:
        if lang == "fr" and self.fr_inner is not None:
            inner = self.fr_inner
        else:
            inner = self.inners.get(route, self.fallback)
        self.last_route = route
        self.last_voice_id = getattr(inner, "voice_id", repr(inner))
        return inner
