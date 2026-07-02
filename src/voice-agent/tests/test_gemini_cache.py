"""Tests for the Gemini context-cache manager + dispatcher integration.

Verifies the contract for `providers.gemini_cache.GeminiCachedContentManager`:

  * `__init__` does NO network work (purely-lazy);
  * `get_cache_name()` provisions the cache on first call + reuses on
    subsequent calls;
  * the manager auto-refreshes when within `_REFRESH_LEAD_SECONDS` of
    TTL expiry (we monkeypatch `_REFRESH_LEAD_SECONDS` larger than the
    test TTL so the next call exercises the refresh branch immediately);
  * `caches.create` errors return `None` and DO NOT poison the manager
    for subsequent retries;
  * `close()` is idempotent and suppresses errors.

And the dispatcher-side contract:

  * `JARVIS_REASONING_MODEL=gemini-2.5-flash` with `GOOGLE_API_KEY` set
    routes through `_build_gemini_primary` → GeminiCachedLLM, but only
    when the `livekit-plugins-google` plugin is actually importable
    (CI does NOT install it, so we monkeypatch the import to make
    construction succeed against a stub);
  * without `GOOGLE_API_KEY`, the same route silently falls back to
    its Groq legacy.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Tests run from the voice-agent root.
sys.path.insert(0, str(Path(__file__).parent.parent))

# Match the keys-for-init pattern used by test_llm_dispatcher_build.
os.environ.setdefault("GROQ_API_KEY", "test-key-for-init")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-deepseek-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")


# ─────────────────────────────────────────────────────────────────────
# GeminiCachedContentManager unit tests
# ─────────────────────────────────────────────────────────────────────


def test_manager_lazy_init():
    """Constructing the manager must NOT touch the network or the
    google-genai client. The Google SDK import is the only side
    effect we allow at construction (it's a normal pure import)."""
    from providers.gemini_cache import GeminiCachedContentManager

    mgr = GeminiCachedContentManager(
        model_name="gemini-2.5-flash",
        system_prompt="x" * 5000,
        ttl_seconds=3600,
    )
    # No cache provisioned yet.
    assert mgr._cache_name is None
    # Client not constructed yet (lazy ensure_client only fires on
    # first get_cache_name call).
    assert mgr._client is None
    assert mgr._expires_at == 0.0


def test_get_cache_name_creates_on_first_call(monkeypatch):
    """`get_cache_name()` must invoke `client.caches.create` exactly
    once on the first call; subsequent calls reuse the cached name
    without re-creating."""
    from providers import gemini_cache as mod
    from providers.gemini_cache import GeminiCachedContentManager

    fake_response = MagicMock(name="created-cache")
    fake_response.name = "cachedContents/jarvis-test-abc123"

    fake_caches = MagicMock()
    fake_caches.create.return_value = fake_response

    fake_client = MagicMock()
    fake_client.caches = fake_caches

    mgr = GeminiCachedContentManager(
        model_name="gemini-2.5-flash",
        system_prompt="x" * 5000,
        ttl_seconds=3600,
    )
    # Skip the real client construction.
    mgr._client = fake_client

    name1 = mgr.get_cache_name()
    assert name1 == "cachedContents/jarvis-test-abc123"
    assert fake_caches.create.call_count == 1
    # The create call must use the model-prefixed id and pass the
    # configured TTL through.
    args, kwargs = fake_caches.create.call_args
    assert kwargs["model"] == "models/gemini-2.5-flash"
    # TTL string is duration-formatted seconds.
    cfg = kwargs["config"]
    assert getattr(cfg, "ttl", None) == "3600s"
    assert getattr(cfg, "system_instruction", None) == "x" * 5000

    # Second call must reuse — no additional create() invocation.
    name2 = mgr.get_cache_name()
    assert name2 == name1
    assert fake_caches.create.call_count == 1


def test_refresh_when_near_expiry(monkeypatch):
    """When `_REFRESH_LEAD_SECONDS` is configured large enough that the
    current TTL window is fully inside the refresh window, the next
    `get_cache_name()` must delete the existing resource and create a
    fresh one."""
    from providers import gemini_cache as mod
    from providers.gemini_cache import GeminiCachedContentManager

    # First and second responses — distinct names so we can verify
    # the refresh actually swapped them.
    first = MagicMock()
    first.name = "cachedContents/first-resource"
    second = MagicMock()
    second.name = "cachedContents/second-resource"

    fake_caches = MagicMock()
    fake_caches.create.side_effect = [first, second]
    fake_caches.delete.return_value = None

    fake_client = MagicMock()
    fake_client.caches = fake_caches

    mgr = GeminiCachedContentManager(
        model_name="gemini-2.5-flash",
        system_prompt="x" * 5000,
        ttl_seconds=10,  # 10s TTL
    )
    mgr._client = fake_client

    # Push the refresh lead WAY past the TTL so the immediate next
    # call considers the resource "near expiry" and refreshes it.
    monkeypatch.setattr(mod, "_REFRESH_LEAD_SECONDS", 100)

    name1 = mgr.get_cache_name()
    assert name1 == "cachedContents/first-resource"
    assert fake_caches.create.call_count == 1
    assert fake_caches.delete.call_count == 0

    # Now: (expires_at - now) < refresh_lead → triggers refresh.
    name2 = mgr.get_cache_name()
    assert name2 == "cachedContents/second-resource"
    assert fake_caches.create.call_count == 2
    # The old resource is best-effort deleted before the new create.
    fake_caches.delete.assert_called_once_with(name="cachedContents/first-resource")


def test_create_failure_returns_none(monkeypatch):
    """A raise from `caches.create` must yield None from
    `get_cache_name()` AND leave the manager in a state where a
    subsequent call can retry (no permanent poison)."""
    from providers.gemini_cache import GeminiCachedContentManager

    fake_caches = MagicMock()
    fake_caches.create.side_effect = RuntimeError("simulated Google 503")

    fake_client = MagicMock()
    fake_client.caches = fake_caches

    mgr = GeminiCachedContentManager(
        model_name="gemini-2.5-flash",
        system_prompt="x" * 5000,
        ttl_seconds=3600,
    )
    mgr._client = fake_client

    # First attempt fails — return None, no exception propagation.
    assert mgr.get_cache_name() is None
    assert fake_caches.create.call_count == 1

    # Manager isn't broken — second call retries.
    fake_response = MagicMock()
    fake_response.name = "cachedContents/after-retry"
    fake_caches.create.side_effect = None
    fake_caches.create.return_value = fake_response

    assert mgr.get_cache_name() == "cachedContents/after-retry"
    assert fake_caches.create.call_count == 2


def test_close_idempotent():
    """Calling close() multiple times must not raise. Subsequent
    get_cache_name() calls after close() return None without trying
    to provision anything."""
    from providers.gemini_cache import GeminiCachedContentManager

    fake_caches = MagicMock()
    fake_caches.delete.return_value = None

    fake_response = MagicMock()
    fake_response.name = "cachedContents/test-resource"
    fake_caches.create.return_value = fake_response

    fake_client = MagicMock()
    fake_client.caches = fake_caches

    mgr = GeminiCachedContentManager(
        model_name="gemini-2.5-flash",
        system_prompt="x" * 5000,
        ttl_seconds=3600,
    )
    mgr._client = fake_client

    # Provision a cache so close() has something to delete.
    mgr.get_cache_name()

    # First close: deletes once.
    mgr.close()
    assert fake_caches.delete.call_count == 1

    # Second close: no-op, no second delete.
    mgr.close()
    assert fake_caches.delete.call_count == 1

    # Post-close get_cache_name returns None without touching the API.
    fake_caches.create.reset_mock()
    assert mgr.get_cache_name() is None
    assert fake_caches.create.call_count == 0


def test_close_suppresses_delete_errors():
    """A failing `caches.delete` during close must NOT propagate —
    the cache will be GC'd on the Google side at TTL anyway."""
    from providers.gemini_cache import GeminiCachedContentManager

    fake_caches = MagicMock()
    fake_response = MagicMock()
    fake_response.name = "cachedContents/test-resource"
    fake_caches.create.return_value = fake_response
    fake_caches.delete.side_effect = RuntimeError("simulated delete 500")

    fake_client = MagicMock()
    fake_client.caches = fake_caches

    mgr = GeminiCachedContentManager(
        model_name="gemini-2.5-flash",
        system_prompt="x" * 5000,
    )
    mgr._client = fake_client
    mgr.get_cache_name()

    # Must not raise even though delete fails.
    mgr.close()
    # Internal state still cleared.
    assert mgr._cache_name is None


def test_ensure_client_raises_without_api_key(monkeypatch):
    """Missing GOOGLE_API_KEY at client-construction time must surface
    as a RuntimeError inside `_ensure_client` — which `get_cache_name`
    catches and converts to None."""
    from providers.gemini_cache import GeminiCachedContentManager

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    mgr = GeminiCachedContentManager(
        model_name="gemini-2.5-flash",
        system_prompt="x" * 5000,
    )
    # Public API returns None gracefully.
    assert mgr.get_cache_name() is None


# ─────────────────────────────────────────────────────────────────────
# Dispatcher integration tests
# ─────────────────────────────────────────────────────────────────────


def _wipe_route_env(monkeypatch) -> None:
    """Strip any per-route override env vars left over from other tests
    (mirrors the helper in test_llm_dispatcher_build)."""
    for var in (
        "JARVIS_BANTER_MODEL",
        "JARVIS_TASK_MODEL",
        "JARVIS_REASONING_MODEL",
        "JARVIS_EMOTIONAL_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


def _install_fake_gemini_llm_module(monkeypatch):
    """Inject a stub `providers.gemini_llm.GeminiCachedLLM` that doesn't
    require livekit-plugins-google. Returns the captured-instances list
    so the caller can assert what got constructed.

    Why: `livekit-plugins-google` is intentionally NOT installed in the
    voice-agent venv (the plugin is optional + the actual route flip is
    deferred). Tests still need to verify the dispatcher CALLS the right
    builder when an operator sets the env override — for that we sub
    in a fake.
    """
    import importlib
    import types as _types

    instances: list[MagicMock] = []

    # Subclass the real livekit LLM base so FallbackAdapter accepts it
    # (FallbackAdapter validates rungs implement the LLM event-emitter
    # interface — `.on`, `.off`, `.emit`, etc.). A bare object stub
    # is rejected with `'FakeGeminiCachedLLM' object has no attribute 'on'`.
    from livekit.agents.llm import LLM as _LiveKitLLM

    class FakeGeminiCachedLLM(_LiveKitLLM):
        def __init__(self, *, model, api_key, temperature=0.6, max_output_tokens=None):
            super().__init__()
            self._model_str = model
            self.api_key = api_key
            self.temperature = temperature
            self.max_output_tokens = max_output_tokens
            # Mimic the GeminiCachedLLM contract: has a cache_mgr
            # attribute (None until first chat() call).
            self._cache_mgr = None
            instances.append(self)

        @property
        def model(self):
            return self._model_str

        def chat(self, **kwargs):
            raise NotImplementedError("test stub — chat not exercised")

        async def aclose(self):
            pass

    fake_mod = _types.ModuleType("providers.gemini_llm")
    fake_mod.GeminiCachedLLM = FakeGeminiCachedLLM
    monkeypatch.setitem(sys.modules, "providers.gemini_llm", fake_mod)
    return instances


def test_dispatcher_with_gemini_route_uses_cache(monkeypatch):
    """When `JARVIS_REASONING_MODEL=gemini-2.5-flash` AND `GOOGLE_API_KEY`
    is set, the REASONING route must build via the GeminiCachedLLM path
    (label `gemini:<model>`). The other routes stay on their Anthropic
    defaults."""
    _wipe_route_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
    monkeypatch.setenv("JARVIS_REASONING_MODEL", "gemini-2.5-flash")

    instances = _install_fake_gemini_llm_module(monkeypatch)

    from providers.llm import build_dispatching_llm

    d = build_dispatching_llm()

    # Walk down the FallbackAdapter to find REASONING's rung-1 LLM.
    from livekit.agents.llm import FallbackAdapter
    reasoning = d.pick("REASONING")
    assert isinstance(reasoning, FallbackAdapter)
    rungs = (
        getattr(reasoning, "_llm_instances", None)
        or getattr(reasoning, "_llms", None)
        or []
    )
    rung_labels = [getattr(r, "_jarvis_label", "") for r in rungs]
    # Rung 1 must be Gemini.
    assert rung_labels[0] == "gemini:gemini-2.5-flash", (
        f"REASONING rung 1 expected gemini:gemini-2.5-flash, got {rung_labels[0]!r} "
        f"(full chain: {rung_labels})"
    )

    # The fake GeminiCachedLLM was instantiated exactly once for the
    # REASONING route — TASK/BANTER/EMOTIONAL still went to Anthropic.
    assert len(instances) == 1
    assert instances[0].model == "gemini-2.5-flash"
    assert instances[0].api_key == "test-google-key"

    # Other routes are unchanged.
    def _label_for(route):
        inner = d.pick(route)
        if isinstance(inner, FallbackAdapter):
            r = (
                getattr(inner, "_llm_instances", None)
                or getattr(inner, "_llms", None)
                or [inner]
            )
            return getattr(r[0], "_jarvis_label", "")
        return getattr(inner, "_jarvis_label", "")

    assert _label_for("BANTER") == "anthropic:claude-haiku-4-5"
    assert _label_for("TASK") == "anthropic:claude-haiku-4-5"
    assert _label_for("EMOTIONAL") == "anthropic:claude-haiku-4-5"


def test_dispatcher_without_google_key_degrades(monkeypatch):
    """When `JARVIS_REASONING_MODEL=gemini-2.5-flash` is set but
    GOOGLE_API_KEY is MISSING, the dispatcher must still boot, and
    the REASONING route must fall back to the shared DeepSeek instance
    (the Groq legacy rung it used was removed 2026-06-29). NO Gemini
    construction is attempted in this path."""
    _wipe_route_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("JARVIS_REASONING_MODEL", "gemini-2.5-flash")

    instances = _install_fake_gemini_llm_module(monkeypatch)

    from providers.llm import build_dispatching_llm

    d = build_dispatching_llm()

    # The fake builder must NOT have been called — the GOOGLE_API_KEY
    # gate short-circuits before we reach `from providers.gemini_llm`.
    assert len(instances) == 0

    # REASONING falls back to the shared DeepSeek instance.
    from livekit.agents.llm import FallbackAdapter
    reasoning = d.pick("REASONING")
    if isinstance(reasoning, FallbackAdapter):
        rungs = (
            getattr(reasoning, "_llm_instances", None)
            or getattr(reasoning, "_llms", None)
            or []
        )
        label = getattr(rungs[0], "_jarvis_label", "") if rungs else ""
    else:
        label = getattr(reasoning, "_jarvis_label", "")
    assert label.startswith("deepseek:"), (
        f"REASONING degraded fallback expected deepseek, got {label!r}"
    )


def test_dispatcher_with_gemini_plugin_missing_degrades(monkeypatch):
    """When `GOOGLE_API_KEY` is set but the Gemini wrapper can't import
    (missing `livekit-plugins-google`), the dispatcher catches the
    ImportError and falls back to the shared DeepSeek instance (the Groq
    legacy rung it used was removed 2026-06-29). The plugin now ships in
    requirements, so simulate the failure: `None` in sys.modules makes
    `import providers.gemini_llm` raise ImportError deterministically."""
    _wipe_route_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
    monkeypatch.setenv("JARVIS_REASONING_MODEL", "gemini-2.5-flash")
    monkeypatch.setitem(sys.modules, "providers.gemini_llm", None)

    from providers.llm import build_dispatching_llm

    d = build_dispatching_llm()

    from livekit.agents.llm import FallbackAdapter
    reasoning = d.pick("REASONING")
    if isinstance(reasoning, FallbackAdapter):
        rungs = (
            getattr(reasoning, "_llm_instances", None)
            or getattr(reasoning, "_llms", None)
            or []
        )
        label = getattr(rungs[0], "_jarvis_label", "") if rungs else ""
    else:
        label = getattr(reasoning, "_jarvis_label", "")
    # The Gemini route degraded to DeepSeek, but everything else still
    # built — dispatcher is functional, no Gemini-pin crash.
    assert label.startswith("deepseek:")


def test_speech_models_gemini_entries_present():
    """`SPEECH_MODELS` must expose `gemini-2.5-flash` and `gemini-2.5-pro`
    so the tray picker can list them. The `build` lambda raises
    ImportError when GOOGLE_API_KEY is unset (so make_speech_llm
    cleanly falls back to DEFAULT_SPEECH_MODEL)."""
    from providers.llm import SPEECH_MODELS

    assert "gemini-2.5-flash" in SPEECH_MODELS
    assert "gemini-2.5-pro" in SPEECH_MODELS
    # Labels are present and human-readable.
    assert "Gemini" in SPEECH_MODELS["gemini-2.5-flash"]["label"]
    assert "Gemini" in SPEECH_MODELS["gemini-2.5-pro"]["label"]


def test_speech_models_gemini_build_raises_without_key(monkeypatch):
    """SPEECH_MODELS['gemini-2.5-flash']['build']() must raise
    ImportError when GOOGLE_API_KEY isn't set — that's the shape
    `make_speech_llm` expects so it can fall back to default."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    from providers.llm import SPEECH_MODELS

    with pytest.raises(ImportError):
        SPEECH_MODELS["gemini-2.5-flash"]["build"]()


def test_extract_system_prompt_handles_list_content():
    """`extract_system_prompt` must handle both str-content and
    list-of-str-content ChatMessage shapes; LiveKit's ChatMessage is
    permissive on that field. Lives in `providers.gemini_cache` so the
    test runs without `livekit-plugins-google` installed."""
    from providers.gemini_cache import extract_system_prompt

    class _StubMsg:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    class _StubCtx:
        def __init__(self, items):
            self.items = items

    ctx = _StubCtx([
        _StubMsg("system", "rule one"),
        _StubMsg("system", ["rule two", "rule three"]),
        _StubMsg("user", "ignore me"),
    ])
    out = extract_system_prompt(ctx)
    assert "rule one" in out
    assert "rule two" in out
    assert "rule three" in out
    assert "ignore me" not in out


def test_extract_system_prompt_empty_inputs():
    """No chat_ctx, empty items, no system messages → empty string
    (caller treats as 'no cacheable prefix' and skips caching)."""
    from providers.gemini_cache import extract_system_prompt

    assert extract_system_prompt(None) == ""

    class _Ctx:
        items = []
    assert extract_system_prompt(_Ctx()) == ""

    class _Msg:
        role = "user"
        content = "no system here"
    class _UserOnlyCtx:
        items = [_Msg()]
    assert extract_system_prompt(_UserOnlyCtx()) == ""
