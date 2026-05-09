"""specialist_node — graph-side handoff coordinator.

Architecture (Phase 6, 2026-05-04): the graph does NOT run the
specialist in-process. The specialist's actual work (browser
ext_*, desktop tools, etc.) runs through LiveKit AgentSession's
normal tool dispatch, kicking off the existing RegistrySpecialist
machinery (specialists/agent.py with the task_done gate). The graph's
job here is:

  1. Emit the non-committal filler ("One moment.") exactly once
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
    "One moment.",
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

    # 2. Clear pending state so speak_gate releases. The specialist
    #    will run in a separate LiveKit turn (dispatched by
    #    AgentSession after the LLM adapter forwards
    #    task_dispatch_node's tool_call chunk).
    #
    # NOTE (live-observed 2026-05-04): an earlier version of this node
    # ALSO re-emitted the transfer_to_* tool_call as a defensive
    # duplicate. That was a bug — the LLM adapter walks every appended
    # AIMessage and surfaces tool_calls from each, so the duplicate
    # caused two identical handoffs to fire on the same turn. AgentSession
    # rejected with "expected to receive only one AgentTask from the
    # tool executions" → turn errored → supervisor re-ran from scratch
    # → fresh filler emitted every cycle ("On it." then "Looking now."
    # then "Let me check.") until the user gave up. Don't re-add the
    # re-emit. task_dispatch_node's AIMessage in state.messages already
    # carries the tool_call; the adapter already surfaces it once.
    return {
        "messages": output_messages,
        "pending_specialist": None,
        "pending_tool_calls": [],
        "last_tool_result": None,  # specialist hasn't run yet
        "handoff_filler_voiced": True,
    }
