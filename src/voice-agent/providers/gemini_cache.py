"""Google Gemini context-cache manager for the JARVIS voice agent.

Background
----------
Gemini does NOT auto-cache the system prompt the way Anthropic / OpenAI
/ DeepSeek do (Anthropic charges for the first turn and refunds the
prefix on every subsequent hit; OpenAI/DeepSeek hash-match transparently).
For Gemini, the caller must explicitly create a `CachedContent` resource
via `client.caches.create(...)` and then pass `cached_content="<name>"`
on every `generate_content` call. Without this:

  - The full system prompt (~33 k tokens for JARVIS today) is re-uploaded
    on EVERY turn → ~2-5 s of input-tokenization latency + full per-token
    billing on every turn.
  - With caching wired correctly: ~700-900 ms TTFW (warm cache), and the
    cached prefix bills at ~25 % of the live-input rate (Flash) / ~10 %
    (Pro 2.5) — same order as Anthropic's `caching="ephemeral"` discount.

What this manager owns
----------------------
- Lazy: no network until `get_cache_name()` is first called.
- Thread-safe (single `threading.Lock`) around create + refresh.
- Refreshes the cache resource when within 5 minutes of TTL expiry.
- Soft-fails: any error returns `None` from `get_cache_name()`; caller
  falls back to sending the system prompt inline. The audio loop is
  never blocked by a caches-API hiccup.

Stable-prefix contract (2026-05-23 refactor)
--------------------------------------------
This manager caches the system prompt string passed to `__init__` AS IS
and expects the CALLER to pass ONLY the stable prefix —
SOUL + JARVIS_INSTRUCTIONS + skill_catalog_block — never the per-turn
volatile suffix (runtime_id + memory + breaker). With that contract:

  - Cache provisioning is a one-shot per session (no churn).
  - Every turn's request references the cached resource via
    ``cached_content="<name>"`` and passes the volatile suffix as the
    inline ``system_instruction``.

The wrapper LLM (`providers.gemini_llm.GeminiCachedLLM`) honors the
split contract via `providers.prompt_cache.split_system_text`: when the
chat_ctx's joined system text starts with the expected stable prefix,
it splits on that boundary. The wrapper used to compute a hash of the
full system message and bypass caching whenever the hash drifted from
the cached version ("drift-aware bypass"); that approach made every
memory write a cache miss for the rest of the session. The new
architecture eliminates the drift problem entirely by keeping the
cached resource bound to the immutable stable prefix and shipping
volatile changes inline each turn.

Minimum prompt size to be cacheable: 1024 tokens (Flash) or 4096 tokens
(Pro 2.5) per https://ai.google.dev/gemini-api/docs/caching. JARVIS's
stable prefix is ~28 k tokens, well above either threshold.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger("jarvis.gemini_cache")


__all__ = ["GeminiCachedContentManager", "extract_system_prompt"]


def extract_system_prompt(chat_ctx) -> str:
    """Concatenate all role='system' message text from a livekit
    ChatContext. Returns ``""`` when no system messages are present.

    Pulled out of the LLM wrapper class so tests can exercise it
    without depending on `livekit-plugins-google` being installed
    (the wrapper module's top-level import requires that plugin)."""
    if chat_ctx is None:
        return ""
    items = getattr(chat_ctx, "items", None) or []
    parts: list[str] = []
    for it in items:
        if getattr(it, "role", None) != "system":
            continue
        content = getattr(it, "content", None)
        # content can be str OR list[str|...] per livekit's
        # ChatMessage shape.
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for sub in content:
                if isinstance(sub, str):
                    parts.append(sub)
    return "\n".join(parts)


# Refresh when within this many seconds of TTL expiry. Picked to be
# comfortably less than the default 5-minute Anthropic warm-cache
# window so a stale cache never lands in a turn.
_REFRESH_LEAD_SECONDS: int = 300


class GeminiCachedContentManager:
    """Lazy, thread-safe holder for a Gemini `CachedContent` resource.

    Construction does NO network. The cache resource is created on the
    first call to `get_cache_name()` and refreshed on subsequent calls
    when it's within `_REFRESH_LEAD_SECONDS` of its TTL expiry.

    Parameters
    ----------
    model_name : str
        Fully-qualified Gemini model id (e.g. ``"gemini-2.5-flash"``).
        Caches are model-scoped on the Google side; using one cache
        across two different models returns 400.
    system_prompt : str
        The string to cache. Must be ≥ 1024 tokens for Flash, ≥ 4096
        for Pro 2.5 per the Gemini API spec — the manager does not
        validate this (the create call will surface the API's error).
        Pass ONLY the stable prefix (see module docstring).
    ttl_seconds : int, default 3600
        TTL on the cache resource. Google's default is also 1 h; the
        manager refreshes when within ``_REFRESH_LEAD_SECONDS`` of expiry.
    """

    def __init__(
        self,
        model_name: str,
        system_prompt: str,
        ttl_seconds: int = 3600,
    ) -> None:
        self._model_name = model_name
        self._system_prompt = system_prompt
        self._ttl_seconds = ttl_seconds
        # Active resource name (None until first create / on failure).
        self._cache_name: Optional[str] = None
        # Wall-clock expiry of the active resource (monotonic time).
        self._expires_at: float = 0.0
        # google-genai client built lazily so __init__ stays network-free.
        self._client = None
        # Reentrant lock: refresh-on-expiry can be triggered from
        # multiple worker threads in parallel; only one should win.
        self._lock = threading.Lock()
        # Closed flag — calling get_cache_name() after close() returns
        # None without attempting any network work.
        self._closed = False

    # ── public API ─────────────────────────────────────────────────────

    def get_cache_name(self) -> Optional[str]:
        """Return the active cache resource name, creating or refreshing
        as needed. Returns ``None`` on any error (caller should send the
        prompt inline that turn)."""
        if self._closed:
            return None
        with self._lock:
            try:
                now = time.monotonic()
                # Fresh enough → reuse.
                if (
                    self._cache_name is not None
                    and (self._expires_at - now) > _REFRESH_LEAD_SECONDS
                ):
                    return self._cache_name
                # Either no cache yet or it's close to expiry → (re)create.
                # If refreshing, best-effort delete the old one first so we
                # don't leak resources on the Google side.
                if self._cache_name is not None:
                    logger.info(
                        f"[gemini-cache] refreshing cache "
                        f"(was {self._cache_name}, within {_REFRESH_LEAD_SECONDS}s of expiry)"
                    )
                    self._delete_locked(self._cache_name)
                    self._cache_name = None
                    self._expires_at = 0.0
                self._create_locked()
                return self._cache_name
            except Exception as e:
                # Any failure — surface as None so the caller falls back
                # to sending the prompt inline. Never raise into the audio
                # loop.
                logger.warning(
                    f"[gemini-cache] get_cache_name failed for model "
                    f"{self._model_name!r}: {type(e).__name__}: {e}"
                )
                return None

    def close(self) -> None:
        """Best-effort delete of the active cache resource. Idempotent —
        safe to call multiple times. Suppresses all errors."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._cache_name is None:
                return
            try:
                self._delete_locked(self._cache_name)
            except Exception as e:
                logger.warning(
                    f"[gemini-cache] close: delete failed for "
                    f"{self._cache_name!r}: {type(e).__name__}: {e}"
                )
            finally:
                self._cache_name = None
                self._expires_at = 0.0

    # ── internals (lock held by caller) ─────────────────────────────────

    def _ensure_client(self):
        """Construct the google-genai client on first use. Reads
        ``GOOGLE_API_KEY`` from the environment. Raises if the SDK is
        unavailable or the key is missing — caller's try/except in
        `get_cache_name` converts this into a soft-None return."""
        if self._client is not None:
            return self._client
        try:
            from google import genai  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "google-genai SDK not installed; "
                "pip install google-genai to enable Gemini caching"
            ) from e
        api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY missing — cannot create Gemini cache"
            )
        self._client = genai.Client(api_key=api_key)
        return self._client

    def _create_locked(self) -> None:
        """Issue the actual `caches.create` call. Caller MUST hold
        ``self._lock``. Updates ``_cache_name`` + ``_expires_at`` on
        success; raises otherwise (caller converts to soft-None)."""
        from google.genai import types  # type: ignore

        client = self._ensure_client()

        # `ttl` is a duration string per the protobuf convention.
        ttl_str = f"{self._ttl_seconds}s"
        config = types.CreateCachedContentConfig(
            system_instruction=self._system_prompt,
            ttl=ttl_str,
            display_name="jarvis-supervisor-prefix",
        )
        logger.info(
            f"[gemini-cache] creating cache for model={self._model_name!r} "
            f"ttl={ttl_str} system_prompt_len={len(self._system_prompt)}"
        )
        # Gemini insists the model id is prefixed with "models/" for the
        # cache create call (and only this call — generate_content accepts
        # the bare id). Match either input form.
        model_id = self._model_name
        if not model_id.startswith("models/"):
            model_id = f"models/{model_id}"
        cached = client.caches.create(model=model_id, config=config)
        self._cache_name = getattr(cached, "name", None)
        if not self._cache_name:
            raise RuntimeError(
                "caches.create returned no resource name; aborting cache use"
            )
        # Track expiry on the monotonic clock so wall-clock skew doesn't
        # confuse the refresh logic.
        self._expires_at = time.monotonic() + self._ttl_seconds
        logger.info(
            f"[gemini-cache] created cache {self._cache_name} "
            f"(expires in {self._ttl_seconds}s)"
        )

    def _delete_locked(self, name: str) -> None:
        """Issue the actual `caches.delete` call. Caller MUST hold
        ``self._lock``. Suppresses errors locally (close()/refresh path
        both handle them at the caller layer)."""
        try:
            client = self._ensure_client()
            client.caches.delete(name=name)
            logger.info(f"[gemini-cache] deleted cache {name}")
        except Exception as e:
            # Don't let a stale-cache delete failure block a fresh create.
            # The Google side will GC the resource at TTL anyway.
            logger.warning(
                f"[gemini-cache] delete {name!r} failed: "
                f"{type(e).__name__}: {e}"
            )
