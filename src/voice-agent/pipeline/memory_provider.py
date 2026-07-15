"""Runtime owner of the cloud MemoryProvider layer (augments file-memory).

Resolves the backend named by JARVIS_MEMORY_PROVIDER from the provider registry
(kind="memory"), owns the per-session lifecycle, fires fire-and-forget background
sync, and serves recall. Every entry point is a safe no-op when no provider is
active/available, so the whole layer is inert by default. Never blocks the voice
turn: sync is fire-and-forget; auto-recall is gated behind a hard timeout.

Public surface
--------------
active_provider()          → registered + available provider, or None
begin_session(session_id)  → calls provider.initialize; no-op if none active
end_session()              → calls provider.end_session; no-op if none active
sync_item_async(role, text)→ fire-and-forget sync of one message; never raises
recall_for_query(query)    → sync deep recall via provider.recall (for the tool)
maybe_recall_for_turn(text, *, timeout_s=1.5)
                           → async cheap auto-recall; "" on timeout/error/no-provider

Import safety
-------------
Does NOT import jarvis_agent, livekit, or anything with heavy side-effects at
module scope. stdlib + tools.* only.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Optional

from tools import _provider_registry as provider_registry
from tools.memory_providers import PROVIDER_KIND, active_provider_name

logger = logging.getLogger("jarvis.memory_provider")

# Module-level session state: True while a session has been successfully begun.
_session_started: bool = False


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------

def active_provider() -> Optional[Any]:
    """The configured + available memory provider, or None.

    Local resolution (unchanged): None when JARVIS_MEMORY_PROVIDER is unset,
    the named provider is not registered, or is_available() is False/raises.

    Cloud wrap (JARVIS_CLOUD_MEMORY=1, pipeline.cloud_memory): the resolved
    local provider is wrapped in a CloudMemoryProvider that goes cloud-first
    for recall/sync and falls back to the local provider on any cloud error —
    the wrap happens HERE (not in the provider registry) because the
    JARVIS_MEMORY_PROVIDER slot is single-select and selecting a "cloud"
    backend by name would DISABLE the local-honcho fallback. With the flag on,
    recall works even when the local provider is unavailable (fallback=None).
    Flag off → identical return to the pre-cloud behavior.
    """
    local_prov: Optional[Any] = None
    name = active_provider_name()
    if name:
        prov = provider_registry.get_provider(PROVIDER_KIND, name)
        if prov is not None:
            try:
                if prov.is_available():
                    local_prov = prov
            except Exception:  # noqa: BLE001 — a probe error means not available
                local_prov = None
    try:
        from pipeline import cloud_memory  # lazy — keeps flag-off import surface identical
        if cloud_memory.enabled():
            return cloud_memory.get_cloud_provider(fallback=local_prov)
    except Exception as exc:  # noqa: BLE001 — cloud layer must never break resolution
        logger.debug("cloud memory provider unavailable: %s", exc)
    return local_prov


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

def begin_session(session_id: str) -> None:
    """Initialize the active provider for this session.  No-op when none active."""
    global _session_started
    prov = active_provider()
    if prov is None:
        return
    try:
        prov.initialize(session_id)
        _session_started = True
        logger.info("memory provider %r session begun (%s)", prov.name, session_id)
    except Exception as exc:  # noqa: BLE001 — provider error must not crash startup
        logger.warning("memory provider begin_session failed: %s", exc)


def end_session() -> None:
    """Flush/close the active provider session.  No-op when none active."""
    global _session_started
    prov = active_provider()
    if prov is None or not _session_started:
        return
    try:
        prov.end_session()
        logger.debug("memory provider session ended")
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory provider end_session failed: %s", exc)
    finally:
        _session_started = False


# ---------------------------------------------------------------------------
# Fire-and-forget sync
# ---------------------------------------------------------------------------

def sync_item_async(role: str, text: str) -> None:
    """Fire-and-forget background sync of one conversation item.

    When called from an async context (the normal voice-turn path), wraps the
    sync call in an asyncio.Task so it never blocks the voice turn.  When called
    from a sync context (tests, startup code), falls back to an inline best-effort
    call.  Never raises under any circumstance.
    """
    prov = active_provider()
    if prov is None or not (text or "").strip():
        return

    async def _task() -> None:
        try:
            result = prov.sync_message(role, text)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001 — background; must never surface
            logger.debug("memory sync_message failed (%s): %s", role, exc)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_task())
    except RuntimeError:
        # No running loop (sync context / test) — inline best-effort.
        try:
            result = prov.sync_message(role, text)
            # If the provider is async but we have no loop, we can't await —
            # log and give up rather than hang or crash.
            if inspect.isawaitable(result):
                logger.debug("memory sync_message returned awaitable in sync context; skipping")
        except Exception as exc:  # noqa: BLE001
            logger.debug("memory sync_message (inline) failed: %s", exc)


# ---------------------------------------------------------------------------
# Recall helpers
# ---------------------------------------------------------------------------

def recall_for_query(query: str) -> str:
    """Deep recall via the active provider (used by the recall() tool).

    Sync wrapper, async-aware: the provider's ``recall`` may be a coroutine
    function (e.g. the Honcho dialectic ``peer.chat``) or a plain sync function.
    Returns the recall string, or "" when no provider is active or the call fails.

    Must be called from a NON-running-loop context — the recall() tool invokes
    this via ``asyncio.to_thread``, so it runs in a worker thread with no event
    loop and ``asyncio.run`` on a coroutine is safe.
    """
    prov = active_provider()
    if prov is None:
        return ""
    try:
        recall_fn = prov.recall
        if inspect.iscoroutinefunction(recall_fn):
            result = asyncio.run(recall_fn(query))
        else:
            result = recall_fn(query)
        return result if isinstance(result, str) else ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory recall failed: %s", exc)
        return ""


def search_for_query(query: str, limit: int = 8) -> str:
    """Semantic message search via the active provider (recall mode='search').

    Sync, async-aware wrapper (mirrors ``recall_for_query``): the provider's
    ``search`` may be a coroutine (Honcho embedding search) or plain sync.
    Returns the matches as text, or "" when no provider is active, the provider
    exposes no ``search``, or the call fails. Called from the recall() tool via
    ``asyncio.to_thread`` (worker thread, no running loop → ``asyncio.run`` safe).
    """
    prov = active_provider()
    if prov is None:
        return ""
    search_fn = getattr(prov, "search", None)
    if search_fn is None:
        return ""
    try:
        if inspect.iscoroutinefunction(search_fn):
            result = asyncio.run(search_fn(query, limit))
        else:
            result = search_fn(query, limit)
        return result if isinstance(result, str) else ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory search failed: %s", exc)
        return ""


async def maybe_recall_for_turn(text: str, *, timeout_s: float = 1.5) -> str:
    """Cheap gated auto-recall for on_user_turn_completed.

    Asks the provider's recall_context path (fast, session-scoped context) rather
    than the full dialectic peer.chat path.  Returns "" on any miss / timeout /
    error so the turn always proceeds.

    Async/sync bridging strategy
    ----------------------------
    * If the provider's recall_context is a coroutine function (e.g. AsyncHoncho),
      await it directly inside a wait_for — no thread needed.
    * If it is a plain sync function, run it in asyncio.to_thread so the event
      loop stays responsive while the sync call blocks (e.g. file I/O, HTTP).
    Either path is wrapped in asyncio.wait_for with timeout_s.
    """
    prov = active_provider()
    if prov is None:
        return ""

    recall_fn = getattr(prov, "recall_context", None)
    if recall_fn is None:
        return ""

    async def _call_async() -> str:
        result = await recall_fn(text)
        return result if isinstance(result, str) else ""

    async def _call_sync() -> str:
        result = await asyncio.to_thread(recall_fn, text)
        return result if isinstance(result, str) else ""

    try:
        if inspect.iscoroutinefunction(recall_fn):
            return await asyncio.wait_for(_call_async(), timeout=timeout_s)
        else:
            return await asyncio.wait_for(_call_sync(), timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001 — timeout or provider error → no inject
        logger.debug("auto-recall skipped (%s)", exc)
        return ""
