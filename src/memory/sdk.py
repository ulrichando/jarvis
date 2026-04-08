"""MemorySearchManager Protocol — swappable memory backend interface.

Mirrors OpenClaw's src/memory-host-sdk/types.ts MemorySearchManager.

Any class that implements this Protocol can be registered as the active
memory backend.  JARVIS enforces the exclusive-slot rule: only one
MemorySearchManager may be active at a time (last registration wins).

Usage:
    class MyMemoryBackend:
        async def search(self, query, session_id, limit): ...
        async def store(self, content, metadata, session_id): ...
        async def delete(self, memory_id): ...
        async def list(self, session_id, limit): ...

    # Register
    from src.memory.sdk import set_memory_backend, get_memory_backend
    set_memory_backend(MyMemoryBackend())
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import logging

log = logging.getLogger("jarvis.memory.sdk")

# ── Protocol definition ───────────────────────────────────────────────────────


@runtime_checkable
class MemorySearchManager(Protocol):
    """Standard interface for pluggable memory backends."""

    async def search(
        self,
        query: str,
        session_id: str = "",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search memory for entries relevant to *query*.

        Returns a list of dicts, each with at least ``id``, ``content``,
        ``score`` (0.0–1.0), and ``metadata`` keys.
        """
        ...

    async def store(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
        session_id: str = "",
    ) -> str:
        """Persist a memory chunk.  Returns the assigned memory ID."""
        ...

    async def delete(self, memory_id: str) -> bool:
        """Delete a stored memory by ID.  Returns True if deleted."""
        ...

    async def list(
        self,
        session_id: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List stored memories, newest first."""
        ...


# ── Exclusive backend slot ────────────────────────────────────────────────────

_active_backend: MemorySearchManager | None = None


def set_memory_backend(backend: MemorySearchManager) -> None:
    """Register *backend* as the active memory backend (exclusive slot).

    The previous backend (if any) is replaced.  Last registration wins.
    """
    global _active_backend
    if _active_backend is not None and _active_backend is not backend:
        log.info(
            "Memory backend replaced: %s → %s",
            type(_active_backend).__name__,
            type(backend).__name__,
        )
    _active_backend = backend


def get_memory_backend() -> MemorySearchManager | None:
    """Return the currently active memory backend, or None."""
    return _active_backend


def clear_memory_backend() -> None:
    """Deregister the active memory backend."""
    global _active_backend
    _active_backend = None
