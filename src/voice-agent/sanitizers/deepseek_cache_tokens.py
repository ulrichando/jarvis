"""Backfill DeepSeek's `prompt_cache_hit_tokens` into the OpenAI-spec
`prompt_tokens_details.cached_tokens` slot so the framework's existing
extraction lands the value in LLMMetrics.prompt_cached_tokens and our
turn-telemetry `prompt_cached_tokens` column.

WHY THIS EXISTS
---------------
DeepSeek auto-caches prompts since V2 (Aug 2024) and returns BOTH:

  usage.prompt_cache_hit_tokens   = N   (DeepSeek-extra, top-level)
  usage.prompt_tokens_details.cached_tokens = N   (OpenAI-spec mirror)

Live probing 2026-05-23 (against api.deepseek.com/v1, model=deepseek-chat,
openai-python 1.x) confirms both fields carry the same value, so for
the current DeepSeek API surface the framework's stock extraction at
livekit.agents.inference.llm.LLMStream._run (which reads the second
field) already populates LLMMetrics.prompt_cached_tokens correctly.

This patch is the DEFENSIVE FALLBACK for two future-proof reasons:

  (a) If a future DeepSeek API version stops populating the OpenAI
      mirror but keeps `prompt_cache_hit_tokens`, the stock extraction
      silently regresses to 0 and we lose cache-rate visibility.
  (b) If a DeepSeek-compatible third-party endpoint (Moonshot Kimi
      compat mode, OpenRouter, etc.) returns ONLY the DeepSeek-extra
      field, we still capture it.

WHAT IT DOES
------------
Wraps `inference.llm.LLMStream._run` to install a stream proxy that
intercepts every `ChatCompletionChunk` and, when:
  - the request is going to api.deepseek.com (base_url check), AND
  - `chunk.usage.prompt_cache_hit_tokens` is positive, AND
  - `chunk.usage.prompt_tokens_details.cached_tokens` is 0 / None,
backfills `cached_tokens` in place from the DeepSeek-extra value.

The framework's downstream code (which constructs
`llm.CompletionUsage(prompt_cached_tokens=cached_tokens)`) then sees
the correct number and the LLMMetrics event lands it correctly.

NEVER OVERWRITES A POSITIVE VALUE. If both fields are already
populated and consistent (the current DeepSeek behavior), the patch
is a no-op. Non-DeepSeek requests skip the gate entirely.

IDEMPOTENT — `install()` is safe to call multiple times.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("jarvis.deepseek_cache_tokens")


def _backfill_chunk_usage(usage: Any) -> bool:
    """Backfill `usage.prompt_tokens_details.cached_tokens` from
    `usage.prompt_cache_hit_tokens` when the OpenAI-spec slot is
    missing/zero and the DeepSeek-extra is positive.

    Returns True if any value was rewritten (for diagnostic logging),
    False otherwise. Never raises — telemetry must never break the
    request path.
    """
    if usage is None:
        return False
    try:
        # DeepSeek-extra lives in pydantic's `model_extra` when the
        # response carries fields beyond the openai-python typed
        # schema. `getattr` also covers the case where a future
        # openai-python version adds the field to the typed schema.
        extra_hit: Any = None
        try:
            extra_hit = getattr(usage, "prompt_cache_hit_tokens", None)
        except Exception:
            extra_hit = None
        if extra_hit is None:
            model_extra = getattr(usage, "model_extra", None)
            if isinstance(model_extra, dict):
                extra_hit = model_extra.get("prompt_cache_hit_tokens")
        if not isinstance(extra_hit, int) or extra_hit <= 0:
            return False

        details = getattr(usage, "prompt_tokens_details", None)
        existing_cached = (
            getattr(details, "cached_tokens", None) if details is not None else None
        )
        if isinstance(existing_cached, int) and existing_cached > 0:
            # Stock extraction already covers it — never overwrite.
            return False

        if details is None:
            # Construct a fresh details object so the framework's
            # `tokens_details.cached_tokens` read finds it.
            try:
                from openai.types.completion_usage import PromptTokensDetails

                usage.prompt_tokens_details = PromptTokensDetails(
                    cached_tokens=extra_hit,
                    audio_tokens=None,
                )
                return True
            except Exception as e:
                logger.debug(f"[deepseek-cache-tokens] could not construct PromptTokensDetails: {e}")
                return False

        # Mutate in place — verified mutable on openai-python 1.x.
        try:
            details.cached_tokens = extra_hit
            return True
        except Exception as e:
            logger.debug(f"[deepseek-cache-tokens] could not set cached_tokens: {e}")
            return False
    except Exception as e:
        logger.debug(f"[deepseek-cache-tokens] backfill skipped: {e}")
        return False


def _wrap_stream(oai_stream: Any) -> Any:
    """Wrap an openai.AsyncStream[ChatCompletionChunk] with a proxy
    that backfills DeepSeek-extra cache fields on each chunk.

    Preserves async-context-manager + async-iterator + `.close()` so
    the framework's `async with stream: async for chunk in stream:`
    pattern works unchanged.
    """

    class _DeepSeekCacheTokenProxy:
        __slots__ = ("_inner",)

        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def __aiter__(self):  # type: ignore[override]
            return self

        async def __anext__(self):
            chunk = await self._inner.__anext__()
            try:
                _backfill_chunk_usage(getattr(chunk, "usage", None))
            except Exception:
                pass
            return chunk

        async def __aenter__(self):
            await self._inner.__aenter__()
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return await self._inner.__aexit__(exc_type, exc, tb)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    return _DeepSeekCacheTokenProxy(oai_stream)


def _patch_run() -> None:
    """Wrap inference.llm.LLMStream._run so the openai stream it opens
    is proxied through `_wrap_stream`. Only kicks in for requests to
    api.deepseek.com (the base_url check).

    Stacks on top of deepseek_roundtrip's _run wrap. The roundtrip
    patch wraps for a contextvar; this one wraps to proxy the stream.
    Both wrappers run on top of one another in install order.
    """
    from livekit.agents.inference import llm as inf_llm

    if getattr(inf_llm.LLMStream, "_jarvis_deepseek_cache_tokens_patched", False):
        return

    orig_run = inf_llm.LLMStream._run

    async def _patched(self) -> None:
        is_deepseek = False
        try:
            client = getattr(self, "_client", None)
            base_url = str(getattr(client, "base_url", "")) if client else ""
            is_deepseek = "deepseek.com" in base_url.lower()
        except Exception:
            is_deepseek = False

        if not is_deepseek:
            await orig_run(self)
            return

        # Patch the openai client's chat.completions.create just for
        # the duration of this _run invocation, so we wrap the
        # specific stream this call produces. We have to wrap at the
        # client level because the framework holds `stream` as a
        # local inside _run — no other hook to intercept the chunks.
        client = getattr(self, "_client", None)
        if client is None:
            await orig_run(self)
            return

        try:
            chat_completions = client.chat.completions
        except Exception:
            await orig_run(self)
            return

        orig_create = chat_completions.create

        async def _wrapped_create(*args, **kwargs):
            stream = await orig_create(*args, **kwargs)
            try:
                return _wrap_stream(stream)
            except Exception as e:
                logger.debug(f"[deepseek-cache-tokens] wrap skipped: {e}")
                return stream

        chat_completions.create = _wrapped_create  # type: ignore[assignment]
        try:
            await orig_run(self)
        finally:
            chat_completions.create = orig_create  # type: ignore[assignment]

    inf_llm.LLMStream._run = _patched
    inf_llm.LLMStream._jarvis_deepseek_cache_tokens_patched = True


def install() -> None:
    """Apply the DeepSeek cache-tokens backfill patch. Idempotent."""
    _patch_run()
    logger.info(
        "DeepSeek prompt_cache_hit_tokens backfill installed "
        "(gates on base_url=deepseek.com; never overwrites positive cached_tokens)"
    )
