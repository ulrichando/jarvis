"""LiveKit-Agents ``LLM`` adapter for Google Gemini with explicit
stable/volatile context caching.

Subclasses ``livekit.plugins.google.LLM`` so the chat-streaming /
tool-call / chunk-decode plumbing is reused verbatim — we only inject
the cached prefix reference (``cached_content``) into the underlying
``google.genai.types.GenerateContentConfig`` that the plugin builds for
each request, and swap the in-flight chat_ctx system message for the
volatile-only remainder so Gemini doesn't reject the request as having
both a cached system_instruction AND an inline one.

Background
----------
Gemini does NOT auto-cache the system prompt the way Anthropic / OpenAI
/ DeepSeek do (Anthropic charges for the first turn and refunds the
prefix on every subsequent hit; OpenAI / DeepSeek hash-match
transparently). For Gemini, the caller must explicitly create a
``CachedContent`` resource via ``client.caches.create(...)`` and then
pass ``cached_content="<name>"`` on every ``generate_content`` call.

Stable/volatile split (revised 2026-05-23)
-------------------------------------------
Earlier revisions of this wrapper computed a hash of the full system
message and only used the cache when the hash matched the original;
when memory or breaker state shifted the system text drifted away from
the cached version and the wrapper silently bypassed caching for that
turn — "drift-aware bypass". On a JARVIS session with even a single
memory write, every subsequent turn missed the cache.

The new architecture splits the system prompt into a STABLE PREFIX
(SOUL + JARVIS_INSTRUCTIONS + skill_catalog_block — never changes
mid-session) and a VOLATILE SUFFIX (runtime_id + memory + breaker
status — changes per memory write / breaker flip). Only the STABLE
prefix is provisioned as a ``CachedContent`` resource on Google's side;
the volatile suffix is passed as the inline ``system_instruction`` on
every turn.

Per Gemini's API, ``cached_content`` and ``system_instruction`` are
both legal in the same request — the cached content covers everything
that was provisioned, and the inline ``system_instruction`` adds
further guidance on top. The plugin builds ``system_instruction`` from
``extra_data.system_messages``; we mutate the in-flight chat_ctx so
only the volatile remainder appears in those messages.

Split-source resolution
-----------------------
The wrapper accepts an optional ``stable_prefix`` at construction time
and exposes ``set_stable_prefix()`` so the supervisor can hand it in
after the prompt state assembles. On each ``chat()`` call:

  1. If a stable_prefix is set AND the incoming system text starts
     with it → exact-prefix split. The cache manager is provisioned
     against ``stable_prefix`` (built once + reused thereafter), and
     the chat_ctx system message is replaced with the recovered
     volatile remainder.
  2. Else if the system text contains ``CACHE_BREAK_MARKER`` → marker
     split, with the cache manager provisioned against the recovered
     stable.
  3. Else → fall through with NO cache. The request goes through with
     the full system prompt inline; we log a warning so operators see
     the wiring miss.

Graceful degrade
----------------
Any failure in the cache path (caches.create returning 503, the cache
TTL expiring mid-window, the chat_ctx replacement raising) falls
through to ``super().chat()`` with the original chat_ctx and no
``cached_content`` — the request still goes through, just without the
cache win for that turn. The audio loop is never blocked by a
caches-API hiccup.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

# Hard import — this module is only loaded when the caller already
# checked that GOOGLE_API_KEY exists. If livekit-plugins-google isn't
# installed, this import raises ImportError, which the caller catches.
from livekit.plugins import google as lk_google  # type: ignore  # noqa: F401

from providers.gemini_cache import GeminiCachedContentManager, extract_system_prompt
from providers.prompt_cache import CACHE_BREAK_MARKER, split_system_text

logger = logging.getLogger("jarvis.gemini_llm")


__all__ = ["GeminiCachedLLM"]


class GeminiCachedLLM(lk_google.LLM):
    """``livekit.plugins.google.LLM`` with explicit stable/volatile cache split.

    See the module docstring for the architecture and design rationale.
    """

    def __init__(
        self,
        *args,
        cache_ttl_seconds: int = 3600,
        stable_prefix: Optional[str] = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        # Manager is provisioned lazily on the first chat() call once
        # we have the stable prefix (either via construction kwarg or
        # late-bound via set_stable_prefix).
        self._cache_mgr: Optional[GeminiCachedContentManager] = None
        self._stable_prefix: str = stable_prefix or ""
        self._cache_ttl_seconds = cache_ttl_seconds
        # Cached resource name, fetched lazily and reused per turn. The
        # underlying manager handles TTL refresh internally.
        self._cached_resource_name: Optional[str] = None

    # ── public API ─────────────────────────────────────────────────────

    def set_stable_prefix(self, stable_prefix: str) -> None:
        """Late-bind the expected stable prefix.

        Called by ``apply_stable_prefix_recursively`` after
        ``_build_initial_prompt_state`` assembles the prompt state.
        If we already provisioned a cache against a different prefix
        (mid-session change), tear it down and rebuild on the next
        chat() — the old cache content no longer matches the prompt
        so it must not be referenced again.
        """
        prev = self._stable_prefix
        if prev and stable_prefix and prev != stable_prefix:
            logger.warning(
                "[gemini-cache] stable prefix changed mid-session "
                f"({len(prev)}→{len(stable_prefix)} chars); "
                "tearing down old cache, will provision new on next chat()"
            )
            if self._cache_mgr is not None:
                try:
                    self._cache_mgr.close()
                except Exception as e:  # noqa: BLE001 — defense
                    logger.debug(f"[gemini-cache] manager close failed: {e}")
            self._cache_mgr = None
            self._cached_resource_name = None
        self._stable_prefix = stable_prefix or ""

    def chat(self, *args, **kwargs):
        """Override to:
          - swap the chat_ctx's single system message for the volatile
            remainder when we have a recoverable stable prefix;
          - inject ``cached_content=<resource-name>`` into extra_kwargs
            so the upstream request references the cached prefix.

        Falls through to ``super().chat()`` untouched if anything in
        the cache path fails — the request still goes through, just
        without the cache win for that turn.
        """
        try:
            cache_name, modified_kwargs = self._maybe_attach_cache(kwargs)
            if cache_name is not None and modified_kwargs is not None:
                kwargs = modified_kwargs
        except Exception as e:  # noqa: BLE001 — cache must never break the call
            logger.warning(
                f"[gemini-cache] chat-time cache attach failed "
                f"(falling back to no-cache): {type(e).__name__}: {e}"
            )
        return super().chat(*args, **kwargs)

    async def aclose(self) -> None:
        """Best-effort close of the cache manager when the LLM shuts down."""
        try:
            await super().aclose()
        finally:
            if self._cache_mgr is not None:
                try:
                    self._cache_mgr.close()
                except Exception as e:
                    logger.warning(
                        f"[gemini-cache] manager close on aclose failed: "
                        f"{type(e).__name__}: {e}"
                    )

    # ── internals ──────────────────────────────────────────────────────

    def _maybe_attach_cache(
        self, kwargs: dict
    ) -> tuple[Optional[str], Optional[dict]]:
        """Build the modified kwargs dict with ``cached_content`` set
        AND the chat_ctx's system message replaced by the volatile
        suffix only.

        Returns ``(cache_name, new_kwargs)`` on success or
        ``(None, None)`` to signal "leave kwargs untouched / no cache
        this turn".
        """
        chat_ctx = kwargs.get("chat_ctx")
        sys_prompt = extract_system_prompt(chat_ctx)
        if not sys_prompt:
            return None, None

        # Recover (stable, volatile) from the joined system text.
        # `split_system_text` returns (full_text, "") when no split is
        # recoverable — `not volatile` then signals "nothing to cache
        # meaningfully" (the whole prompt would have to live in either
        # the cached resource OR the inline instruction, not split
        # across both — and Gemini's cache provides no win when there's
        # nothing inline to amortize it against). Bail to no-cache.
        stable, volatile = split_system_text(
            sys_prompt, self._stable_prefix or None
        )
        if not stable or not volatile:
            logger.debug(
                "[gemini-cache] no stable/volatile split recoverable from "
                f"system prompt ({len(sys_prompt)} chars, "
                f"stable={len(stable)} volatile={len(volatile)}); "
                "proceeding uncached"
            )
            return None, None

        # First chat(): build the cache manager against the recovered
        # stable prefix. Per Gemini docs the cache content must be
        # ≥1024 tokens (Flash) / ≥4096 tokens (Pro 2.5) — the create
        # call will surface the API's error if the stable prefix is too
        # small. We don't validate locally because the threshold is
        # tokenizer-specific and the API is authoritative.
        if self._cache_mgr is None:
            try:
                self._cache_mgr = GeminiCachedContentManager(
                    model_name=self.model,
                    system_prompt=stable,
                    ttl_seconds=self._cache_ttl_seconds,
                )
                # Capture what we provisioned against so set_stable_prefix
                # can detect drift and rebuild.
                if not self._stable_prefix:
                    self._stable_prefix = stable
                logger.info(
                    f"[gemini-cache] manager initialized for model "
                    f"{self.model!r} (stable_prefix={len(stable)} chars, "
                    f"volatile_suffix={len(volatile)} chars)"
                )
            except Exception as e:
                logger.warning(
                    f"[gemini-cache] manager init failed: "
                    f"{type(e).__name__}: {e}"
                )
                return None, None

        cache_name = self._cache_mgr.get_cache_name()
        if not cache_name:
            return None, None
        self._cached_resource_name = cache_name

        # Build the modified kwargs: replace the chat_ctx with a copy
        # whose system message holds only the volatile suffix, and
        # merge cached_content into extra_kwargs. The original chat_ctx
        # is left untouched (the framework keeps the full history; we
        # only send less to the LLM this turn).
        modified_kwargs = dict(kwargs)
        modified_kwargs["chat_ctx"] = self._chat_ctx_with_system(
            chat_ctx, volatile
        )

        existing = kwargs.get("extra_kwargs")
        merged_extra = dict(existing) if isinstance(existing, dict) else {}
        # The google-genai SDK's GenerateContentConfig accepts either
        # snake_case ``cached_content`` or camelCase ``cachedContent``.
        # The plugin uses snake_case for its other extra_kwargs entries
        # (``tool_config``, ``response_schema``, etc.) so we match.
        merged_extra["cached_content"] = cache_name
        modified_kwargs["extra_kwargs"] = merged_extra
        return cache_name, modified_kwargs

    @staticmethod
    def _chat_ctx_with_system(chat_ctx, new_system_text: str):
        """Return a chat_ctx clone whose system message(s) are replaced
        with ``new_system_text`` (single block when non-empty, empty
        otherwise). All non-system items are preserved.

        Used to substitute the volatile remainder for the joined
        system prompt without mutating the framework's stored
        chat_ctx — the framework holds the canonical history.
        """
        # Import here to keep the module's top-level import side-effects
        # limited to livekit-plugins-google (which the test-mode caller
        # may have already stubbed out).
        from livekit.agents.llm import ChatContext, ChatMessage
        from livekit.agents.voice.generation import INSTRUCTIONS_MESSAGE_ID

        original_items = list(getattr(chat_ctx, "items", None) or [])
        new_items: list = []
        replaced = False
        for item in original_items:
            if (
                getattr(item, "type", None) == "message"
                and getattr(item, "role", None) == "system"
            ):
                if not replaced and new_system_text:
                    # Preserve the special INSTRUCTIONS_MESSAGE_ID so the
                    # framework's update_instructions / remove_instructions
                    # helpers continue to recognise this item on round-trip.
                    new_items.append(
                        ChatMessage(
                            id=getattr(item, "id", INSTRUCTIONS_MESSAGE_ID),
                            role="system",
                            content=[new_system_text],
                            created_at=getattr(item, "created_at", 0.0),
                        )
                    )
                    replaced = True
                # Drop any additional system messages — the volatile
                # remainder absorbed them.
                continue
            new_items.append(item)
        return ChatContext(items=new_items)
