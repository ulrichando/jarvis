"""grounding_gate_node — Phase 1 binary release-or-replace, TASK-only."""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _draft_state(text: str, retry_count: int = 0):
    """Build a JarvisState with one assistant TASK-route message."""
    from langchain_core.messages import AIMessage
    from supervisor_graph.state import initial_state
    s = initial_state()
    s["route"] = "TASK"
    s["messages"] = [AIMessage(content=text)]
    s["grounding_retry_count"] = retry_count
    return s


def _stub_client_with_evidence(tool: str, ts_offset: float = -1):
    from blackboard.schema import ToolResult
    client = MagicMock()
    client.recent_tools = MagicMock(return_value=[
        ToolResult(
            tool=tool, args={}, result=f"ok: {tool} succeeded",
            ok=True, ts=time.time() + ts_offset, call_id="x",
        ),
    ])
    return client


def _stub_client_empty():
    client = MagicMock()
    client.recent_tools = MagicMock(return_value=[])
    return client


def test_release_when_all_claims_have_evidence():
    from supervisor_graph.grounding_gate import grounding_gate_node
    state = _draft_state("I've opened a new tab.")
    out = grounding_gate_node(state, client=_stub_client_with_evidence("ext_new_tab"))
    # No state mutation — gate released cleanly.
    assert "messages" not in out
    assert "grounding_rejected_claims" not in out


def test_no_claims_passes_through():
    """Text with no past-tense claim is always released."""
    from supervisor_graph.grounding_gate import grounding_gate_node
    state = _draft_state("How are you?")
    out = grounding_gate_node(state, client=_stub_client_empty())
    # No claims → no mutation.
    assert "messages" not in out
    assert "grounding_rejected_claims" not in out


def test_release_when_route_not_task():
    """Non-TASK routes bypass grounding entirely (Phase 1 scope)."""
    from supervisor_graph.grounding_gate import grounding_gate_node
    from supervisor_graph.state import initial_state
    from langchain_core.messages import AIMessage

    state = initial_state()
    state["route"] = "BANTER"
    state["messages"] = [AIMessage(content="I've sent your email.")]
    out = grounding_gate_node(state, client=_stub_client_empty())
    # No state mutation expected — gate bypassed.
    assert "messages" not in out
    assert "grounding_rejected_claims" not in out


def test_reject_replaces_message_in_place():
    """Phase 1: rejection REPLACES the lying message via id-match
    (LangChain add_messages reducer)."""
    from supervisor_graph.grounding_gate import grounding_gate_node, GROUNDING_FALLBACK_MESSAGE
    from supervisor_graph.state import initial_state
    from langchain_core.messages import AIMessage

    rejected = AIMessage(content="I've sent the email.", id="msg_42")
    state = initial_state()
    state["route"] = "TASK"
    state["messages"] = [rejected]
    out = grounding_gate_node(state, client=_stub_client_empty())

    # Replacement message must have the SAME id so add_messages replaces.
    assert "messages" in out
    replacement = out["messages"][0]
    assert replacement.id == "msg_42", (
        f"expected replacement id 'msg_42'; got {replacement.id!r} "
        "— without id-match, LangGraph appends instead of replaces, "
        "and TTS will speak both the lie AND the fallback"
    )
    assert replacement.content == GROUNDING_FALLBACK_MESSAGE
    assert "grounding_rejected_claims" in out
