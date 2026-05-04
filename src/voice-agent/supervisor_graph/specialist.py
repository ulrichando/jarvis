"""specialist_node — the bridge from the graph to the existing
RegistrySpecialist machinery in `specialists/agent.py`.

Three responsibilities:
  1. Emit the non-committal filler ("One moment, sir.") exactly once
     per handoff — bridges the latency gap so the user hears a voice.
     The Sierra/Hamming/Vapi pattern: never claim completion before
     the work happens.
  2. Run the named specialist to completion via _run_specialist().
     The specialist still goes through its existing task_done gate
     (added 2026-05-04) so it cannot bail out without doing work.
  3. Clear pending_specialist + pending_tool_calls regardless of
     specialist outcome (success OR failure). Never leave the graph
     in a deadlocked pending state.

The actual specialist run is in _run_specialist() so tests can swap
it without standing up a full LiveKit AgentSession.
"""
from __future__ import annotations

import logging
import random
from typing import Optional

from langchain_core.messages import AIMessage, ToolMessage

logger = logging.getLogger("supervisor_graph.specialist")

# Non-committal fillers. NEVER include past-tense success language.
# All are < 1 second to synthesize via Groq Orpheus.
_FILLERS = (
    "One moment, sir.",
    "On it.",
    "Let me check.",
    "Looking now.",
)


def _pick_filler() -> str:
    return random.choice(_FILLERS)


def _run_specialist(name: str, request: str, state: dict) -> str:
    """Invoke the named specialist and return its final summary.

    For Phase 5 of this plan, this is a thin shim over the existing
    `RegistrySpecialist` mechanism. The graph constructs the
    specialist with the current chat_ctx and runs it in-process; the
    LiveKit AgentSession dispatches its tools normally.

    NOTE: in Phase 6 (graph-as-LLM adapter, Task 13) the specialist
    invocation is replaced by an inner LangGraph subgraph that wraps
    the same RegistrySpecialist as a node. Until then this shim is
    stubbed in tests via patch.
    """
    raise NotImplementedError(
        "Wired up by graph.py + llm_adapter.py in later tasks. "
        "Tests inject this via unittest.mock.patch."
    )


def specialist_node(state: dict) -> dict:
    """Run the in-flight specialist; emit filler-once; clear pending.

    The graph routes here when `pending_specialist` is set (typically
    set by task_dispatch_node when it emits a transfer_to_X tool
    call). On entry: set the filler if not already emitted. On exit:
    pending_specialist and pending_tool_calls are guaranteed empty —
    speak_gate will release.
    """
    name = state.get("pending_specialist")
    if not name:
        # Defensive — shouldn't happen given graph wiring.
        logger.warning("[specialist] called with no pending_specialist")
        return {}

    user_query = state.get("user_query") or ""
    output_messages: list = []

    # 1. Filler-once — bridges latency without lying.
    if not state.get("handoff_filler_voiced"):
        filler = _pick_filler()
        logger.info("[specialist] filler: %r → %s", filler, name)
        output_messages.append(AIMessage(content=filler))

    # 2. Run the specialist. Catch all exceptions; never deadlock.
    try:
        summary = _run_specialist(name, user_query, state)
        if not summary:
            summary = f"({name} specialist returned no summary)"
        logger.info("[specialist] %s done: %r", name, summary[:120])
    except Exception as e:
        summary = f"The {name} specialist failed: {type(e).__name__}: {e}"
        logger.warning("[specialist] %s failed: %s", name, e)

    # 3. Append the specialist's summary as a tool result the speak
    #    path can consume. Pair with the handoff's tool_call_id so
    #    pending_tool_calls clears cleanly.
    pending = state.get("pending_tool_calls") or []
    if pending:
        # The first pending id corresponds to the handoff that started
        # this specialist. Pair them.
        output_messages.append(ToolMessage(
            content=summary, tool_call_id=pending[0],
        ))

    return {
        "messages": output_messages,
        "pending_specialist": None,
        "pending_tool_calls": [],
        "last_tool_result": summary,
        "handoff_filler_voiced": True,
    }
