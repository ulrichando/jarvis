"""T13 — L1 synthesis via session stash.

Pycall stashes parsed call info on session._jarvis_text_shape_pending.
Subagent gate drains the stash at task_done check time, calls
synthesize_and_insert into the persisted chat_ctx, then re-checks
items_since.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_pycall_stashes_call_info_on_session_when_chat_ctx_unreachable():
    """When pycall detects a leak, it stashes (tool_name, raw_args)
    on session._jarvis_text_shape_pending."""
    from livekit.agents.inference import llm as inf_llm
    import sanitizers.pycall as pycall_sanitizer
    pycall_sanitizer._PYCALL_STATE.clear()
    pycall_sanitizer.install()

    session = SimpleNamespace()
    self_mock = SimpleNamespace(
        _tool_call_id=None, _fnc_name=None, _fnc_raw_arguments=None,
        _tool_extra=None, _tool_index=None,
        _tool_ctx=SimpleNamespace(
            function_tools={"launch_app": object(), "task_done": object()}
        ),
        _event_ch=SimpleNamespace(send_nowait=lambda c: None),
        _chat_ctx=None,  # explicitly unreachable
        _session=session,
    )
    import threading
    thinking = threading.Event()

    chunks = ['launch_app("google-chrome")']
    for content in chunks:
        delta = SimpleNamespace(content=content, tool_calls=None,
                                reasoning_content=None)
        c = SimpleNamespace(delta=delta, finish_reason=None)
        inf_llm.LLMStream._parse_choice(self_mock, "resp_stash", c, thinking)

    pending = getattr(session, "_jarvis_text_shape_pending", None)
    assert pending is not None
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "launch_app"
    assert "google-chrome" in pending[0]["raw_args"]


def test_gate_drains_stash_and_synthesizes_into_chat_ctx():
    """When the subagent gate runs at task_done check time, it
    drains session._jarvis_text_shape_pending and calls
    synthesize_and_insert into the subagent's persisted chat_ctx."""
    from subagents.agent import _drain_text_shape_stash
    session = SimpleNamespace(
        _jarvis_text_shape_pending=[
            {"tool_name": "launch_app", "raw_args": "binary='google-chrome'"}
        ]
    )
    chat_ctx = SimpleNamespace(items=[])
    synthesized_count = _drain_text_shape_stash(session, chat_ctx)
    assert synthesized_count == 1
    # Pair appended to chat_ctx
    assert len(chat_ctx.items) == 2
    fc, fco = chat_ctx.items
    assert fc.call_id == fco.call_id
    assert fc.name == "launch_app"
    # Stash drained
    assert getattr(session, "_jarvis_text_shape_pending", []) == []


def test_drain_empty_stash_returns_zero():
    """When the stash is empty or missing, _drain_text_shape_stash
    returns 0 and chat_ctx is untouched."""
    from subagents.agent import _drain_text_shape_stash
    session = SimpleNamespace()
    chat_ctx = SimpleNamespace(items=[])
    assert _drain_text_shape_stash(session, chat_ctx) == 0
    assert chat_ctx.items == []


def test_drain_multiple_stashed_calls():
    """Drain handles multiple stashed text-shape calls."""
    from subagents.agent import _drain_text_shape_stash
    session = SimpleNamespace(
        _jarvis_text_shape_pending=[
            {"tool_name": "launch_app", "raw_args": "binary='a'"},
            {"tool_name": "screenshot", "raw_args": ""},
        ]
    )
    chat_ctx = SimpleNamespace(items=[])
    synthesized_count = _drain_text_shape_stash(session, chat_ctx)
    assert synthesized_count == 2
    assert len(chat_ctx.items) == 4  # 2 pairs
    assert getattr(session, "_jarvis_text_shape_pending", []) == []


def test_drain_writes_visible_to_subsequent_readonly_chat_ctx_view():
    """End-to-end: when the drain appends to the MUTABLE _chat_ctx.items,
    a subsequent read via the _ReadOnlyChatContext snapshot picks up
    the new items. This mirrors the gate's actual wiring:
      1. drain(session, self._chat_ctx)  # appends to mutable list
      2. self.chat_ctx.items[idx:]       # property → fresh snapshot
    Confirms the property's snapshot semantics don't shadow the drain."""
    from livekit.agents.llm.chat_context import ChatContext, _ReadOnlyChatContext
    from subagents.agent import _drain_text_shape_stash

    persisted = ChatContext.empty()
    session = SimpleNamespace(
        _jarvis_text_shape_pending=[
            {"tool_name": "launch_app", "raw_args": "binary='google-chrome'"},
        ]
    )
    handoff_start_idx = len(persisted.items)  # 0
    # Drain into the persisted (mutable) chat_ctx.
    n = _drain_text_shape_stash(session, persisted)
    assert n == 1
    # Now simulate the gate's read path: a fresh _ReadOnlyChatContext
    # snapshot of self._chat_ctx.items.
    ro = _ReadOnlyChatContext(persisted.items)
    since_handoff = ro.items[handoff_start_idx:]
    assert len(since_handoff) == 2  # FunctionCall + FunctionCallOutput
    from livekit.agents.llm import FunctionCall
    real_calls = [
        it for it in since_handoff
        if isinstance(it, FunctionCall) and it.name != "task_done"
    ]
    assert len(real_calls) == 1
    assert real_calls[0].name == "launch_app"
