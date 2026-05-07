"""Dispatching LLM wrapper.

Plain Python class for v1 — does not subclass livekit.agents.llm.LLM.
Keeping it framework-agnostic lets unit tests exercise routing without
constructing a full LiveKit pipeline. The integration step in
jarvis_agent.py builds a DispatchingLLM with real inner LLMs and
forwards `chat()` calls to the picked inner.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class DispatchingLLM:
    """Picks an inner LLM based on the current route tag."""
    inners: dict[str, Any]
    fallback: Any
    last_route: Optional[str] = None
    last_llm_label: Optional[str] = None

    def pick(self, route: str) -> Any:
        inner = self.inners.get(route, self.fallback)
        self.last_route = route
        # Prefer our private attribute (_jarvis_label) — `label` collides
        # with a read-only property on livekit.plugins.groq.LLM that raises
        # AttributeError on assignment, so we use _jarvis_label everywhere.
        self.last_llm_label = getattr(inner, "_jarvis_label", None) or getattr(inner, "label", repr(inner))
        return inner
