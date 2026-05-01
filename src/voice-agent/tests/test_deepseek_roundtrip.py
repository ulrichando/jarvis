"""Tests for deepseek_roundtrip — the patches that round-trip DeepSeek's
reasoning_content field through livekit-plugins-openai.

The integration path is exercised live (the agent runs the patched
flow against the real DeepSeek API). Here we cover the pure-function
parts and verify the patches install idempotently and produce the
expected outputs when fed synthetic chat-context inputs.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import deepseek_roundtrip


@pytest.fixture(autouse=True)
def _clean_sidecars():
    """Each test starts with empty sidecars."""
    deepseek_roundtrip._REASONING_BY_CALL_ID.clear()
    deepseek_roundtrip._STREAMING_STATE.clear()
    yield
    deepseek_roundtrip._REASONING_BY_CALL_ID.clear()
    deepseek_roundtrip._STREAMING_STATE.clear()


# ── install() idempotency ─────────────────────────────────────────────


def test_install_is_idempotent():
    """Calling install() multiple times must not double-wrap the patches."""
    deepseek_roundtrip.install()
    deepseek_roundtrip.install()
    deepseek_roundtrip.install()
    from livekit.agents.inference import llm as inf_llm
    from livekit.agents.llm._provider_format import openai as oai_fmt
    assert getattr(inf_llm.LLMStream, "_jarvis_deepseek_patched", False) is True
    assert getattr(oai_fmt, "_jarvis_deepseek_patched", False) is True


def test_cache_size_reflects_sidecar():
    """Diagnostic helper returns the call-id sidecar size."""
    assert deepseek_roundtrip.cache_size() == 0
    deepseek_roundtrip._REASONING_BY_CALL_ID["call_a"] = "thinking..."
    deepseek_roundtrip._REASONING_BY_CALL_ID["call_b"] = "more thinking"
    assert deepseek_roundtrip.cache_size() == 2


# ── to_chat_ctx injection path ────────────────────────────────────────


def _build_synth_chat_ctx_with_assistant_tool_call(call_id: str = "call_xyz"):
    """Build a minimal ChatContext with one user msg, one assistant
    tool_call PAIRED with its tool output (the provider-format
    converter drops orphan tool_calls). Used to drive the patched
    to_chat_ctx through its injection path."""
    from livekit.agents import llm as agents_llm
    ctx = agents_llm.ChatContext()
    ctx.add_message(role="user", content="open chrome")
    ctx.items.append(agents_llm.FunctionCall(
        call_id=call_id,
        name="open_app",
        arguments='{"name":"chrome"}',
    ))
    ctx.items.append(agents_llm.FunctionCallOutput(
        call_id=call_id,
        name="open_app",
        output="opened",
        is_error=False,
    ))
    return ctx


def test_to_chat_ctx_injects_real_reasoning_when_cached():
    """When _REASONING_BY_CALL_ID has an entry for the message's first
    tool_call.id, that string lands as `reasoning_content` on the
    serialized assistant message."""
    deepseek_roundtrip.install()
    deepseek_roundtrip._REASONING_BY_CALL_ID["call_real"] = "real captured reasoning"

    ctx = _build_synth_chat_ctx_with_assistant_tool_call(call_id="call_real")
    messages, _ = ctx.to_provider_format(format="openai")
    assistant = next(m for m in messages if m.get("role") == "assistant")
    assert assistant.get("reasoning_content") == "real captured reasoning"


def test_to_chat_ctx_injects_placeholder_when_uncached():
    """Tool-call messages whose call_id isn't in the cache (e.g.
    recalled from the conversations DB) get the placeholder. Without
    this, DeepSeek thinking-mode rejects the request with HTTP 400."""
    deepseek_roundtrip.install()
    # No entry for 'call_unknown' in the sidecar.
    ctx = _build_synth_chat_ctx_with_assistant_tool_call(call_id="call_unknown")
    messages, _ = ctx.to_provider_format(format="openai")
    assistant = next(m for m in messages if m.get("role") == "assistant")
    assert assistant.get("reasoning_content") == deepseek_roundtrip._PLACEHOLDER_REASONING


def test_to_chat_ctx_no_inject_on_text_only_assistant():
    """Assistant messages without tool_calls should NOT receive
    reasoning_content — DeepSeek only requires it on tool-call turns,
    and adding it elsewhere is wasted bytes."""
    deepseek_roundtrip.install()
    from livekit.agents import llm as agents_llm
    ctx = agents_llm.ChatContext()
    ctx.add_message(role="user", content="how are you")
    ctx.add_message(role="assistant", content="I'm well, thanks.")
    messages, _ = ctx.to_provider_format(format="openai")
    assistant = next(m for m in messages if m.get("role") == "assistant")
    assert "reasoning_content" not in assistant


def test_to_chat_ctx_uses_first_tool_call_id_as_lookup():
    """Multi-tool-call assistant messages key off the FIRST call_id."""
    deepseek_roundtrip.install()
    deepseek_roundtrip._REASONING_BY_CALL_ID["call_first"] = "for first"

    from livekit.agents import llm as agents_llm
    ctx = agents_llm.ChatContext()
    ctx.add_message(role="user", content="do two things")
    # Two tool_calls + their outputs (orphans get pruned).
    for cid, fname in (("call_first", "thing_a"), ("call_second", "thing_b")):
        ctx.items.append(agents_llm.FunctionCall(
            call_id=cid, name=fname, arguments="{}"))
        ctx.items.append(agents_llm.FunctionCallOutput(
            call_id=cid, name=fname, output="ok", is_error=False))
    messages, _ = ctx.to_provider_format(format="openai")
    # Each function_call produces one assistant tool_call message.
    assistants_with_tool = [
        m for m in messages
        if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    assert len(assistants_with_tool) >= 1
    # First assistant msg should have real reasoning. Others get placeholder
    # (since 'call_second' isn't in the cache).
    assert assistants_with_tool[0].get("reasoning_content") == "for first"


# ── _parse_choice capture path (synthetic chunk) ─────────────────────


def test_parse_choice_captures_reasoning_keyed_by_tool_call_id():
    """When the original _parse_choice runs over a chunk that has
    BOTH delta.reasoning_content AND delta.tool_calls (with id), the
    patched wrapper stores the reasoning under that id."""
    from livekit.agents.inference import llm as inf_llm

    deepseek_roundtrip.install()

    # Reach the patched function. Build a synthetic stream where:
    #   chunk 1: tool_call with id='call_synth' + reasoning_content="thinking part 1"
    #   chunk 2: more reasoning_content "thinking part 2"
    #   chunk 3: finish_reason='tool_calls' (triggers finalization)
    response_id = "resp_test_1"

    # Each chunk has one Choice with a delta. We mimic the openai SDK shape.
    def _make_choice(*, content=None, reasoning=None, tool_calls=None, finish=None):
        delta = SimpleNamespace(
            content=content,
            tool_calls=tool_calls,
            reasoning_content=reasoning,
        )
        return SimpleNamespace(delta=delta, finish_reason=finish)

    def _tool_call(*, id=None, name=None, args=None, index=0):
        fn = SimpleNamespace(name=name, arguments=args)
        return SimpleNamespace(id=id, index=index, function=fn,
                               extra_content=None)

    # Mock self with the minimum that _parse_choice's existing logic needs.
    # The original _parse_choice mutates self._tool_call_id, self._fnc_name,
    # etc. — they need to exist as None initially.
    self_mock = SimpleNamespace(
        _tool_call_id=None,
        _fnc_name=None,
        _fnc_raw_arguments=None,
        _tool_extra=None,
        _tool_index=None,
    )
    import threading
    thinking = threading.Event()  # asyncio.Event accepts is_set, threading does too

    # Drive three chunks through the patched function.
    inf_llm.LLMStream._parse_choice(
        self_mock, response_id,
        _make_choice(reasoning="thinking part 1",
                     tool_calls=[_tool_call(id="call_synth", name="open_app")]),
        thinking,
    )
    inf_llm.LLMStream._parse_choice(
        self_mock, response_id,
        _make_choice(reasoning=" part 2",
                     tool_calls=[_tool_call(id=None, args='{"name":"chrome"}')]),
        thinking,
    )
    inf_llm.LLMStream._parse_choice(
        self_mock, response_id,
        _make_choice(finish="tool_calls"),
        thinking,
    )

    # On finalization (finish_reason='tool_calls'), the captured
    # reasoning is committed to _REASONING_BY_CALL_ID under the
    # observed call_id.
    assert "call_synth" in deepseek_roundtrip._REASONING_BY_CALL_ID
    assert deepseek_roundtrip._REASONING_BY_CALL_ID["call_synth"] == \
        "thinking part 1 part 2"


def test_parse_choice_skips_when_no_reasoning_emitted():
    """A non-DeepSeek provider (Groq, OpenAI proper) doesn't emit
    delta.reasoning_content. The patch must not add anything to the
    sidecar in that case."""
    from livekit.agents.inference import llm as inf_llm
    deepseek_roundtrip.install()

    response_id = "resp_no_reasoning"

    def _make_choice(**kw):
        delta = SimpleNamespace(
            content=kw.get("content"),
            tool_calls=kw.get("tool_calls"),
            # No reasoning_content attribute at all (older SDKs may
            # produce a Pydantic object without the field set).
        )
        return SimpleNamespace(delta=delta, finish_reason=kw.get("finish"))

    self_mock = SimpleNamespace(
        _tool_call_id=None, _fnc_name=None, _fnc_raw_arguments=None,
        _tool_extra=None, _tool_index=None,
    )
    import threading
    thinking = threading.Event()

    # Stream of plain text + finish, no reasoning.
    inf_llm.LLMStream._parse_choice(
        self_mock, response_id,
        _make_choice(content="hi there"),
        thinking,
    )
    inf_llm.LLMStream._parse_choice(
        self_mock, response_id,
        _make_choice(finish="stop"),
        thinking,
    )

    assert deepseek_roundtrip.cache_size() == 0
