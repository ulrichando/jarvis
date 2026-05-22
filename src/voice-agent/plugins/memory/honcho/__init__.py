"""Honcho cloud memory backend — real AsyncHoncho implementation.

Uses the high-level ``honcho-ai`` SDK (``honcho.Honcho`` with ``honcho.aio`` async
view). All network calls are async; the runtime in ``pipeline/memory_provider.py``
detects this via ``inspect.iscoroutinefunction`` and awaits accordingly.

Layer is inert when:
  - ``HONCHO_API_KEY`` is unset  →  ``is_available()`` returns False
  - ``honcho-ai`` is not installed  →  ``is_available()`` returns False
  - ``initialize()`` has not been called  →  recall/sync no-op safely

Never raises into the voice turn — every method guards its own errors and
returns ``""`` / no-ops on any failure. JARVIS-native naming throughout
(no foreign naming conventions).
"""
from __future__ import annotations

import importlib.util
import logging
import os
from typing import Optional

from tools.memory_providers import MemoryProvider

logger = logging.getLogger("jarvis.memory.honcho")


class HonchoMemoryProvider(MemoryProvider):
    """AsyncHoncho-backed cross-session memory.

    Session lifecycle
    -----------------
    ``initialize(session_id)`` builds the Honcho client and resolves/creates
    the peer + session handles via the async API.  Because ``initialize`` is
    called from the synchronous ``begin_session`` entrypoint in the runtime,
    we run the async init in a dedicated event-loop call (asyncio.run) so the
    caller doesn't need to be async.

    All subsequent methods (``recall``, ``recall_context``, ``sync_message``)
    are ``async def`` so the runtime awaits them directly.  If ``initialize``
    failed (handles are None), every method returns ``""`` / no-ops.
    """

    name = "honcho"

    def __init__(self) -> None:
        self._client: Optional[object] = None      # honcho.Honcho instance
        self._peer_user: Optional[object] = None   # Peer for "ulrich"
        self._peer_agent: Optional[object] = None  # Peer for "jarvis"
        self._session: Optional[object] = None     # Session handle

    # ------------------------------------------------------------------
    # Availability gate
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """True when HONCHO_API_KEY is set AND the honcho package is importable."""
        if not os.environ.get("HONCHO_API_KEY", "").strip():
            return False
        return importlib.util.find_spec("honcho") is not None

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def initialize(self, session_id: str) -> None:
        """Build the Honcho client and resolve/create the peer + session handles.

        Runs the async setup synchronously via asyncio.run so the runtime's
        synchronous ``begin_session`` can call us without an event loop.  If
        anything fails (bad key, network error, etc.) handles stay None and
        subsequent recall/sync calls silently no-op.
        """
        import asyncio
        try:
            asyncio.run(self._async_init(session_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[honcho] initialize failed — recall/sync will no-op: %s", exc)
            self._client = self._peer_user = self._peer_agent = self._session = None

    async def _async_init(self, session_id: str) -> None:
        """Async portion of initialize — builds client + resolves handles."""
        from honcho import Honcho, MessageCreateParams  # noqa: F401 — checked by is_available
        api_key = os.environ.get("HONCHO_API_KEY", "").strip()
        if not api_key:
            raise ValueError("HONCHO_API_KEY not set")

        client = Honcho(api_key=api_key)
        self._client = client

        # Resolve/create peer and session handles via the async API.
        self._peer_user = await client.aio.peer("ulrich")
        self._peer_agent = await client.aio.peer("jarvis")
        self._session = await client.aio.session(session_id)
        logger.info("[honcho] session initialized: %s", session_id)

    def end_session(self) -> None:
        """Best-effort cleanup — clear handles so stale refs don't linger."""
        self._peer_user = None
        self._peer_agent = None
        self._session = None
        self._client = None
        logger.debug("[honcho] session handles cleared")

    # ------------------------------------------------------------------
    # Async recall paths
    # ------------------------------------------------------------------

    async def recall(self, query: str) -> str:
        """Deep dialectic recall via peer.chat (NL-in, prose-out).

        This is the expensive path (multi-second server-side reasoning) — the
        runtime only calls it from the explicit ``recall()`` tool, never on
        the synchronous voice turn.  Returns ``""`` on any failure.
        """
        if self._peer_user is None:
            return ""
        try:
            result = await self._peer_user.aio.chat(query)
            return result if isinstance(result, str) else (result or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[honcho] recall failed: %s", exc)
            return ""

    async def recall_context(self, hint: str = "") -> str:
        """Cheap session-context recall (summary + recent messages).

        Used by the gated auto-recall path (``maybe_recall_for_turn``).
        Always returns a compact text string or ``""`` on any failure.
        """
        if self._session is None:
            return ""
        try:
            ctx = await self._session.aio.context(summary=True, tokens=512)
            # SessionContext has __repr__ with message + summary counts.
            # Render as plain text: use the summary content if present,
            # then fall back to a str representation.
            parts: list[str] = []
            if getattr(ctx, "summary", None) and ctx.summary.content:
                parts.append(ctx.summary.content)
            messages = getattr(ctx, "messages", None) or []
            for msg in messages[-6:]:  # last 6 messages for compactness
                peer_id = getattr(msg, "peer_id", "")
                content = getattr(msg, "content", "")
                if peer_id and content:
                    parts.append(f"{peer_id}: {content}")
            return "\n".join(parts) if parts else ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("[honcho] recall_context failed: %s", exc)
            return ""

    # ------------------------------------------------------------------
    # Async write path
    # ------------------------------------------------------------------

    async def sync_message(self, role: str, text: str) -> None:
        """Add one message to the Honcho session (fire-and-forget by the runtime).

        ``role`` is ``"user"`` or ``"assistant"``.  Maps to the appropriate
        peer so Honcho can attribute the message correctly for its user model.
        """
        if self._session is None or self._peer_user is None or self._peer_agent is None:
            return
        try:
            from honcho import MessageCreateParams
            peer = self._peer_user if role == "user" else self._peer_agent
            msg = MessageCreateParams(content=text, peer_id=peer.id)
            await self._session.aio.add_messages(msg)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[honcho] sync_message failed (%s): %s", role, exc)


def register(ctx) -> None:
    ctx.register_memory_provider(HonchoMemoryProvider())
