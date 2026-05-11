"""Bailout-regex + retry-ceiling tests added 2026-05-08.

Live-captured failure mode: desktop specialist looped 3× "Browser
opened, sir." (each refused, no tool fired) — 9s of silence to user.
Two fixes:

  1. _BAILOUT_SUMMARY_RE accepts environmental-gate phrasings
     (e.g. "Google Chrome isn't available", "extension not connected")
     so an environmentally-blocked specialist can hand back gracefully.

  2. After _NO_TOOL_RETRY_CEILING consecutive REFUSES on a single
     handoff, the gate force-allows task_done with a generic bailout
     summary so the user isn't trapped in silence.

Both fixes preserve the original property: a confab like
"Done, sir, opened a tab" still gets refused on first-call.
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


def _make_specialist():
    from livekit.agents.llm import ChatContext, ChatMessage
    from subagents.agent import RegistrySubagent
    from subagents.registry import HandoffSubagent

    spec = HandoffSubagent(
        name="desktop",
        transfer_tool="transfer_to_desktop",
        when_to_use="x",
        instructions="x",
        tool_factory=lambda: [],
        ack_phrase="ok",
        max_history_items=4,
        enabled=True,
    )
    supervisor = MagicMock()
    specialist = RegistrySubagent(spec=spec, supervisor=supervisor)
    specialist._handoff_start_idx = 1
    # Single pre-handoff item so handoff-window starts at index 1.
    specialist._chat_ctx = ChatContext(items=[
        ChatMessage(role="user", content=["pre-handoff"]),
    ])
    return specialist, supervisor


# ── Bailout phrases — environmental gates ────────────────────────────


@pytest.mark.parametrize("summary", [
    # Existing allowlist (regression)
    "user changed topic",
    "not a desktop task",
    "wrong specialist",
    "cannot accomplish — handing back to supervisor",
    "needs the browser specialist",
    # New 2026-05-08 environmental phrasings
    "Google Chrome isn't available, sir.",
    "extension not connected, sir.",
    "browser is not connected, sir.",
    "tool unavailable, sir.",
    "service offline.",
    "bridge disconnected.",
    "chrome unavailable.",
    # New 2026-05-09 weather specialist phrasings (review closeout)
    "I couldn't determine your location — which city did you have in mind?",
    "Weather service is not connected.",
])
def test_bailout_phrases_pass_gate(summary):
    """Phrases on the bailout allowlist must let task_done through
    when no real tool fired."""
    from livekit.agents.llm import FunctionCall
    specialist, supervisor = _make_specialist()
    specialist._chat_ctx.items.append(
        FunctionCall(call_id="c1", arguments="{}", name="task_done")
    )
    next_agent, msg = _run(specialist.task_done(MagicMock(), summary))
    assert next_agent is supervisor, (
        f"expected handoff to supervisor for bailout summary {summary!r}, "
        f"got next_agent={next_agent!r}"
    )
    assert msg == summary


# ── Confab phrases — must STILL be refused on first call ─────────────


@pytest.mark.parametrize("summary", [
    "Browser opened, sir.",
    "Done, sir.",
    "Opened a new tab, sir.",
    "Page loaded successfully.",
    "Searching for feminine women clothing trends.",
    "Page screenshot obtained.",
    "Browser opened on the second attempt, sir.",
])
def test_confab_phrases_refused_on_first_call(summary):
    """Confab summaries (no tool fired AND not an allowed bailout)
    must be refused, keeping the agent on the specialist."""
    from livekit.agents.llm import FunctionCall
    specialist, supervisor = _make_specialist()
    specialist._chat_ctx.items.append(
        FunctionCall(call_id="c1", arguments="{}", name="task_done")
    )
    next_agent, msg = _run(specialist.task_done(MagicMock(), summary))
    assert next_agent is specialist, (
        f"confab summary {summary!r} should stay on specialist; "
        f"got {next_agent!r}"
    )
    assert "REFUSED" in msg
    assert specialist._no_tool_refusals == 1


# ── Retry ceiling — force-bailout after N refusals ───────────────────


def test_retry_ceiling_force_bailout_after_three_refusals(monkeypatch):
    """After 3 consecutive REFUSES on a single handoff, the gate
    must force-allow task_done with a generic bailout summary so the
    user isn't trapped in silence."""
    from livekit.agents.llm import FunctionCall
    # Ensure default ceiling = 3 (override env in case CI sets it).
    # No reload needed: ceiling is read at RUNTIME inside task_done.
    monkeypatch.setenv("JARVIS_SPECIALIST_NO_TOOL_RETRY_CEILING", "3")

    specialist, supervisor = _make_specialist()

    confab = "Browser opened, sir."

    # Refusal 1 — stays on specialist
    specialist._chat_ctx.items.append(
        FunctionCall(call_id="c1", arguments="{}", name="task_done")
    )
    next_agent_1, msg_1 = _run(specialist.task_done(MagicMock(), confab))
    assert next_agent_1 is specialist
    assert "REFUSED" in msg_1
    assert specialist._no_tool_refusals == 1

    # Refusal 2 — still on specialist
    specialist._chat_ctx.items.append(
        FunctionCall(call_id="c2", arguments="{}", name="task_done")
    )
    next_agent_2, msg_2 = _run(specialist.task_done(MagicMock(), confab))
    assert next_agent_2 is specialist
    assert "REFUSED" in msg_2
    assert specialist._no_tool_refusals == 2

    # Refusal 3 — force-bailed; transitions to supervisor
    specialist._chat_ctx.items.append(
        FunctionCall(call_id="c3", arguments="{}", name="task_done")
    )
    next_agent_3, msg_3 = _run(specialist.task_done(MagicMock(), confab))
    assert next_agent_3 is supervisor, (
        "after retry ceiling, gate must force-allow handoff to supervisor"
    )
    # The summary the supervisor sees is the safe generic, not the confab.
    assert "handing back to supervisor" in msg_3.lower() or \
           "cannot accomplish" in msg_3.lower()


def test_retry_counter_resets_on_new_handoff():
    """When a specialist re-enters (new handoff via on_enter), the
    refusal counter must reset so the new handoff gets a fresh shot."""
    from livekit.agents.llm import FunctionCall
    specialist, _ = _make_specialist()
    specialist._chat_ctx.items.append(
        FunctionCall(call_id="c1", arguments="{}", name="task_done")
    )
    _run(specialist.task_done(MagicMock(), "Browser opened, sir."))
    assert specialist._no_tool_refusals == 1
    _run(specialist.on_enter())
    assert specialist._no_tool_refusals == 0


def test_retry_ceiling_runtime_env_read(monkeypatch):
    """Ceiling MUST be read from os.environ at runtime, not cached at
    module-import time. Prove it by changing the env-var BETWEEN
    refusal 1 and refusal 2, and asserting refusal 2 follows the NEW
    ceiling. With a cached constant, this test fails — refusal 2 would
    still be REFUSED under the import-time value of 3."""
    from livekit.agents.llm import FunctionCall

    # Start under ceiling=3: refusal 1 is REFUSED (1+1 < 3).
    monkeypatch.setenv("JARVIS_SPECIALIST_NO_TOOL_RETRY_CEILING", "3")
    specialist, supervisor = _make_specialist()
    confab = "Browser opened, sir."

    specialist._chat_ctx.items.append(
        FunctionCall(call_id="c1", arguments="{}", name="task_done")
    )
    next_agent_1, msg_1 = _run(specialist.task_done(MagicMock(), confab))
    assert next_agent_1 is specialist
    assert "REFUSED" in msg_1
    assert specialist._no_tool_refusals == 1

    # Operator drops ceiling to 2 mid-handoff (live edit).
    # Cached constant → refusal 2 still REFUSED. Runtime read →
    # 1+1 >= 2 fires force-bail and transitions to supervisor.
    monkeypatch.setenv("JARVIS_SPECIALIST_NO_TOOL_RETRY_CEILING", "2")
    specialist._chat_ctx.items.append(
        FunctionCall(call_id="c2", arguments="{}", name="task_done")
    )
    next_agent_2, msg_2 = _run(specialist.task_done(MagicMock(), confab))
    assert next_agent_2 is supervisor, (
        "runtime env change must take effect on next task_done; "
        f"got next_agent={next_agent_2!r} (expected supervisor)"
    )
    assert (
        "handing back to supervisor" in msg_2.lower()
        or "cannot accomplish" in msg_2.lower()
    )
