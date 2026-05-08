"""Token-aware chat_ctx pruning (fix B in the 2026-05-08 audit).

Live-captured failure: pre-flight at 2026-05-08T17:51:52 reported
`est_tokens=293321 max=128000` (2.3× the model's window). Groq silently
truncated the head, removing JARVIS_INSTRUCTIONS, and the supervisor
LLM degenerated into hallucinating `delegate(role='summarize', ...)`
for every utterance.

Fix: `_prune_chat_ctx_for_budget` drops oldest non-system items
(in tool-call-pair-aware fashion) until the estimate fits. This module
tests the helper in isolation.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key-for-init")


def test_empty_ctx_returned_unchanged():
    from livekit.agents.llm import ChatContext
    from jarvis_agent import _prune_chat_ctx_for_budget
    ctx = ChatContext.empty()
    assert _prune_chat_ctx_for_budget(ctx, 1000) is ctx


def test_under_budget_returned_unchanged():
    from livekit.agents.llm import ChatContext, ChatMessage
    from jarvis_agent import _prune_chat_ctx_for_budget
    ctx = ChatContext(items=[
        ChatMessage(role="user", content=["hi"]),
        ChatMessage(role="assistant", content=["hello"]),
    ])
    pruned = _prune_chat_ctx_for_budget(ctx, target_tokens=10_000)
    assert pruned is ctx, "no pruning needed; original returned"


def test_oldest_dropped_until_under_budget():
    """When estimate exceeds target, oldest non-system items drop first."""
    from livekit.agents.llm import ChatContext, ChatMessage
    from jarvis_agent import (
        _prune_chat_ctx_for_budget,
        _ctx_items_token_estimate,
    )
    # 20 turns of ~100-char content = ~25 tokens each = 500 total.
    items = []
    for i in range(20):
        items.append(ChatMessage(
            role="user" if i % 2 == 0 else "assistant",
            content=["x" * 100 + f" turn {i}"],
        ))
    ctx = ChatContext(items=items)
    initial = _ctx_items_token_estimate(items)
    assert initial > 100, f"setup expected >100 tokens, got {initial}"

    # Tight target: should drop most items.
    pruned = _prune_chat_ctx_for_budget(ctx, target_tokens=100)
    assert _ctx_items_token_estimate(pruned.items) <= 100
    # Most-recent items survive (that's the point of dropping oldest).
    last_content = str(pruned.items[-1].content)
    assert "turn 19" in last_content, (
        f"expected last item to be turn 19, got: {last_content}"
    )


def test_system_messages_always_kept():
    """The system message (the JARVIS_INSTRUCTIONS preamble) must
    never be dropped — that's the failure mode this fix exists to
    prevent."""
    from livekit.agents.llm import ChatContext, ChatMessage
    from jarvis_agent import _prune_chat_ctx_for_budget

    sys_message = ChatMessage(role="system", content=["S" * 400])
    items = [sys_message]
    for i in range(15):
        items.append(ChatMessage(role="user", content=["x" * 200]))
    ctx = ChatContext(items=items)

    # Tight target — still keeps system message.
    pruned = _prune_chat_ctx_for_budget(ctx, target_tokens=120)
    assert any(
        getattr(it, "role", None) == "system" for it in pruned.items
    ), "system message dropped — that's the bug, not the fix"


def test_tool_call_and_output_dropped_together():
    """When a FunctionCall is dropped, its paired FunctionCallOutput
    must drop too (and vice versa). API rejects orphan tool messages."""
    from livekit.agents.llm import (
        ChatContext, ChatMessage, FunctionCall, FunctionCallOutput,
    )
    from jarvis_agent import _prune_chat_ctx_for_budget

    items = [
        ChatMessage(role="system", content=["sys"]),
        ChatMessage(role="user", content=["x" * 600]),
        FunctionCall(call_id="cid1", arguments="{}", name="ext_navigate"),
        FunctionCallOutput(
            call_id="cid1", output="x" * 600, is_error=False,
        ),
        ChatMessage(role="user", content=["y" * 200]),
    ]
    ctx = ChatContext(items=items)

    # Target small enough that the tool pair must drop.
    pruned = _prune_chat_ctx_for_budget(ctx, target_tokens=80)

    pruned_call_ids = [
        getattr(it, "call_id", None) for it in pruned.items
    ]
    n_calls = sum(
        1 for it in pruned.items
        if hasattr(it, "name") and getattr(it, "type", None) == "function_call"
    )
    n_outputs = sum(
        1 for it in pruned.items
        if getattr(it, "type", None) == "function_call_output"
    )
    assert n_calls == n_outputs, (
        f"orphan tool pair after prune: calls={n_calls} outputs={n_outputs}; "
        f"pruned items: {[type(it).__name__ for it in pruned.items]}"
    )
