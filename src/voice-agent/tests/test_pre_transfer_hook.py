"""Tests for HandoffSubagent.pre_transfer (added 2026-05-11 evening).

The hook is the structural fix for the recurring "supervisor LLM
called transfer_to_screen_share without first calling
set_screen_share" failure. Verifies:

  1. When the hook returns None, the transfer proceeds normally.
  2. When the hook returns an abort string, the transfer is short-
     circuited: control stays on the supervisor and the abort string
     is returned as the tool_result.
  3. When the hook raises, the exception is caught and converted to a
     descriptive abort — a buggy hook must NOT crash the handoff
     machinery (live-failure mode the gate is here to prevent).
  4. When the hook is absent (None spec field, the default), behavior
     is unchanged from the pre-hook baseline.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key-for-init")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_spec(*, pre_transfer=None, name="testsub"):
    from subagents.registry import HandoffSubagent
    return HandoffSubagent(
        name=name,
        transfer_tool=f"transfer_to_{name}",
        when_to_use="test",
        instructions="test",
        tool_factory=lambda: [],
        ack_phrase="ok",
        max_history_items=4,
        enabled=True,
        pre_transfer=pre_transfer,
    )


def _make_context(supervisor):
    """Build a minimal RunContext-shaped MagicMock that build_transfer_tool
    only touches via `.session.current_agent`. The supervisor stub provides
    a `chat_ctx.copy()` that returns a truncatable stub."""
    truncate_result = MagicMock()
    chat_ctx_copy = MagicMock()
    chat_ctx_copy.truncate.return_value = truncate_result
    supervisor.chat_ctx.copy.return_value = chat_ctx_copy

    session = MagicMock()
    session.current_agent = supervisor
    ctx = MagicMock()
    ctx.session = session
    return ctx


# ── Happy path: hook returns None → transfer proceeds ──────────────


def test_pre_transfer_returning_none_proceeds():
    """When the pre_transfer hook returns None, the build_transfer_tool
    closure must construct the subagent (next_agent != supervisor)."""
    hook_calls = []

    async def hook(context, request, supervisor):
        hook_calls.append((request,))
        return None

    spec = _make_spec(pre_transfer=hook)

    from subagents.agent import build_transfer_tool
    tool = build_transfer_tool(spec)

    # function_tool wraps the underlying coroutine; the wrapped fn is
    # accessible via the descriptor's `__wrapped__` attribute. Falls
    # back to scanning attributes if the LiveKit version names it
    # differently.
    underlying = getattr(tool, "__wrapped__", None)
    if underlying is None:
        # livekit-agents stores the original on `.fnc` on some versions
        underlying = getattr(tool, "fnc", None) or getattr(tool, "callable", None)
    assert underlying is not None, (
        f"could not unwrap function_tool decorator (got {type(tool)!r}); "
        f"update _unwrap helper"
    )

    supervisor = MagicMock(name="supervisor")
    ctx = _make_context(supervisor)

    next_agent, ack = _run(underlying(ctx, "hello"))

    assert hook_calls == [("hello",)], "hook must have been awaited once"
    # The subagent constructor returns a RegistrySubagent which is NOT
    # the supervisor mock. So next_agent should be a fresh instance.
    assert next_agent is not supervisor, "transfer should have proceeded"
    assert ack == spec.ack_phrase


# ── Abort path: hook returns a string → transfer is cancelled ──────


def test_pre_transfer_returning_string_aborts():
    """A non-None hook return value aborts the transfer: control stays
    on the supervisor and the string is delivered as tool_result."""
    async def hook(context, request, supervisor):
        return "(screen-share unreachable on :8767)"

    spec = _make_spec(pre_transfer=hook)

    from subagents.agent import build_transfer_tool
    tool = build_transfer_tool(spec)
    underlying = getattr(tool, "__wrapped__", None) or tool.fnc

    supervisor = MagicMock(name="supervisor")
    ctx = _make_context(supervisor)

    next_agent, msg = _run(underlying(ctx, "share my screen"))

    assert next_agent is supervisor, (
        "abort must keep control on the supervisor, not construct a subagent"
    )
    assert "screen-share unreachable" in msg


# ── Exception path: hook crashes → caught and converted to abort ───


def test_pre_transfer_raising_is_caught():
    """A buggy hook must not bubble its exception up into the handoff
    machinery — it gets caught and converted to an abort string."""
    async def hook(context, request, supervisor):
        raise RuntimeError("kaboom")

    spec = _make_spec(pre_transfer=hook)

    from subagents.agent import build_transfer_tool
    tool = build_transfer_tool(spec)
    underlying = getattr(tool, "__wrapped__", None) or tool.fnc

    supervisor = MagicMock(name="supervisor")
    ctx = _make_context(supervisor)

    next_agent, msg = _run(underlying(ctx, "go"))

    assert next_agent is supervisor, "exception path must abort the transfer"
    assert "RuntimeError" in msg
    assert "kaboom" in msg


# ── No hook: pre-existing behavior is unchanged ────────────────────


def test_no_pre_transfer_field_proceeds_normally():
    """Specs without a pre_transfer hook (the default) must behave
    exactly as before — no regression on the desktop/browser path."""
    spec = _make_spec(pre_transfer=None)
    assert spec.pre_transfer is None

    from subagents.agent import build_transfer_tool
    tool = build_transfer_tool(spec)
    underlying = getattr(tool, "__wrapped__", None) or tool.fnc

    supervisor = MagicMock(name="supervisor")
    ctx = _make_context(supervisor)

    next_agent, ack = _run(underlying(ctx, "do the thing"))

    assert next_agent is not supervisor
    assert ack == spec.ack_phrase
