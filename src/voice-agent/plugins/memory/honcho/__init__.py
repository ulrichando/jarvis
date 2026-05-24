"""Honcho memory backend — real implementation via the honcho-ai SDK.

Uses the high-level ``honcho.Honcho`` client with its ``.aio`` async view. All
network calls are async; the runtime in ``pipeline/memory_provider.py`` detects
this via ``inspect.iscoroutinefunction`` and awaits accordingly.

Cloud vs. self-hosted: defaults to the managed service (api.honcho.dev) when
only ``HONCHO_API_KEY`` is set. Set ``HONCHO_BASE_URL`` (e.g.
``http://127.0.0.1:8000``) to point at a self-hosted Honcho server — the
plugin activates with either credential alone, so a local server with auth
disabled needs only the base URL.

Layer is inert when:
  - BOTH ``HONCHO_API_KEY`` and ``HONCHO_BASE_URL`` are unset → ``is_available()`` returns False
  - ``honcho-ai`` is not installed                            → ``is_available()`` returns False
  - init has not yet succeeded                                → recall/sync no-op safely

Never raises into the voice turn — every method guards its own errors and
returns ``""`` / no-ops on any failure. JARVIS-native naming throughout.

Lazy init (important): ``initialize(session_id)`` only STORES the session id — it
does NO network and never calls ``asyncio.run``. The client + peer/session handles
are built lazily by ``_ensure_init()`` on the first async call (``recall`` /
``recall_context`` / ``sync_message``). This is required because ``initialize`` is
invoked from the synchronous ``begin_session`` runtime entrypoint, which itself is
called from the async ``on_enter`` hook — calling ``asyncio.run`` there would raise
``RuntimeError: asyncio.run() cannot be called from a running event loop`` and
silently leave the backend permanently inert. Deferring the awaitable work to the
already-async call sites avoids that entirely.
"""
from __future__ import annotations

import importlib.util
import logging
import os
from typing import Optional

from tools.memory_providers import MemoryProvider

logger = logging.getLogger("jarvis.memory.honcho")


class HonchoMemoryProvider(MemoryProvider):
    """Honcho-backed cross-session memory (async, lazy-initialized)."""

    name = "honcho"

    def __init__(self) -> None:
        self._client: Optional[object] = None       # honcho.Honcho instance
        self._peer_user: Optional[object] = None    # Peer for "ulrich"
        self._peer_agent: Optional[object] = None   # Peer for "jarvis"
        self._session: Optional[object] = None       # Session handle
        self._session_id: Optional[str] = None       # set by initialize()
        self._init_attempted: bool = False           # don't hammer a failing init

    # ------------------------------------------------------------------
    # Availability gate
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """True when at least one credential is set AND the honcho package is importable.

        Either ``HONCHO_API_KEY`` (cloud, api.honcho.dev) or ``HONCHO_BASE_URL``
        (self-hosted server) is enough — both may be set together for an
        authenticated self-hosted instance.
        """
        api_key = os.environ.get("HONCHO_API_KEY", "").strip()
        base_url = os.environ.get("HONCHO_BASE_URL", "").strip()
        if not (api_key or base_url):
            return False
        return importlib.util.find_spec("honcho") is not None

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def initialize(self, session_id: str) -> None:
        """Store the session id for lazy init. NO network, NO asyncio.run.

        Safe to call from inside a running event loop (begin_session is sync but
        runs under the async on_enter hook). The actual client/peer/session
        handles are built by ``_ensure_init`` on the first async operation.
        """
        self._session_id = session_id
        self._init_attempted = False
        self._client = self._peer_user = self._peer_agent = self._session = None

    async def _ensure_init(self) -> None:
        """Lazily build the client + resolve handles on first async use.

        Idempotent (returns immediately once a session handle exists), runs at
        most once per session even on failure (``_init_attempted`` guard), and
        swallows every error — on failure the handles stay None and callers
        no-op. Runs inside the caller's event loop, so no asyncio.run.
        """
        if self._session is not None or self._init_attempted:
            return
        self._init_attempted = True
        if not self.is_available() or not self._session_id:
            return
        try:
            from honcho import Honcho  # checked importable by is_available()

            # Build kwargs so api_key / base_url are each optional:
            # cloud-default when only api_key is set; local self-host when
            # only base_url is set; both for an authed self-hosted instance.
            api_key = os.environ.get("HONCHO_API_KEY", "").strip()
            base_url = os.environ.get("HONCHO_BASE_URL", "").strip()
            kwargs: dict[str, str] = {}
            if api_key:
                kwargs["api_key"] = api_key
            if base_url:
                kwargs["base_url"] = base_url

            client = Honcho(**kwargs)
            self._client = client
            self._peer_user = await client.aio.peer("ulrich")
            self._peer_agent = await client.aio.peer("jarvis")
            self._session = await client.aio.session(self._session_id)
            logger.info(
                "[honcho] session initialized: %s (target=%s)",
                self._session_id,
                base_url or "api.honcho.dev",
            )
        except Exception as exc:  # noqa: BLE001 — never surface into a turn
            logger.warning("[honcho] init failed — recall/sync will no-op: %s", exc)
            self._client = self._peer_user = self._peer_agent = self._session = None

    def end_session(self) -> None:
        """Best-effort cleanup — clear handles so stale refs don't linger."""
        self._client = self._peer_user = self._peer_agent = self._session = None
        self._session_id = None
        self._init_attempted = False
        logger.debug("[honcho] session handles cleared")

    # ------------------------------------------------------------------
    # Async recall paths
    # ------------------------------------------------------------------

    async def recall(self, query: str) -> str:
        """Deep dialectic recall via peer.chat (NL-in, prose-out).

        Expensive path (multi-second server-side reasoning) — only invoked from
        the explicit ``recall()`` tool, never on the synchronous voice turn.
        Returns ``""`` on any failure.
        """
        await self._ensure_init()
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

        Used by the gated auto-recall path (``maybe_recall_for_turn``), which
        wraps it in a hard timeout. Returns a compact text string or ``""``.
        """
        await self._ensure_init()
        if self._session is None:
            return ""
        try:
            ctx = await self._session.aio.context(summary=True, tokens=512)
            parts: list[str] = []
            summary = getattr(ctx, "summary", None)
            if summary is not None and getattr(summary, "content", None):
                parts.append(summary.content)
            for msg in (getattr(ctx, "messages", None) or [])[-6:]:
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

        ``role`` is ``"user"`` or ``"assistant"``; mapped to the matching peer so
        Honcho attributes the message correctly for its user model.
        """
        await self._ensure_init()
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
