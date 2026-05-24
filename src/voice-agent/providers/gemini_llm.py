"""LiveKit-Agents `LLM` adapter for Google Gemini with context caching.

Subclasses `livekit.plugins.google.LLM` so the chat-streaming /
tool-call / chunk-decode plumbing is reused verbatim — we only inject
the `cached_content` reference into the underlying
`google.genai.types.GenerateContentConfig` that the plugin builds for
each request.

Why subclass (not write-from-scratch)
-------------------------------------
The plugin already implements:

  - ChatContext → google `Content` turn-list conversion
    (`chat_ctx.to_provider_format(format="google", ...)`).
  - LiveKit `Tool` list → google `FunctionDeclaration` schema
    conversion via `create_tools_config`.
  - Streaming chunk dispatch onto the `_event_ch` channel that
    LiveKit-Agents' `LLMStream` plumbing expects.
  - Tool-call thought-signatures for Gemini 2.5+ multi-turn.

Rewriting any of that would be ~400 lines of duplicated code that
would lag behind upstream plugin updates. Subclassing is ~30 lines.

Why not just pass `cached_content=` as an `extra_kwargs` dict
-------------------------------------------------------------
The plugin's `LLM.chat()` builds `extra` dict, which it eventually
unpacks into `GenerateContentConfig(**extra)`. We COULD pass
``extra_kwargs={"cached_content": name}`` and have it slot in via
that path — but the cache must be created LAZILY against the system
prompt observed in `chat_ctx`, which means we need to inspect the
incoming chat_ctx BEFORE building extra_kwargs. The cleanest place
to do that is the subclass's overridden `chat()`.

Graceful degrade
----------------
This module's import is gated at the call site (see
`providers.llm.SPEECH_MODELS["gemini-2.5-flash"]["build"]`):
``from providers.gemini_llm import GeminiCachedLLM`` raises
ImportError if `livekit-plugins-google` is not installed, which the
dispatcher's existing try/except handles by skipping the Gemini
primary and falling back to the route's Groq legacy.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

# Hard import — this module is only loaded when the caller already
# checked that GOOGLE_API_KEY exists. If livekit-plugins-google isn't
# installed, this import raises ImportError, which the caller catches.
from livekit.plugins import google as lk_google  # type: ignore  # noqa: F401

from providers.gemini_cache import GeminiCachedContentManager, extract_system_prompt

logger = logging.getLogger("jarvis.gemini_llm")


__all__ = ["GeminiCachedLLM"]


class GeminiCachedLLM(lk_google.LLM):
    """`livekit.plugins.google.LLM` with explicit context caching.

    Behavior on first chat() call:

      1. Extract the system message text from `chat_ctx` (turns whose
         role is "system", concatenated).
      2. If we have no `GeminiCachedContentManager` yet, build one with
         that system prompt as the cache contents.
      3. Inject ``cached_content=<resource-name>`` into the plugin's
         `extra_kwargs` so the underlying request references the cache.

    On subsequent calls: if the system message hash matches what we
    cached on first use, reuse the cache. If it has drifted (memory
    block or breaker block changed text), skip caching for that single
    call and proceed inline — we don't churn the resource on every
    turn. See `providers.gemini_cache.GeminiCachedContentManager`
    module docstring for the full rationale.
    """

    def __init__(self, *args, cache_ttl_seconds: int = 3600, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Manager is built lazily on the first chat() call once we've
        # seen the system prompt — until then we don't know what to cache.
        self._cache_mgr: Optional[GeminiCachedContentManager] = None
        # Hash of the system prompt that we cached on first use. Used
        # to decide whether subsequent turns hit the cache (hash matches)
        # or bypass it (system message changed).
        self._cached_prompt_hash: Optional[int] = None
        self._cache_ttl_seconds = cache_ttl_seconds

    # ── public API ─────────────────────────────────────────────────────

    def chat(self, *args, **kwargs):
        """Override to inject cached_content into the request config.

        Falls through to super().chat() if anything in the cache path
        fails — the request still goes through, just without the cache
        win for that turn."""
        try:
            extra_kwargs = self._maybe_attach_cache(kwargs)
            if extra_kwargs is not None:
                kwargs["extra_kwargs"] = extra_kwargs
        except Exception as e:
            # Defensive: any failure in the cache path must NOT break
            # the actual chat call.
            logger.warning(
                f"[gemini-cache] chat-time cache attach failed "
                f"(falling back to no-cache): {type(e).__name__}: {e}"
            )
        return super().chat(*args, **kwargs)

    async def aclose(self) -> None:
        """Best-effort close of the cache manager when the LLM shuts
        down. The super().aclose() is invoked first so the streaming
        pipeline is torn down before we tap the caches API."""
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

    def _maybe_attach_cache(self, kwargs: dict) -> Optional[dict[str, Any]]:
        """Build the extra_kwargs dict with cached_content set, OR return
        None to signal 'caller's kwargs already correct, no override'.

        Returns None if:
          - There's no extractable system prompt in chat_ctx.
          - The system prompt drifted vs what we cached → don't thrash.
          - The cache manager failed to provision a resource.
        """
        chat_ctx = kwargs.get("chat_ctx")
        sys_prompt = extract_system_prompt(chat_ctx)
        if not sys_prompt:
            return None

        prompt_hash = hash(sys_prompt)

        # First call: build the manager with whatever we got. Use the
        # full system prompt as the cache contents; see module docstring
        # of `gemini_cache` for the stable-prefix-only caveat.
        if self._cache_mgr is None:
            try:
                self._cache_mgr = GeminiCachedContentManager(
                    model_name=self.model,
                    system_prompt=sys_prompt,
                    ttl_seconds=self._cache_ttl_seconds,
                )
                self._cached_prompt_hash = prompt_hash
                logger.info(
                    f"[gemini-cache] manager initialized for model "
                    f"{self.model!r} (prompt hash={prompt_hash})"
                )
            except Exception as e:
                logger.warning(
                    f"[gemini-cache] manager init failed: "
                    f"{type(e).__name__}: {e}"
                )
                return None

        # Subsequent calls: cache only when the system prompt hash
        # matches what we provisioned the resource for. If it drifted,
        # bypass caching for this single call (DON'T re-provision —
        # that would churn the resource every turn the dynamic block
        # changes, completely defeating the latency win).
        if prompt_hash != self._cached_prompt_hash:
            logger.debug(
                "[gemini-cache] system prompt hash drifted "
                f"(expected {self._cached_prompt_hash}, got {prompt_hash}); "
                "bypassing cache for this turn"
            )
            return None

        cache_name = self._cache_mgr.get_cache_name()
        if not cache_name:
            return None

        # Merge with whatever the caller passed in extra_kwargs already.
        # `extra_kwargs` is NOT_GIVEN by default in livekit's `chat()`
        # signature — preserve that distinction by treating dict-or-missing
        # as the only legal shape.
        existing = kwargs.get("extra_kwargs")
        if isinstance(existing, dict):
            merged = dict(existing)
        else:
            merged = {}
        # The google-genai SDK's GenerateContentConfig accepts either the
        # snake_case `cached_content` or camelCase `cachedContent`. The
        # plugin uses snake_case for its other extra_kwargs entries
        # (`tool_config`, `response_schema`, etc.), so we match.
        merged["cached_content"] = cache_name
        return merged
