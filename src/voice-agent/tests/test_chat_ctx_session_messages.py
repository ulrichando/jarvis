"""Regression tests for `pipeline.chat_ctx.session_chat_messages`.

Guards the livekit-agents 1.5 API migration that silently broke the turn
dispatcher / graph / ctx-compaction (2026-06 review finding):

  * `chat_ctx` lives on the Agent, not the AgentSession — `session.chat_ctx`
    raises AttributeError.
  * `ChatContext.messages` became a *method*, not a property — accessing it
    as an attribute yields a bound method that `reversed()`/slicing choke on.
  * `current_agent.chat_ctx` is a read-only wrapper whose list is immutable
    but whose message `.content` mutations still persist.

These tests use the REAL livekit ChatContext (not a list mock) precisely
because a list mock is what hid the original bug.
"""
from __future__ import annotations

import types

from pipeline.chat_ctx import session_chat_messages


def _real_ctx_with(*user_texts: str):
    from livekit.agents.llm import ChatContext
    ctx = ChatContext.empty()
    for t in user_texts:
        ctx.add_message(role="user", content=t)
    return ctx


def _agent_with_ctx(ctx):
    """A stand-in Agent exposing a read-only chat_ctx, like livekit's Agent."""
    from livekit.agents.voice.agent import _ReadOnlyChatContext
    return types.SimpleNamespace(chat_ctx=_ReadOnlyChatContext(ctx.items))


def test_returns_messages_from_current_agent():
    """The happy path: real ctx on current_agent → real ChatMessage list."""
    ctx = _real_ctx_with("hello", "world")
    session = types.SimpleNamespace(current_agent=_agent_with_ctx(ctx))
    msgs = session_chat_messages(session)
    assert isinstance(msgs, list)
    assert len(msgs) == 2
    # Must be ChatMessage objects, NOT a bound method or empty list.
    assert all(getattr(m, "role", None) == "user" for m in msgs)


def test_messages_method_not_attribute():
    """`.messages` is a method now — the helper must call it, so the result
    is reversible/sliceable (the exact thing the old code got wrong)."""
    ctx = _real_ctx_with("a", "b", "c")
    session = types.SimpleNamespace(current_agent=_agent_with_ctx(ctx))
    msgs = session_chat_messages(session)
    # Would raise TypeError if msgs were a bound method (the original bug).
    assert [m for m in reversed(msgs)]
    assert msgs[-1:]  # slicing works


def test_content_mutation_persists():
    """Prefix-injection contract: mutating a returned message's .content
    persists to the live context even through the read-only wrapper."""
    ctx = _real_ctx_with("open my email")
    session = types.SimpleNamespace(current_agent=_agent_with_ctx(ctx))
    msgs = session_chat_messages(session)
    msgs[-1].content = "[Route: TASK] open my email"
    # Re-read fresh and confirm the mutation stuck on the underlying ctx.
    again = session_chat_messages(session)
    content = again[-1].content
    text = content if isinstance(content, str) else content[0]
    assert text.startswith("[Route: TASK]")


def test_session_chat_ctx_attribute_error_degrades_to_empty():
    """Accessing session.chat_ctx (the original bug) must NOT leak out — the
    helper reads current_agent.chat_ctx and returns [] when unavailable."""
    # AgentSession has no chat_ctx; emulate with no current_agent.
    session = types.SimpleNamespace(current_agent=None)
    assert session_chat_messages(session) == []


def test_missing_current_agent_attr_degrades_to_empty():
    session = types.SimpleNamespace()  # no current_agent at all
    assert session_chat_messages(session) == []


def test_real_agentsession_has_no_chat_ctx():
    """Lock the root cause at the class level (instantiation-free, so no
    event-loop / test-ordering fragility): AgentSession exposes no `chat_ctx`
    descriptor anywhere in its MRO — it lives on Agent. The writable session
    ctx is `.history`."""
    from livekit.agents.voice import AgentSession
    assert all("chat_ctx" not in vars(c) for c in AgentSession.__mro__)
    assert isinstance(getattr(AgentSession, "history", None), property)
