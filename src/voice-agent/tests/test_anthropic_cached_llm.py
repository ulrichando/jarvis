"""Tests for the Anthropic cached LLM subclass — stable/volatile split.

Verifies that the new ``providers.anthropic_cached_llm.AnthropicCachedLLM``
wrapper places ``cache_control`` on the STABLE prefix (block 0) rather
than the LAST system block (the parent plugin's default behaviour with
``caching="ephemeral"``). The cache breakpoint sitting at the
stable/volatile boundary is the load-bearing change behind the
≥95 % cache-hit target.

These tests don't hit the live Anthropic API — they exercise the
``chat()`` body up to the ``self._client.messages.create(...)`` call
with a mocked client, then inspect the call's ``system=`` kwarg to
confirm the structural contract.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Tests run from the voice-agent root.
sys.path.insert(0, str(Path(__file__).parent.parent))

# anthropic plugin reads ANTHROPIC_API_KEY at __init__ time.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("GROQ_API_KEY", "test-key-for-init")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-deepseek-key")


def _drive_chat(wrapper, ctx):
    """Drive `wrapper.chat(chat_ctx=ctx)` inside an event loop.

    The LiveKit ``LLMStream.__init__`` schedules a metrics-monitor task
    via ``asyncio.create_task``, which requires a running event loop.
    These tests don't actually consume the stream — they only need the
    ``messages.create(...)`` call to fire so the kwargs are captured."""

    async def _run():
        # The chat() call is synchronous (it returns the stream), but
        # it must execute INSIDE a running event loop for the stream's
        # __init__ to succeed.
        wrapper.chat(chat_ctx=ctx)

    asyncio.run(_run())


def _build_wrapper(stable_prefix: str | None = None, model: str = "claude-haiku-4-5"):
    """Construct an AnthropicCachedLLM with mocked transport.

    Returns ``(wrapper, mock_messages_create)`` so the test can drive
    ``wrapper.chat()`` and inspect the recorded ``system=`` payload."""
    from providers.anthropic_cached_llm import AnthropicCachedLLM

    wrapper = AnthropicCachedLLM(
        model=model,
        api_key="test-anthropic-key",
        temperature=0.6,
        max_tokens=200,
        stable_prefix=stable_prefix,
    )
    # Replace the AsyncClient's messages.create with a mock so the
    # chat() call returns without going to the network.
    mock_create = MagicMock(return_value=MagicMock())
    wrapper._client.messages.create = mock_create
    wrapper._client.beta.messages.create = mock_create
    return wrapper, mock_create


def _build_chat_ctx(system_text: str, user_text: str = "Hello"):
    """Build a minimal ChatContext with one system message + one user message."""
    from livekit.agents.llm import ChatContext, ChatMessage

    items = [
        ChatMessage(id="sys", role="system", content=[system_text]),
        ChatMessage(id="usr", role="user", content=[user_text]),
    ]
    return ChatContext(items=items)


# ──────────────────────────────────────────────────────────────────────
# Core contract: when stable_prefix matches, system is 2-element list
# with cache_control on block 0.
# ──────────────────────────────────────────────────────────────────────


def test_exact_prefix_split_places_cache_on_block_0():
    """When the system text starts with the configured stable_prefix,
    the wrapper must emit a 2-element ``system=[...]`` list with
    ``cache_control`` on block 0 (the stable prefix) and NO
    cache_control on block 1 (the volatile suffix). This is the core
    contract — volatile changes leave the cache valid."""
    stable = "STABLE: SOUL + INSTRUCTIONS\n" * 50  # ~1.5KB representative
    volatile = "VOLATILE: runtime-id + memory + breaker"
    full = stable + volatile

    wrapper, mock_create = _build_wrapper(stable_prefix=stable)
    ctx = _build_chat_ctx(full)
    _drive_chat(wrapper, ctx)

    # The mocked create captured the call kwargs.
    assert mock_create.called, "messages.create was not invoked"
    kwargs = mock_create.call_args.kwargs
    system_blocks = kwargs.get("system")
    assert isinstance(system_blocks, list), (
        f"system must be a list, got {type(system_blocks).__name__}"
    )
    assert len(system_blocks) == 2, (
        f"system must have exactly 2 blocks (stable + volatile), "
        f"got {len(system_blocks)}: {[b.get('text', '')[:30] for b in system_blocks]}"
    )

    # Block 0 = stable prefix, marked cached.
    assert system_blocks[0]["text"] == stable
    assert system_blocks[0]["type"] == "text"
    assert system_blocks[0].get("cache_control") == {"type": "ephemeral", "ttl": "1h"}

    # Block 1 = volatile suffix, NOT cached.
    assert system_blocks[1]["text"] == volatile
    assert system_blocks[1]["type"] == "text"
    assert "cache_control" not in system_blocks[1]


def test_marker_split_when_stable_prefix_unset():
    """When no stable_prefix is configured but the system text contains
    ``CACHE_BREAK_MARKER``, the wrapper splits on the marker. This is
    the fallback path for wrappers constructed before the prompt state
    assembled (e.g. early-built speech LLMs)."""
    from providers.prompt_cache import CACHE_BREAK_MARKER

    stable = "MARKED STABLE BLOCK"
    volatile = "MARKED VOLATILE BLOCK"
    full = f"{stable}\n{CACHE_BREAK_MARKER}\n{volatile}"

    wrapper, mock_create = _build_wrapper(stable_prefix=None)
    ctx = _build_chat_ctx(full)
    _drive_chat(wrapper, ctx)

    kwargs = mock_create.call_args.kwargs
    system_blocks = kwargs.get("system")
    assert len(system_blocks) == 2
    assert system_blocks[0]["text"] == stable
    assert system_blocks[0].get("cache_control") == {"type": "ephemeral", "ttl": "1h"}
    assert system_blocks[1]["text"] == volatile
    assert "cache_control" not in system_blocks[1]


# ──────────────────────────────────────────────────────────────────────
# Empty/whitespace text-block guard (2026-06-23 "looking it up, never
# returns the result" incident). Anthropic 400s the WHOLE request with
# "messages: text content blocks must contain non-whitespace text" if any
# message carries an empty text block; chat_ctx serialization emits these
# for interrupted/cancelled assistant turns. Once one lands in history,
# every supervisor turn 400s (not retryable) → the agent goes silent and
# never delivers the (already-retrieved) result.
# ──────────────────────────────────────────────────────────────────────


def test_strip_empties_drops_whitespace_text_block():
    """A whitespace-only text block is removed; the message survives with a
    placeholder so role alternation / tool pairing isn't broken."""
    from providers.anthropic_cached_llm import _strip_empty_text_blocks

    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "what were the scores?"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "   "}]},
    ]
    n = _strip_empty_text_blocks(msgs)
    assert n == 1
    for m in msgs:
        for b in m["content"]:
            if isinstance(b, dict) and b.get("type") == "text":
                assert b["text"].strip(), f"empty text block survived: {b!r}"
    assert msgs[1]["content"], "assistant message must not be left with empty content"


def test_strip_empties_keeps_tool_use_block():
    """An empty text block alongside a tool_use is dropped, the tool_use kept —
    a tool call with no preamble text must still execute."""
    from providers.anthropic_cached_llm import _strip_empty_text_blocks

    msgs = [{
        "role": "assistant",
        "content": [
            {"type": "text", "text": ""},
            {"type": "tool_use", "id": "t1", "name": "web_search", "input": {}},
        ],
    }]
    _strip_empty_text_blocks(msgs)
    types = [b.get("type") for b in msgs[0]["content"]]
    assert types == ["tool_use"], f"expected only tool_use, got {types}"


def test_strip_empties_handles_string_content():
    """Plain-string content that is whitespace-only is replaced, not left empty
    (Anthropic rejects empty string content too)."""
    from providers.anthropic_cached_llm import _strip_empty_text_blocks

    msgs = [{"role": "user", "content": "   "}]
    _strip_empty_text_blocks(msgs)
    assert msgs[0]["content"].strip(), "whitespace string content must be replaced"


def test_strip_empties_leaves_valid_messages_untouched():
    """No empty blocks → no mutation, nothing reported cleaned."""
    from providers.anthropic_cached_llm import _strip_empty_text_blocks

    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
    ]
    before = [[dict(b) for b in m["content"]] for m in msgs]
    n = _strip_empty_text_blocks(msgs)
    assert n == 0
    assert [m["content"] for m in msgs] == before


def test_no_empty_text_block_reaches_anthropic_create():
    """End-to-end wiring: when serialization yields a message with an empty text
    block (the incident condition), chat() must strip it BEFORE the
    messages.create(...) call so Anthropic never 400s."""
    from unittest.mock import MagicMock, patch

    wrapper, mock_create = _build_wrapper()
    ctx = _build_chat_ctx("system prompt")

    poisoned_messages = [
        {"role": "user", "content": [{"type": "text", "text": "what were the scores?"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "   "}]},  # cancelled turn
    ]
    extra_data = MagicMock()
    extra_data.system_messages = []
    with patch.object(type(ctx), "to_provider_format", return_value=(poisoned_messages, extra_data)):
        _drive_chat(wrapper, ctx)

    assert mock_create.called, "messages.create was not invoked"
    sent = mock_create.call_args.kwargs["messages"]
    for m in sent:
        content = m["content"]
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    assert b["text"].strip(), f"empty text block sent to Anthropic: {b!r}"
        elif isinstance(content, str):
            assert content.strip(), "empty string content sent to Anthropic"


def test_no_split_falls_back_to_last_block_cache():
    """When the system text has neither a stable_prefix match nor a
    marker, the wrapper must NOT crash — it falls back to the parent
    plugin's default behaviour (one block per original system message,
    ``cache_control`` on the LAST block). Caching still works for the
    single-message case; this is the behaviour an un-wrapped
    ``lk_anthropic.LLM`` with ``caching="ephemeral"`` produces."""
    full = "JUST A FLAT SYSTEM PROMPT NO MARKER NO PREFIX"

    wrapper, mock_create = _build_wrapper(stable_prefix=None)
    ctx = _build_chat_ctx(full)
    _drive_chat(wrapper, ctx)

    kwargs = mock_create.call_args.kwargs
    system_blocks = kwargs.get("system")
    assert len(system_blocks) == 1
    assert system_blocks[0]["text"] == full
    # The last block (= the only block) is cached — parent plugin default.
    assert system_blocks[0].get("cache_control") == {"type": "ephemeral", "ttl": "1h"}


def test_set_stable_prefix_late_binding():
    """``set_stable_prefix()`` must update the wrapper so subsequent
    chat() calls use exact-prefix split. This is the path used by
    ``apply_stable_prefix_recursively`` after the prompt state
    assembles."""
    stable = "LATE-BOUND STABLE PREFIX " * 20
    volatile = " ... VOLATILE TAIL"
    full = stable + volatile

    wrapper, mock_create = _build_wrapper(stable_prefix=None)

    # First call without binding — no split (falls back to single-block).
    ctx_1 = _build_chat_ctx(full)
    _drive_chat(wrapper, ctx_1)
    blocks_first = mock_create.call_args.kwargs.get("system")
    assert len(blocks_first) == 1  # full prompt in one block

    # Bind the prefix, then call again — now 2-block split.
    wrapper.set_stable_prefix(stable)
    ctx_2 = _build_chat_ctx(full)
    _drive_chat(wrapper, ctx_2)
    blocks_second = mock_create.call_args.kwargs.get("system")
    assert len(blocks_second) == 2
    assert blocks_second[0]["text"] == stable
    assert blocks_second[0].get("cache_control") == {"type": "ephemeral", "ttl": "1h"}
    assert blocks_second[1]["text"] == volatile
    assert "cache_control" not in blocks_second[1]


def test_volatile_change_preserves_stable_block_text():
    """Two consecutive chat() calls with the same stable_prefix but
    DIFFERENT volatile suffixes — block 0 (stable) must be byte-identical
    across both, so Anthropic's prompt cache hashes it as a hit on the
    second call. This is the load-bearing assertion behind the
    ≥95 % cache-hit target."""
    stable = "IDENTICAL STABLE PREFIX " * 30
    volatile_a = "RUNTIME-V1 MEMORY-V1 BREAKER-OK"
    volatile_b = "RUNTIME-V1 MEMORY-V2 BREAKER-OK"  # one new memory write

    wrapper, mock_create = _build_wrapper(stable_prefix=stable)

    _drive_chat(wrapper, _build_chat_ctx(stable + volatile_a))
    blocks_a = mock_create.call_args.kwargs.get("system")

    _drive_chat(wrapper, _build_chat_ctx(stable + volatile_b))
    blocks_b = mock_create.call_args.kwargs.get("system")

    # The stable block (block 0) is byte-identical across turns.
    assert blocks_a[0]["text"] == blocks_b[0]["text"]
    assert blocks_a[0].get("cache_control") == {"type": "ephemeral", "ttl": "1h"}
    assert blocks_b[0].get("cache_control") == {"type": "ephemeral", "ttl": "1h"}

    # Only the volatile block (block 1) differs.
    assert blocks_a[1]["text"] != blocks_b[1]["text"]
    assert blocks_a[1]["text"] == volatile_a
    assert blocks_b[1]["text"] == volatile_b


# ──────────────────────────────────────────────────────────────────────
# Cache_control discipline
# ──────────────────────────────────────────────────────────────────────


def test_only_one_system_breakpoint():
    """Anthropic accepts ≤ 4 cache breakpoints per request. The
    wrapper must place exactly ONE on the system blocks (on block 0
    when split, on the last block when not). Multiple system-side
    breakpoints would waste the breakpoint budget for tools / history."""
    stable = "STABLE " * 100
    volatile = "VOLATILE"
    full = stable + volatile

    wrapper, mock_create = _build_wrapper(stable_prefix=stable)
    ctx = _build_chat_ctx(full)
    _drive_chat(wrapper, ctx)

    blocks = mock_create.call_args.kwargs.get("system")
    cached_count = sum(1 for b in blocks if b.get("cache_control"))
    assert cached_count == 1, (
        f"expected exactly 1 cache_control on system blocks, got {cached_count}"
    )


def test_no_caching_kwarg_passed_to_parent():
    """The subclass forces ``caching=NOT_GIVEN`` so the parent's
    auto-placement of cache_control on the LAST system block doesn't
    double-mark our stable block. ``self._opts.caching`` should not be
    "ephemeral"."""
    from providers.anthropic_cached_llm import AnthropicCachedLLM
    from livekit.agents.utils import is_given

    wrapper = AnthropicCachedLLM(
        model="claude-haiku-4-5",
        api_key="test-anthropic-key",
        caching="ephemeral",  # passed but should be ignored
    )
    # The opts dataclass holds caching; for our subclass it must NOT
    # resolve to "ephemeral" or the parent's chat() path would interfere.
    # We explicitly popped it in __init__ so the parent stores NOT_GIVEN.
    assert not (is_given(wrapper._opts.caching) and wrapper._opts.caching == "ephemeral")


# ──────────────────────────────────────────────────────────────────────
# Sampling-param gate — Opus 4.7+/Fable reject temperature/top_k (400)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model", ["claude-opus-4-7", "claude-opus-4-8", "claude-fable-5"]
)
def test_sampling_params_omitted_for_opus_47_plus(model):
    """Opus 4.7+/Fable reject temperature/top_k with a 400. The shared
    builder sets temperature=0.6 for every Claude tier, so the wrapper
    must drop it on the wire for these models — otherwise every
    tray-select / escalation turn fails. This is the bug fix."""
    wrapper, mock_create = _build_wrapper(stable_prefix=None, model=model)
    _drive_chat(wrapper, _build_chat_ctx("FLAT SYSTEM PROMPT"))

    assert mock_create.called, "messages.create was not invoked"
    kwargs = mock_create.call_args.kwargs
    assert "temperature" not in kwargs, (
        f"{model} must NOT receive temperature (400s); "
        f"got {kwargs.get('temperature')!r}"
    )
    assert "top_k" not in kwargs, f"{model} must NOT receive top_k (400s)"


@pytest.mark.parametrize("model", ["claude-haiku-4-5", "claude-sonnet-4-6"])
def test_sampling_params_kept_for_haiku_sonnet(model):
    """Haiku 4.5 / Sonnet 4.6 still accept temperature — the wrapper must
    keep forwarding the configured 0.6 so behaviour is unchanged for the
    default voice path."""
    wrapper, mock_create = _build_wrapper(stable_prefix=None, model=model)
    _drive_chat(wrapper, _build_chat_ctx("FLAT SYSTEM PROMPT"))

    kwargs = mock_create.call_args.kwargs
    assert kwargs.get("temperature") == 0.6, (
        f"{model} must keep temperature=0.6; got {kwargs.get('temperature')!r}"
    )


@pytest.mark.parametrize(
    "model,rejects",
    [
        ("claude-haiku-4-5", False),
        ("claude-sonnet-4-6", False),
        ("claude-opus-4-6", False),   # ≤ 4.6 still accepts sampling params
        ("claude-opus-4-7", True),
        ("claude-opus-4-8", True),
        ("claude-opus-4-10", True),   # forward-looking: 4.10+
        ("claude-opus-5", True),      # forward-looking: Opus 5+
        ("claude-fable-5", True),
    ],
)
def test_model_rejects_sampling_params_predicate(model, rejects):
    """The predicate matches the families FORWARD (Opus ≥ 4.7, Opus ≥ 5,
    any Fable) and leaves Haiku / Sonnet / Opus ≤ 4.6 untouched, so it
    doesn't rot as newer adaptive-thinking-only models ship."""
    from providers.anthropic_cached_llm import _model_rejects_sampling_params

    assert _model_rejects_sampling_params(model) is rejects


# ──────────────────────────────────────────────────────────────────────
# Integration with the dispatcher
# ──────────────────────────────────────────────────────────────────────


def _wipe_route_env(monkeypatch) -> None:
    """Strip per-route override env vars (mirror of the helper in
    test_llm_dispatcher_build)."""
    for var in (
        "JARVIS_BANTER_MODEL",
        "JARVIS_TASK_MODEL",
        "JARVIS_REASONING_MODEL",
        "JARVIS_EMOTIONAL_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_dispatcher_uses_cached_wrapper_class(monkeypatch):
    """``build_dispatching_llm`` must construct the new
    ``AnthropicCachedLLM`` subclass for Anthropic primaries, not the
    bare ``lk_anthropic.LLM``. Catches a regression where the wrapper
    gets bypassed (e.g. an accidental refactor that drops the import)."""
    _wipe_route_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

    from providers.anthropic_cached_llm import AnthropicCachedLLM
    from providers.llm import build_dispatching_llm
    from livekit.agents.llm import FallbackAdapter

    d = build_dispatching_llm()
    for route in ("BANTER", "TASK", "REASONING", "EMOTIONAL"):
        inner = d.pick(route)
        # Each route is wrapped in a FallbackAdapter; rung 1 is the
        # Anthropic primary.
        assert isinstance(inner, FallbackAdapter)
        rungs = (
            getattr(inner, "_llm_instances", None)
            or getattr(inner, "_llms", None)
            or []
        )
        assert rungs, f"route {route} has no rungs"
        # Rung 1 is our wrapper subclass.
        assert isinstance(rungs[0], AnthropicCachedLLM), (
            f"route {route} rung 1 expected AnthropicCachedLLM, "
            f"got {type(rungs[0]).__name__}"
        )


def test_apply_stable_prefix_recursively_walks_dispatcher(monkeypatch):
    """``apply_stable_prefix_recursively`` must walk the full
    DispatchingLLM → FallbackAdapter → LLM tree and call
    ``set_stable_prefix`` on every wrapper that has it."""
    _wipe_route_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

    from providers.llm import build_dispatching_llm
    from providers.prompt_cache import apply_stable_prefix_recursively

    d = build_dispatching_llm()
    stable = "STABLE PREFIX " * 100

    n = apply_stable_prefix_recursively(d, stable)
    # 2026-05-24: 4→8 route expansion (Task 4 of pre-TTS confab gate).
    # Anthropic primaries: BANTER + TASK (legacy) + REASONING + EMOTIONAL
    # + TASK_DESKTOP + TASK_BROWSER + TASK_FILES + TASK_OTHER = 8.
    # TASK_CODE's primary is DeepSeek (no AnthropicCachedLLM wrapper).
    # (Groq + DeepSeek rungs don't expose set_stable_prefix, so they
    # silently skip — they auto-cache on prefix-match anyway.)
    assert n == 8, f"expected 8 wrappers updated, got {n}"

    # And every Anthropic primary now holds the prefix.
    for route in ("BANTER", "TASK", "REASONING", "EMOTIONAL",
                  "TASK_DESKTOP", "TASK_BROWSER", "TASK_FILES", "TASK_OTHER"):
        inner = d.pick(route)
        rungs = (
            getattr(inner, "_llm_instances", None)
            or getattr(inner, "_llms", None)
            or []
        )
        primary = rungs[0]
        assert primary._stable_prefix == stable
