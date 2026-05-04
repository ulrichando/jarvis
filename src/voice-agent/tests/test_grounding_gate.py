"""grounding_gate_node — validates draft against blackboard with retry budget."""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _draft_state(text: str, retry_count: int = 0):
    """Build a JarvisState with one assistant message containing `text`."""
    from langchain_core.messages import AIMessage
    from supervisor_graph.state import initial_state
    s = initial_state()
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
    state = _draft_state("I've opened a new tab, sir.")
    out = grounding_gate_node(state, client=_stub_client_with_evidence("ext_new_tab"))
    assert out["__route__"] == "release"
    assert "messages" not in out  # message untouched


def test_reject_and_retry_when_claim_lacks_evidence():
    from supervisor_graph.grounding_gate import grounding_gate_node
    state = _draft_state("I've sent the email, sir.")
    # No evidence on board.
    out = grounding_gate_node(state, client=_stub_client_empty())
    assert out["__route__"] == "regenerate"
    assert out["grounding_retry_count"] == 1
    assert "rejected_claims" in out or "grounding_rejected_claims" in out


def test_no_claims_passes_through():
    """Text with no past-tense claim is always released."""
    from supervisor_graph.grounding_gate import grounding_gate_node
    state = _draft_state("How are you, sir?")
    out = grounding_gate_node(state, client=_stub_client_empty())
    assert out["__route__"] == "release"


def test_retry_budget_exhausted_emits_fallback():
    """After 3 rejections, replace the draft with a fixed honest message."""
    from supervisor_graph.grounding_gate import grounding_gate_node
    state = _draft_state("I've opened it.", retry_count=3)
    out = grounding_gate_node(state, client=_stub_client_empty())
    assert out["__route__"] == "release"
    # The message must have been replaced with the honest fallback.
    msgs = out["messages"]
    assert len(msgs) >= 1
    content = msgs[-1].content.lower()
    assert "didn't go" in content or "wasn't able" in content or "expected" in content
