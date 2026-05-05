"""build_graph() — assembles the supervisor StateGraph.

Topology:

    START
      ↓
    classify
      ↓ (route)
      ├─ BANTER → banter_speak → speak_gate → END
      ├─ EMOTIONAL → emotional_speak → speak_gate → END
      ├─ REASONING → reasoning_speak → speak_gate → END
      ├─ TASK → task_dispatch
      │     ↓ (does it carry transfer_to_*?)
      │     ├─ yes → set pending_specialist
      │     │       → specialist (filler + run + clear) → speak_gate → END
      │     └─ no  → tool_node → cleanup → speak_gate → END

The conditional branches are implemented with `add_conditional_edges`.
"""
from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

from .classify import classify_node
from .dispatch import (
    banter_speak_node,
    emotional_speak_node,
    reasoning_speak_node,
    task_dispatch_node,
)
from .grounding_gate import grounding_gate_node
from .specialist import specialist_node
from .speak_gate import speak_gate_branch, speak_gate_node
from .state import JarvisState
from .tools import clear_resolved_pending

logger = logging.getLogger("supervisor_graph.graph")


def _route_branch(state: dict) -> str:
    """Branch fn after classify_node — fans out to the right speak/dispatch."""
    return state.get("route") or "BANTER"


def _post_dispatch_branch(state: dict) -> str:
    """Branch fn after task_dispatch_node — was the tool a handoff
    (transfer_to_*) or a direct tool call?

    `pending_specialist` is set by task_dispatch_node in its return dict
    so LangGraph's reducer propagates it before this branch fn runs.
    We simply read state; no mutation needed."""
    if state.get("pending_specialist"):
        return "specialist"
    msgs = state.get("messages") or []
    if not msgs:
        return "no_op"
    last = msgs[-1]
    tcs = getattr(last, "tool_calls", None) or []
    if tcs:
        return "tool_node"
    return "no_op"


def build_graph(*, specialist_tools: list[Any]):
    """Compile the supervisor graph. `specialist_tools` is the list
    of @function_tool transfer_to_X (and `delegate`) tools the
    supervisor's task_dispatch should bind."""

    g = StateGraph(JarvisState)

    # Nodes
    g.add_node("classify", classify_node)
    g.add_node("banter", banter_speak_node)
    g.add_node("reasoning", reasoning_speak_node)
    g.add_node("emotional", emotional_speak_node)
    g.add_node(
        "task_dispatch",
        lambda s: task_dispatch_node(s, tools=specialist_tools),
    )
    g.add_node("specialist", specialist_node)
    # tool_node is currently a no-op for direct (non-handoff) tools;
    # specialists are the dispatch path today. Direct tool execution
    # via LangGraph's prebuilt ToolNode is wired in a future task
    # when we add non-specialist tools. For now we just clear pending.
    g.add_node("tool_node", clear_resolved_pending)
    g.add_node("speak_gate", speak_gate_node)
    # V2 grounding gate (gated by JARVIS_BLACKBOARD env). Validates
    # the supervisor's draft against blackboard tool results before
    # release. When the flag is OFF this node short-circuits to
    # "release" so v1 behavior is preserved exactly.
    import os as _os
    if _os.environ.get("JARVIS_BLACKBOARD", "0") == "1":
        g.add_node("grounding_gate", grounding_gate_node)
    else:
        # No-op shim: passthrough that always releases.
        g.add_node("grounding_gate", lambda s: {})
    # No-op terminal for the rare WAITING / unknown route.
    g.add_node("no_op", lambda s: {})

    # Edges
    g.add_edge(START, "classify")
    g.add_conditional_edges(
        "classify",
        _route_branch,
        {
            "BANTER": "banter",
            "REASONING": "reasoning",
            "EMOTIONAL": "emotional",
            "TASK": "task_dispatch",
            "WAITING": "no_op",
        },
    )

    # Speak nodes go through the gate before END.
    for n in ("banter", "reasoning", "emotional"):
        g.add_edge(n, "speak_gate")

    # task_dispatch fans out: handoff → specialist; direct → tool_node.
    g.add_conditional_edges(
        "task_dispatch",
        _post_dispatch_branch,
        {
            "specialist": "specialist",
            "tool_node": "tool_node",
            "no_op": "no_op",
        },
    )

    # Specialist + tool_node converge at speak_gate.
    g.add_edge("specialist", "speak_gate")
    g.add_edge("tool_node", "speak_gate")
    g.add_edge("no_op", END)

    # speak_gate decides: release → grounding_gate; otherwise loop.
    g.add_conditional_edges(
        "speak_gate",
        speak_gate_branch,
        {
            "release": "grounding_gate",   # was: END
            "block_for_tool": "tool_node",
            "block_for_specialist": "specialist",
        },
    )

    # Grounding gate is binary in Phase 1: it either passes the message
    # through unchanged or replaces it with the honest fallback in place.
    # Either way, the next stop is END — no regeneration loop.
    g.add_edge("grounding_gate", END)

    return g.compile()
