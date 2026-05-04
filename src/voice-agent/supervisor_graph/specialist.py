"""specialist_node — graph-side handoff coordinator.

Architecture (Phase 6, 2026-05-04): the graph does NOT run the
specialist in-process. The specialist's actual work (browser
ext_*, desktop tools, etc.) runs through LiveKit AgentSession's
normal tool dispatch, kicking off the existing RegistrySpecialist
machinery (specialists/agent.py with the task_done gate). The graph's
job here is:

  1. Emit the non-committal filler ("One moment, sir.") exactly once
     so the user hears a voice during the latency.
  2. Re-emit the transfer_to_* tool_call so the LLM adapter surfaces
     it as a real ChatChunk for AgentSession to dispatch.
  3. Clear pending_specialist + pending_tool_calls so speak_gate
     releases this turn (the specialist runs in a separate turn).

On the NEXT user turn, the chat_ctx already contains the specialist's
result (as a tool_result message); the supervisor's classify routes
appropriately and life goes on.
"""
from __future__ import annotations

import logging
import random

from langchain_core.messages import AIMessage

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


def specialist_node(state: dict) -> dict:
    """Emit the filler-once and the handoff tool_call passthrough.

    Does NOT run the specialist directly. The LLM adapter surfaces
    the tool_call so AgentSession dispatches via the normal LiveKit
    tool path → existing RegistrySpecialist.
    """
    name = state.get("pending_specialist")
    if not name:
        logger.warning("[specialist] called with no pending_specialist")
        return {}

    output_messages: list = []

    # 1. Filler-once.
    if not state.get("handoff_filler_voiced"):
        filler = _pick_filler()
        logger.info("[specialist] filler: %r → %s", filler, name)
        output_messages.append(AIMessage(content=filler))

    # 2. Re-emit the handoff tool_call so the LLM adapter surfaces it.
    #    The most recent AIMessage in messages should be task_dispatch's
    #    output with tool_calls populated. Find it and copy the
    #    tool_calls into a new AIMessage in our return — this
    #    duplicates the tool_call into the post-handoff state slice
    #    that the LLM adapter will scan.
    last_handoff_call = None
    for m in reversed(state.get("messages") or []):
        tcs = getattr(m, "tool_calls", None) or []
        for tc in tcs:
            tc_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
            if tc_name.startswith("transfer_to_"):
                last_handoff_call = tc
                break
        if last_handoff_call is not None:
            break

    if last_handoff_call is not None:
        output_messages.append(AIMessage(
            content="",
            tool_calls=[last_handoff_call],
        ))

    # 3. Clear pending state so speak_gate releases. The specialist
    #    will run in a separate LiveKit turn (dispatched by
    #    AgentSession after our tool_call chunk is forwarded).
    return {
        "messages": output_messages,
        "pending_specialist": None,
        "pending_tool_calls": [],
        "last_tool_result": None,  # specialist hasn't run yet
        "handoff_filler_voiced": True,
    }
