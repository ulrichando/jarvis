"""speak_gate — the structural cure for "JARVIS lies about completion".

The terminal speak path of the supervisor graph runs through this gate.
The gate inspects state and emits a routing decision. Three outcomes:

  release              — both pending lists empty → graph proceeds to END
                         (the assistant's final content has already been
                          emitted upstream; speak_gate does not synthesize
                          new content, it only decides "is it safe to leave
                          this turn?").
  block_for_tool       — pending_tool_calls non-empty → route back to
                         tool_node so the in-flight tool can complete.
  block_for_specialist — pending_specialist set → wait for the
                         specialist's task_done before proceeding.

What this prevents:

  - The supervisor LLM (or its fallback) emitting "Done, sir" while a
    tool_call has not been resolved.
  - Cross-stream lies where DeepSeek-the-fallback hallucinates
    completion in a NEW stream after Groq dropped a tool_call mid-way.

Both failure modes were live-observed 2026-05-04 and are the specific
bugs this gate exists to make impossible.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("supervisor_graph.speak_gate")


def speak_gate_node(state: dict) -> dict:
    """LangGraph node. Returns a routing-decision label in
    `__route__`. Pure function; never speaks; never mutates state
    fields the user sees."""
    pending_tools = state.get("pending_tool_calls") or []
    pending_spec = state.get("pending_specialist")

    if pending_tools:
        logger.warning(
            "[speak-gate] BLOCK pending_tool_calls=%s — routing back to tool_node",
            pending_tools,
        )
        return {"__route__": "block_for_tool"}

    if pending_spec:
        logger.warning(
            "[speak-gate] BLOCK pending_specialist=%r — waiting for task_done",
            pending_spec,
        )
        return {"__route__": "block_for_specialist"}

    logger.info("[speak-gate] release — no pending tools or specialist")
    return {"__route__": "release"}


def speak_gate_branch(state: dict) -> str:
    """Branch function used by `add_conditional_edges`. Returns one of
    'release', 'block_for_tool', 'block_for_specialist' so the graph
    can dispatch to the right next node."""
    route = state.get("__route__")
    if route in ("release", "block_for_tool", "block_for_specialist"):
        return route
    # Defensive default: release. Better to under-block than to deadlock.
    logger.warning(
        "[speak-gate] unknown __route__=%r; defaulting to release",
        route,
    )
    return "release"
