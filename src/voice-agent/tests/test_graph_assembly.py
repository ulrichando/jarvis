"""Integration test: compile the full graph and run synthetic
turns through it end-to-end. No real Groq calls — every LLM is
patched."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


def _ai(content: str = "", tool_calls=None):
    from langchain_core.messages import AIMessage
    return AIMessage(content=content, tool_calls=tool_calls or [])


def test_graph_compiles():
    from supervisor_graph.graph import build_graph
    g = build_graph(specialist_tools=[])
    assert g is not None


def test_graph_banter_path_end_to_end():
    """User says chitchat → classify routes BANTER → speak → END."""
    from supervisor_graph.graph import build_graph
    from supervisor_graph.state import initial_state

    fake_classifier = MagicMock()
    fake_classifier.invoke = MagicMock(
        return_value=MagicMock(content='{"route": "BANTER", "confidence": 0.9}')
    )
    fake_banter = MagicMock()
    fake_banter.invoke = MagicMock(return_value=_ai("Just fine."))

    with patch(
        "supervisor_graph.classify._build_classifier_chain",
        return_value=fake_classifier,
    ), patch(
        "supervisor_graph.dispatch._build_banter_llm",
        return_value=fake_banter,
    ):
        g = build_graph(specialist_tools=[])
        # Trigger non-regex path so the LLM classifier fires.
        out = g.invoke(initial_state(user_query="how are you"))

    contents = [getattr(m, "content", "") for m in out["messages"]]
    assert any("fine" in c.lower() for c in contents)
    assert out["route"] == "BANTER"
    # speak_gate must release — no pending state.
    assert out["pending_tool_calls"] == []
    assert out["pending_specialist"] is None


def test_graph_task_with_handoff_path_end_to_end():
    """User says verb-initial TASK → regex routes TASK → dispatch
    emits transfer_to_browser → specialist runs → done."""
    from supervisor_graph.graph import build_graph
    from supervisor_graph.state import initial_state

    fake_task_llm_response = _ai("", tool_calls=[
        {"name": "transfer_to_browser",
         "args": {"request": "open a tab"},
         "id": "call_xyz",
         "type": "tool_call"},
    ])
    fake_task_llm = MagicMock()
    fake_task_llm.bind_tools = MagicMock(return_value=fake_task_llm)
    fake_task_llm.invoke = MagicMock(return_value=fake_task_llm_response)

    # Stub the transfer_to_browser tool — graph treats anything starting
    # with transfer_to_ as a specialist handoff and routes accordingly.
    fake_specialist_tool = MagicMock()
    fake_specialist_tool.name = "transfer_to_browser"

    with patch(
        "supervisor_graph.dispatch._build_task_llm",
        return_value=fake_task_llm,
    ):
        g = build_graph(specialist_tools=[fake_specialist_tool])
        out = g.invoke(initial_state(user_query="open a tab"))

    # Filler must be present (the only thing the graph emits before
    # AgentSession dispatches the tool_call). The actual specialist
    # work happens in a separate LiveKit turn, NOT inside the graph.
    contents = " ".join(
        getattr(m, "content", "") for m in out["messages"]
    ).lower()
    assert ("moment" in contents or "on it" in contents
            or "let me check" in contents or "looking now" in contents)
    # The transfer_to_browser tool_call must be RE-EMITTED in
    # specialist_node's output so the LLM adapter forwards it as a
    # ChatChunk with tool_calls populated.
    has_handoff_tc = False
    for m in out["messages"]:
        for tc in (getattr(m, "tool_calls", None) or []):
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name")
            if name == "transfer_to_browser":
                has_handoff_tc = True
                break
    assert has_handoff_tc, (
        "expected transfer_to_browser tool_call surfaced for AgentSession"
    )
    # speak_gate released cleanly — pending state cleared so this
    # turn ends and AgentSession can dispatch the tool.
    assert out["pending_specialist"] is None
    assert out["pending_tool_calls"] == []
