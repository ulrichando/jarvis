"""Reasoning models (qwen3-32b on REASONING route, DeepSeek-R1, gpt-oss
"show-thinking" mode) emit chain-of-thought wrapped in <think>…</think>
tags as part of the regular content. Without stripping, TTS speaks the
entire trace before the actual reply — the user hears JARVIS narrate
his own thinking, which was the live-observed bug 2026-05-04.

These tests pin the strip behaviour. A regression here re-opens that bug.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import os
os.environ.setdefault("GROQ_API_KEY", "test-key")


@pytest.mark.parametrize("text,expected", [
    ("<think>internal reasoning</think>The answer is 42.", "The answer is 42."),
    ("<thinking>foo</thinking>bar", "bar"),
    ("<think>multi\nline\nreasoning</think>final reply", "final reply"),
    ("clean text no tags", "clean text no tags"),
    ("<THINK>uppercase tag</THINK>after", "after"),
    ("text <think>middle</think> more text", "text more text"),
    ("<reasoning>some chain of thought</reasoning>output", "output"),
    # Multiple blocks — no spaces were in the input around the tags,
    # so none in the output either.
    ("<think>a</think>between<think>b</think>after",
     "betweenafter"),
    # Whitespace cleanup after removal
    ("Reply.  <think>x</think>  More.", "Reply. More."),
    # Empty reasoning block
    ("<think></think>just the reply", "just the reply"),
])
def test_strip_reasoning_traces(text, expected):
    from supervisor_graph.llm_adapter import _strip_reasoning_traces
    assert _strip_reasoning_traces(text) == expected


def test_ai_messages_to_chunks_strips_reasoning():
    """End-to-end: an AIMessage carrying <think>...</think> must yield
    a ChatChunk whose content has the reasoning removed. Otherwise TTS
    speaks the whole thing."""
    from supervisor_graph.llm_adapter import _ai_messages_to_chunks
    from langchain_core.messages import AIMessage

    m = AIMessage(content=(
        "<think>The user wants me to explain recursion. Recursion is "
        "when a function calls itself. Let me think about base cases."
        "</think>Recursion is a function calling itself, sir."
    ))
    chunks = _ai_messages_to_chunks([m])
    contents = [c.delta.content for c in chunks if c.delta and c.delta.content]
    joined = " ".join(contents)
    assert "Recursion is a function calling itself" in joined
    assert "<think>" not in joined
    assert "base cases" not in joined


def test_strip_handles_none_and_empty():
    """Defensive: None / empty / whitespace-only must return cleanly."""
    from supervisor_graph.llm_adapter import _strip_reasoning_traces
    assert _strip_reasoning_traces("") == ""
    assert _strip_reasoning_traces(None) is None or _strip_reasoning_traces(None) == ""
    assert _strip_reasoning_traces("   ") == ""
