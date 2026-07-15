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

import asyncio
import importlib.util
import logging
import os
from typing import Optional

from tools.memory_providers import MemoryProvider

logger = logging.getLogger("jarvis.memory.honcho")

# Cap on how many working-representation conclusions to pull per turn — bounds
# the injected block. No ``search_query`` is passed, so the call is a plain read
# (no server-side embedding). Its own sub-timeout keeps a slow/hung honcho from
# eating the shared 1.5 s turn budget and starving the summary+messages inject.
_USER_MODEL_MAX_CONCLUSIONS = 12
_USER_MODEL_TIMEOUT_S = 0.5

# honcho's working-representation markdown is split into sections. The
# ``Explicit Observations`` layer is a timestamped restatement of the raw
# transcript — redundant with the ``context`` messages we already inject, and
# noisy in a prompt. The deductive/inductive/contradiction layers are the
# deriver's *reasoned* user model — the only part worth injecting. We ALLOWLIST
# those by heading prefix: fail-safe, so an unknown or renamed section from a
# future honcho image is dropped, never injected. Until the deriver/dreamer
# produce durable conclusions the filter yields nothing.
_DURABLE_REP_SECTIONS = ("deductive", "inductive", "contradiction")

# Semantic-search (recall mode='search') bounds. Cap each returned message so a
# few long assistant turns can't dump tens of KB into the voice model's context
# (the tool's max_result_size_chars is not enforced by the adapter). The
# assistant peer id gates the self-poisoning denial filter (jarvis-side only —
# a user message that matches the denial pattern is real and kept).
_SEARCH_MSG_MAX_CHARS = 400
_AGENT_PEER_ID = "jarvis"


def _durable_representation(rep: str) -> str:
    """Keep only the durable (reasoned) sections of a honcho working
    representation markdown — the deductive/inductive/contradiction layers —
    dropping the raw ``## Explicit Observations`` transcript restatement and any
    unknown section. Returns ``""`` when no durable *content* remains (a bare
    heading with no bullets counts as nothing).

    >>> _durable_representation("## Explicit Observations\\n[t] said hi")
    ''
    >>> _durable_representation("## Explicit Observations (recent)\\n[t] said hi")
    ''
    >>> _durable_representation("## Deductive Observations\\n[t] values privacy")
    '## Deductive Observations\\n[t] values privacy'
    """
    if not rep or not rep.strip():
        return ""
    kept: list[str] = []
    keep = False
    for line in rep.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            keep = stripped[3:].strip().lower().startswith(_DURABLE_REP_SECTIONS)
            if keep:
                kept.append(line)
            continue
        if keep and stripped:
            kept.append(line)
    # Require at least one non-heading line — a bare durable heading is no signal.
    if not any(not line.strip().startswith("## ") for line in kept):
        return ""
    return "\n".join(kept).strip()


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
        """Cheap session-context recall, enriched with the durable user model.

        Used by the gated auto-recall path (``maybe_recall_for_turn``), which
        wraps it in a hard 1.5 s timeout. Returns a compact text string or ``""``.

        Two honcho reads run CONCURRENTLY — ``session.context`` (rolling summary
        + recent messages) and ``peer.representation`` (the deriver's user model,
        filtered to durable conclusions by ``_user_model``). The user-model leg
        carries its OWN sub-timeout, so a slow/hung representation call can't
        extend the critical path or starve the summary+messages inject (which is
        the guaranteed floor). Ordered general→specific: user model, then
        summary, then the last few messages.
        """
        await self._ensure_init()
        if self._session is None:
            return ""
        try:
            ctx, user_model = await asyncio.gather(
                self._session.aio.context(summary=True, tokens=512),
                self._user_model(),
                return_exceptions=True,
            )
            parts: list[str] = []
            if isinstance(user_model, str) and user_model:
                parts.append(user_model)
            if not isinstance(ctx, BaseException) and ctx is not None:
                summary = getattr(ctx, "summary", None)
                if summary is not None and getattr(summary, "content", None):
                    parts.append(summary.content)
                for msg in (getattr(ctx, "messages", None) or [])[-6:]:
                    peer_id = getattr(msg, "peer_id", "")
                    content = getattr(msg, "content", "")
                    if peer_id and content:
                        parts.append(f"{peer_id}: {content}")
            elif isinstance(ctx, BaseException):
                logger.warning("[honcho] recall_context context() failed: %s", ctx)
            return "\n".join(parts) if parts else ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("[honcho] recall_context failed: %s", exc)
            return ""

    async def _user_model(self) -> str:
        """honcho working representation, filtered to its durable conclusions.

        Returns the deductive/inductive/contradiction sections (the deriver's
        *reasoned* user model) under a ``[user model]`` marker, dropping the
        noisy ``## Explicit Observations`` transcript-restatement layer. Returns
        ``""`` when the peer handle is missing, the call fails/times out, or no
        durable conclusions exist yet (the common case until the deriver/dreamer
        mature). No ``search_query`` → no server-side embedding; a private
        ``_USER_MODEL_TIMEOUT_S`` sub-budget bounds a slow/hung honcho so it
        can't eat the shared turn budget.
        """
        if self._peer_user is None:
            return ""
        try:
            rep = await asyncio.wait_for(
                self._peer_user.aio.representation(
                    max_conclusions=_USER_MODEL_MAX_CONCLUSIONS,
                ),
                timeout=_USER_MODEL_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 — incl. TimeoutError → degrade to ""
            logger.debug("[honcho] representation fetch failed: %s", exc)
            return ""
        durable = _durable_representation(rep if isinstance(rep, str) else "")
        return f"[user model]\n{durable}" if durable else ""

    async def search(self, query: str, limit: int = 8) -> str:
        """Semantic message retrieval — honcho embedding search over the stored
        turns, workspace-scoped so it spans past sessions.

        Returns the actual past messages ranked by MEANING (not keyword) as a
        compact ``peer: text`` block, or ``""``. Distinct from ``recall`` (which
        synthesizes a prose answer) and from the keyword ``session_search`` /
        ``recall_conversation`` tools — this finds messages phrased differently
        from the query. Costs one server-side embedding call, so it is invoked
        only from the ``recall(mode="search")`` tool, never on the turn path.
        """
        await self._ensure_init()
        if self._client is None:
            return ""
        try:
            msgs = await self._client.aio.search(query, limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[honcho] search failed: %s", exc)
            return ""
        try:
            from sanitizers.denial_detector import is_capability_denial
        except Exception:  # noqa: BLE001 — detector missing → no redaction
            is_capability_denial = None  # type: ignore[assignment]
        lines: list[str] = []
        for msg in (msgs or [])[:limit]:
            peer_id = getattr(msg, "peer_id", "") or "?"
            content = (getattr(msg, "content", "") or "").strip()
            if not content:
                continue
            # Self-poisoning gate (mirrors tools/session_search.py, jarvis-side
            # only): never replay a persisted assistant capability-denial ("I
            # can't remember X") back into context — it teaches the model to keep
            # denying. Scoped to the assistant peer so a user message matching
            # the pattern is kept.
            if (peer_id == _AGENT_PEER_ID
                    and is_capability_denial is not None
                    and is_capability_denial(content)):
                logger.warning(
                    "[self-poisoning gate] redacted persisted denial from honcho "
                    "search result: %r", content[:120]
                )
                continue
            if len(content) > _SEARCH_MSG_MAX_CHARS:
                content = content[:_SEARCH_MSG_MAX_CHARS].rstrip() + "…"
            lines.append(f"{peer_id}: {content}")
        return "\n".join(lines)

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
