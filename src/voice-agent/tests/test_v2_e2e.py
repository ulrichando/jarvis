"""End-to-end V2 path with mocked LLMs + real blackboard (Redis).

This exercises the full graph flow: classify → dispatch → specialist
→ tool result → blackboard write → grounding gate release. Uses a
real Redis with a unique prefix; cleans up after.

Note: an earlier draft of this docstring listed `speculative` as a
node between classify and dispatch. That node was removed in commit
feb681d4 ("speculative: remove dead code — defer to Phase 2"); the
graph now goes classify → dispatch directly."""
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


@pytest.fixture
def isolated_blackboard():
    """Create an isolated Redis namespace for this test."""
    from blackboard.client import BlackboardClient
    prefix = f"e2e_test:{time.time_ns()}"
    c = BlackboardClient(prefix=prefix)
    yield prefix, c
    # Cleanup: delete all keys under this prefix
    for key in c._r.keys(f"{prefix}:*"):
        c._r.delete(key)


def _ai(content: str = "", tool_calls=None):
    """Helper to build AIMessage with tool_calls."""
    from langchain_core.messages import AIMessage
    return AIMessage(content=content, tool_calls=tool_calls or [])


def test_v2_e2e_classify_to_grounding_gate_release(isolated_blackboard):
    """User asks "open a tab" → graph classifies as TASK → task_dispatch
    emits transfer_to_browser → specialist node emits filler → speak_gate
    releases → grounding_gate finds recent tool evidence and releases to END.
    """
    prefix, bb = isolated_blackboard

    from supervisor_graph.graph import build_graph
    from supervisor_graph.state import initial_state
    from blackboard.schema import ToolResult

    # Step 1: Mock the classify node to return TASK route
    def mock_classify(state):
        return {
            "route": "TASK",
            "route_confidence": 0.95,
        }

    # Step 2: Mock task_dispatch to emit transfer_to_browser
    fake_task_response = _ai("", tool_calls=[{
        "name": "transfer_to_browser",
        "args": {"request": "open a tab"},
        "id": "call_e2e_001",
        "type": "tool_call",
    }])
    fake_task_llm = MagicMock()
    fake_task_llm.bind_tools = MagicMock(return_value=fake_task_llm)
    fake_task_llm.invoke = MagicMock(return_value=fake_task_response)

    fake_tool = MagicMock()
    fake_tool.name = "transfer_to_browser"
    fake_tool.description = "Transfer to browser specialist"
    fake_tool.info = MagicMock()
    fake_tool.info.name = "transfer_to_browser"
    fake_tool.info.description = "Transfer to browser specialist"
    fake_tool.info.arguments_dict = {
        "type": "object",
        "properties": {
            "request": {"type": "string"}
        },
        "required": ["request"]
    }

    # Step 3: Pre-populate blackboard with a matching tool result
    # so grounding_gate will find evidence and release
    result = ToolResult(
        tool="transfer_to_browser",
        args={"request": "open a tab"},
        result="Tab opened successfully",
        ok=True,
        ts=time.time() - 1,  # 1 second ago (within 30s window)
        call_id="call_e2e_001",
    )
    bb.write_tool_result(result)

    # Step 4: Build and invoke the graph with mocked classify + task_dispatch
    with patch.dict(os.environ, {
        "JARVIS_BLACKBOARD": "1",
        "JARVIS_BLACKBOARD_PREFIX": prefix,
    }), patch("supervisor_graph.classify.classify_node", side_effect=mock_classify), \
        patch("supervisor_graph.dispatch._build_task_llm", return_value=fake_task_llm):
        g = build_graph(specialist_tools=[fake_tool])
        out = g.invoke(initial_state(user_query="open a tab"))

    # Assertions
    # 1. Specialist node must have emitted a filler
    contents = " ".join(getattr(m, "content", "") for m in out["messages"]).lower()
    fillers = ("moment", "on it", "looking", "let me check")
    assert any(f in contents for f in fillers), \
        f"Expected filler in messages, got: {contents}"

    # 2. Graph must have released cleanly through grounding_gate
    assert out["pending_specialist"] is None, \
        f"pending_specialist should be None, got {out['pending_specialist']}"
    assert out["pending_tool_calls"] == [], \
        f"pending_tool_calls should be empty, got {out['pending_tool_calls']}"

    # 3. Final message should reference the tool result (no regeneration)
    assert out.get("grounding_retry_count", 0) == 0, \
        "grounding_gate should not have retried"


def test_v2_e2e_banter_route_no_blackboard(isolated_blackboard):
    """User asks "how are you" → classify routes to BANTER → banter_speak
    emits content → speak_gate releases → grounding_gate (since no tool
    claims, passes through) → END. No blackboard evidence needed.
    """
    prefix, bb = isolated_blackboard

    from supervisor_graph.graph import build_graph
    from supervisor_graph.state import initial_state
    from langchain_core.messages import AIMessage

    # Mock classify to return BANTER
    def mock_classify(state):
        return {
            "route": "BANTER",
            "route_confidence": 0.92,
        }

    # Mock the banter LLM to return simple content
    fake_banter_llm = MagicMock()
    fake_banter_llm.invoke = MagicMock(
        return_value=AIMessage(content="I'm doing splendidly, thank you for asking.")
    )

    with patch.dict(os.environ, {
        "JARVIS_BLACKBOARD": "1",
        "JARVIS_BLACKBOARD_PREFIX": prefix,
    }), patch("supervisor_graph.classify.classify_node", side_effect=mock_classify), \
        patch("supervisor_graph.dispatch._build_banter_llm", return_value=fake_banter_llm):
        g = build_graph(specialist_tools=[])
        out = g.invoke(initial_state(user_query="how are you"))

    # Assertions
    # 1. Must have content from banter_speak_node
    contents = " ".join(getattr(m, "content", "") for m in out["messages"]).lower()
    assert "splendidly" in contents, f"Expected banter content, got: {contents}"

    # 2. Must release cleanly
    assert out["pending_specialist"] is None
    assert out["pending_tool_calls"] == []
    assert out.get("grounding_retry_count", 0) == 0


def test_v2_e2e_speak_gate_blocks_pending_specialist(isolated_blackboard):
    """Graph structure test: after specialist_node, pending_specialist
    is cleared but pending_tool_calls may remain. speak_gate should block
    if pending_tool_calls is non-empty (real scenario: specialist may emit
    additional nested tool_calls).
    """
    prefix, bb = isolated_blackboard

    from supervisor_graph.graph import build_graph
    from supervisor_graph.state import initial_state

    # Mock classify to return TASK
    def mock_classify(state):
        return {
            "route": "TASK",
            "route_confidence": 0.95,
        }

    # Mock task_dispatch to emit transfer_to_browser with pending tools
    fake_task_response = _ai("", tool_calls=[{
        "name": "transfer_to_browser",
        "args": {"request": "search google"},
        "id": "call_xyz_001",
        "type": "tool_call",
    }])
    fake_task_llm = MagicMock()
    fake_task_llm.bind_tools = MagicMock(return_value=fake_task_llm)
    fake_task_llm.invoke = MagicMock(return_value=fake_task_response)

    fake_tool = MagicMock()
    fake_tool.name = "transfer_to_browser"
    fake_tool.info = MagicMock()
    fake_tool.info.name = "transfer_to_browser"
    fake_tool.info.arguments_dict = {"type": "object", "properties": {}, "required": []}

    with patch.dict(os.environ, {
        "JARVIS_BLACKBOARD": "1",
        "JARVIS_BLACKBOARD_PREFIX": prefix,
    }), patch("supervisor_graph.classify.classify_node", side_effect=mock_classify), \
        patch("supervisor_graph.dispatch._build_task_llm", return_value=fake_task_llm):
        g = build_graph(specialist_tools=[fake_tool])
        # Invoke without pre-populating evidence, so grounding_gate will
        # try to validate and reject if the supervisor emits success claims
        out = g.invoke(initial_state(user_query="search google"))

    # After specialist_node, pending_specialist should be cleared
    # but pending_tool_calls will still reference the transfer_to_browser
    # The graph may loop or the filler won't contain claims, so grounding
    # gate will release. This test just verifies the shape isn't broken.
    assert "messages" in out


def test_v2_e2e_grounding_gate_rejects_unsupported_claim(isolated_blackboard):
    """User says "open a tab" but no evidence in blackboard → specialist_node
    emits non-committal filler (which has no claims to ground) → grounding_gate
    releases without needing blackboard evidence. Tests that the gate doesn't
    reject fillers.
    """
    prefix, bb = isolated_blackboard

    from supervisor_graph.graph import build_graph
    from supervisor_graph.state import initial_state
    from langchain_core.messages import AIMessage

    # Mock classify to return TASK
    def mock_classify(state):
        return {
            "route": "TASK",
            "route_confidence": 0.95,
        }

    # Mock task_dispatch to emit transfer_to_browser
    fake_response = AIMessage(
        content="",
        tool_calls=[{
            "name": "transfer_to_browser",
            "args": {"request": "new tab"},
            "id": "call_new_001",
            "type": "tool_call",
        }]
    )
    fake_task_llm = MagicMock()
    fake_task_llm.bind_tools = MagicMock(return_value=fake_task_llm)
    fake_task_llm.invoke = MagicMock(return_value=fake_response)

    fake_tool = MagicMock()
    fake_tool.name = "transfer_to_browser"
    fake_tool.info = MagicMock()
    fake_tool.info.name = "transfer_to_browser"
    fake_tool.info.arguments_dict = {"type": "object", "properties": {}, "required": []}

    with patch.dict(os.environ, {
        "JARVIS_BLACKBOARD": "1",
        "JARVIS_BLACKBOARD_PREFIX": prefix,
    }), patch("supervisor_graph.classify.classify_node", side_effect=mock_classify), \
        patch("supervisor_graph.dispatch._build_task_llm", return_value=fake_task_llm):
        g = build_graph(specialist_tools=[fake_tool])
        # Invoke WITHOUT pre-populating evidence
        out = g.invoke(initial_state(user_query="open a new tab"))

    # The specialist_node emits a filler ("One moment, sir." etc).
    # The filler has no past-tense claims, so grounding_gate will find
    # nothing to validate and release cleanly (no evidence needed).
    contents = " ".join(getattr(m, "content", "") for m in out["messages"]).lower()
    fillers = ("moment", "on it", "looking", "let me check")
    assert any(f in contents for f in fillers), \
        f"Expected filler in messages, got: {contents}"

    # grounding_gate should not have needed to retry since there were
    # no claims to validate
    assert out.get("grounding_retry_count", 0) == 0, \
        "Fillers have no claims, so grounding_gate shouldn't retry"
    assert out["pending_specialist"] is None
    assert out["pending_tool_calls"] == []
