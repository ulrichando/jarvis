"""JarvisState — the TypedDict every node in the supervisor graph
reads from and writes to.

The two load-bearing channels are `pending_tool_calls` and
`pending_specialist`. The terminal `speak_gate` node refuses to fire
while either is non-empty; that is the structural cure for the
"supervisor lies about completion" failure mode.

Channel design notes:
  - `messages` uses LangGraph's `add_messages` reducer so concurrent
    nodes can append cleanly. Standard LangGraph pattern.
  - `pending_tool_calls` is a list of tool_call_ids (strings). When a
    tool_call is emitted by a dispatch node, its id appears here.
    When a matching ToolMessage arrives, the id is removed.
  - `pending_specialist` is the name of an in-flight specialist
    handoff (e.g. "browser"). Set when transfer_to_X fires; cleared
    when the specialist's task_done returns.
  - `handoff_filler_voiced` is a single-shot flag. The graph emits a
    non-committal filler ("One moment, sir.") exactly once per handoff
    so the user hears a voice while the specialist works — but never
    a completion claim.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


Route = Literal["BANTER", "TASK", "REASONING", "EMOTIONAL", "WAITING"]


class JarvisState(TypedDict):
    # Conversation channels
    messages: Annotated[list[BaseMessage], add_messages]
    user_query: str
    audio_meta: dict[str, Any]

    # Routing
    route: Route
    route_confidence: float

    # State-shape gate (the structural cure)
    pending_tool_calls: list[str]
    pending_specialist: Optional[str]
    last_tool_result: Optional[str]
    handoff_filler_voiced: bool

    # Recovery
    failed_providers: list[str]
    retry_attempt: int

    # V2 — grounding gate
    grounding_retry_count: int
    grounding_rejected_claims: list[str]

    # V2 — speculative prefetch deferred to Phase 2 (the node was
    # implemented in 2026-05-04 but never wired into the graph; rather
    # than ship inert code, we removed it. State fields will return when
    # a working dispatch path is in place.)


def initial_state(user_query: str = "", audio_meta: Optional[dict] = None) -> JarvisState:
    """Construct a clean state for a new turn. The graph compile path
    expects every key present (TypedDict gates aren't enforced at
    runtime, but our nodes assume them)."""
    return JarvisState(
        messages=[],
        user_query=user_query,
        audio_meta=audio_meta or {},
        route="BANTER",
        route_confidence=0.0,
        pending_tool_calls=[],
        pending_specialist=None,
        last_tool_result=None,
        handoff_filler_voiced=False,
        failed_providers=[],
        retry_attempt=0,
        grounding_retry_count=0,
        grounding_rejected_claims=[],
    )
