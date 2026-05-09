"""LangGraph supervisor guards (fix F+G in the 2026-05-08 audit).

  F. Recursion limit — the supervisor graph hit `Recursion limit of
     10007` live on 2026-05-08T16:16:39 because someone bumped the
     default 25 to a huge value AND the speak_gate↔tool_node loop has
     an unresolvable case (tool_node is currently a no-op for direct
     tools). Cap at 25 so the worker fails fast instead of burning
     5,000 cycles before crashing.

  G. Sanitizer bypass — `JarvisSupervisorGraphLLM._build_chunks`
     constructs ChatChunks directly from LangGraph AIMessage content
     and skips the production sanitizer chain (pycall / dsml /
     tool_name) which monkey-patches the OpenAI/Groq plugin's
     `_parse_choice`. Live 2026-05-08 16:14-18 voiced
     `ext_navigate("https://entropic.com")` 8 times. Patch the
     adapter to call sanitize_text_for_tts on outbound content.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key-for-init")


# ── F: recursion limit ───────────────────────────────────────────────


def test_graph_recursion_limit_constant_is_capped():
    """The supervisor adapter must cap recursion at 25 (LangGraph's
    safe default). Anything > 50 invites the 5,000-cycle meltdown."""
    from supervisor_graph.llm_adapter import _GRAPH_RECURSION_LIMIT
    assert _GRAPH_RECURSION_LIMIT <= 50, (
        f"_GRAPH_RECURSION_LIMIT={_GRAPH_RECURSION_LIMIT} too high; "
        "live failure 2026-05-08 hit 10007 before crash"
    )


def test_invoke_passes_recursion_limit_through_config(monkeypatch):
    """When `_build_chunks` runs the graph, it MUST pass
    `recursion_limit` in the invoke config so a stuck graph trips
    GraphRecursionError after 25 cycles instead of running forever."""
    from langchain_core.messages import HumanMessage
    from livekit.agents.llm import ChatContext
    from supervisor_graph import llm_adapter

    captured_config = {}

    class FakeGraph:
        def invoke(self, state, config=None):
            captured_config["config"] = config
            from langchain_core.messages import AIMessage
            return {"messages": [AIMessage(content="ok")]}

    stream = llm_adapter._GraphLLMStream(
        chat_ctx=ChatContext(items=[]),
        graph=FakeGraph(),
    )
    # Manually drive the build path (no asyncio dance needed).
    stream._build_chunks()

    assert captured_config["config"] is not None, (
        "recursion_limit not passed to graph.invoke"
    )
    assert captured_config["config"].get("recursion_limit") == \
        llm_adapter._GRAPH_RECURSION_LIMIT


# ── G: sanitizer applied to graph output ─────────────────────────────


def test_sanitizer_strips_pycall_leak_in_graph_output():
    """Adapter must run pycall sanitizer on AIMessage.content so a
    tool-call-as-text leak from the graph's LLM doesn't hit TTS."""
    from langchain_core.messages import AIMessage
    from supervisor_graph.llm_adapter import _ai_messages_to_chunks

    leak = AIMessage(content='ext_navigate("https://entropic.com")')
    chunks = _ai_messages_to_chunks([leak])
    # All TTS-bound content chunks must be empty (suppressed).
    for ch in chunks:
        content = getattr(ch.delta, "content", None)
        assert not content, (
            f"sanitizer failed to suppress pycall leak in graph "
            f"output: {content!r}"
        )


def test_sanitizer_strips_xml_function_leak():
    """`<function>ext_screenshot</function>` and similar XML envelopes
    must also be suppressed."""
    from langchain_core.messages import AIMessage
    from supervisor_graph.llm_adapter import _ai_messages_to_chunks

    leak = AIMessage(content="<function>ext_screenshot</function>")
    chunks = _ai_messages_to_chunks([leak])
    for ch in chunks:
        content = getattr(ch.delta, "content", None)
        assert not content, (
            f"sanitizer failed to suppress XML leak: {content!r}"
        )


def test_sanitizer_strips_xml_attr_leak():
    """`<function=task_done>` shape — captured live 2026-05-05."""
    from langchain_core.messages import AIMessage
    from supervisor_graph.llm_adapter import _ai_messages_to_chunks

    leak = AIMessage(content='<function=task_done>{"summary": "x"}')
    chunks = _ai_messages_to_chunks([leak])
    for ch in chunks:
        content = getattr(ch.delta, "content", None)
        assert not content, (
            f"sanitizer failed to suppress XML-attr leak: {content!r}"
        )


def test_sanitizer_passes_normal_content_unchanged():
    """A normal voice reply must NOT be touched by the sanitizer."""
    from langchain_core.messages import AIMessage
    from supervisor_graph.llm_adapter import _ai_messages_to_chunks

    msg = AIMessage(content="Right away. Browser is open.")
    chunks = _ai_messages_to_chunks([msg])
    found = False
    for ch in chunks:
        content = getattr(ch.delta, "content", None)
        if content and "Browser is open" in content:
            found = True
    assert found, (
        "normal content stripped by sanitizer — false-positive in regex"
    )
