"""ContextEngine Protocol — pluggable context assembly and compaction.

Mirrors OpenClaw's src/context-engine/ design.

A ContextEngine controls *what the LLM sees at the top of every turn*:
it assembles a list of messages from memory + history, can compact the
conversation when the token budget is tight, and receives hooks before
and after each turn so it can update its state.

Only ONE ContextEngine may be active at a time (exclusive slot).

Usage:
    class MyContextEngine:
        async def assemble(self, session_id, history, budget): ...
        async def compact(self, session_id, messages, budget): ...
        async def ingest(self, role, content, session_id): ...
        async def after_turn(self, session_id, assistant_reply): ...

    from src.context.engine_protocol import set_context_engine
    set_context_engine(MyContextEngine())
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
import logging

log = logging.getLogger("jarvis.context")

# ── Message type alias ────────────────────────────────────────────────────────
# A message dict matches the OpenAI/Anthropic schema: {role, content, ...}
Message = dict[str, Any]


# ── Protocol ──────────────────────────────────────────────────────────────────

@runtime_checkable
class ContextEngine(Protocol):
    """Standard interface for pluggable context assembly engines."""

    async def assemble(
        self,
        session_id: str,
        history: list[Message],
        token_budget: int = 80_000,
    ) -> list[Message]:
        """Build the message list to send to the LLM for this turn.

        Receives the full raw *history* and must return a (possibly
        compressed / filtered) list that fits within *token_budget*.
        """
        ...

    async def compact(
        self,
        session_id: str,
        messages: list[Message],
        token_budget: int = 80_000,
    ) -> list[Message]:
        """Compact *messages* to fit within *token_budget*.

        Called by the agent loop when the context is too long.
        Returns a shorter list; the original list must not be mutated.
        """
        ...

    async def ingest(
        self,
        role: str,
        content: str,
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Ingest a new message (before it reaches the LLM).

        Use this to update vector stores, knowledge graphs, etc.
        """
        ...

    async def after_turn(
        self,
        session_id: str,
        assistant_reply: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Called after the LLM has responded.

        Use this to update running summaries, forgetting curves, etc.
        """
        ...


# ── Exclusive backend slot ────────────────────────────────────────────────────

_active_engine: ContextEngine | None = None


def set_context_engine(engine: ContextEngine) -> None:
    """Register *engine* as the active context engine (exclusive slot)."""
    global _active_engine
    if _active_engine is not None and _active_engine is not engine:
        log.info(
            "Context engine replaced: %s → %s",
            type(_active_engine).__name__,
            type(engine).__name__,
        )
    _active_engine = engine


def get_context_engine() -> ContextEngine | None:
    """Return the currently active context engine, or None."""
    return _active_engine


def clear_context_engine() -> None:
    """Deregister the active context engine."""
    global _active_engine
    _active_engine = None
