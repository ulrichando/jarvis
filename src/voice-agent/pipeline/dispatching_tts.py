"""Dispatching TTS wrapper. Sibling pattern of DispatchingLLM.

The integration step in jarvis_agent.py constructs four inner TTS
instances (one per route) and assembles them here. The voice_id
attribute is a duck-typed convenience for telemetry; real LiveKit
TTS instances expose the voice somewhere on themselves and can be
adapted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class DispatchingTTS:
    inners: dict[str, Any]
    fallback: Any
    last_route: Optional[str] = None
    last_voice_id: Optional[str] = None

    def pick(self, route: str) -> Any:
        inner = self.inners.get(route, self.fallback)
        self.last_route = route
        self.last_voice_id = getattr(inner, "voice_id", repr(inner))
        return inner
