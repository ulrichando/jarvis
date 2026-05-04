"""specialist_node:
  1. emits a filler chunk ("One moment, sir.") exactly once
  2. re-emits the most recent transfer_to_* tool_call so the LLM
     adapter surfaces it for AgentSession to dispatch via the
     existing RegistrySpecialist path (the actual specialist runs in
     a separate LiveKit turn, NOT in-process).
  3. clears pending_specialist + pending_tool_calls so speak_gate
     releases this turn."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _ai_with_tool_call(name, args, call_id):
    from langchain_core.messages import AIMessage
    return AIMessage(content="", tool_calls=[{
        "name": name, "args": args, "id": call_id, "type": "tool_call",
    }])


def test_specialist_node_emits_filler_once():
    from supervisor_graph.specialist import specialist_node, _FILLERS
    from supervisor_graph.state import initial_state

    state = initial_state(user_query="open a tab")
    state["pending_specialist"] = "browser"
    state["pending_tool_calls"] = ["call_abc"]
    state["messages"] = [_ai_with_tool_call(
        "transfer_to_browser", {"request": "open a tab"}, "call_abc"
    )]
    out1 = specialist_node(state)
    contents = [getattr(m, "content", "") for m in out1["messages"]]
    assert any(c in _FILLERS for c in contents), (
        f"expected one of _FILLERS in {contents!r}"
    )
    assert out1["handoff_filler_voiced"] is True

    # Second invocation in the same handoff (rare; defensive) must
    # NOT add another filler.
    state2 = initial_state(user_query="…")
    state2["pending_specialist"] = "browser"
    state2["pending_tool_calls"] = ["call_abc"]
    state2["handoff_filler_voiced"] = True
    state2["messages"] = [_ai_with_tool_call(
        "transfer_to_browser", {"request": "x"}, "call_abc"
    )]
    out2 = specialist_node(state2)
    contents = [getattr(m, "content", "") for m in out2["messages"]]
    assert not any(c in _FILLERS for c in contents), (
        "filler must be emitted at most once per handoff"
    )


def test_specialist_node_clears_pending():
    from supervisor_graph.specialist import specialist_node
    from supervisor_graph.state import initial_state

    state = initial_state()
    state["pending_specialist"] = "browser"
    state["pending_tool_calls"] = ["call_abc"]
    state["messages"] = [_ai_with_tool_call(
        "transfer_to_browser", {"request": "x"}, "call_abc"
    )]
    out = specialist_node(state)
    assert out["pending_specialist"] is None
    assert out["pending_tool_calls"] == []


def test_specialist_node_re_emits_tool_call():
    """The most recent transfer_to_* tool_call from messages must be
    surfaced as a tool_call in the output AIMessage so the LLM
    adapter forwards it to AgentSession."""
    from supervisor_graph.specialist import specialist_node
    from supervisor_graph.state import initial_state

    state = initial_state()
    state["pending_specialist"] = "browser"
    state["pending_tool_calls"] = ["call_abc"]
    state["messages"] = [_ai_with_tool_call(
        "transfer_to_browser", {"request": "open a tab"}, "call_abc"
    )]
    out = specialist_node(state)
    # Find the AIMessage with tool_calls in the output.
    tool_call_msg = None
    for m in out["messages"]:
        if getattr(m, "tool_calls", None):
            tool_call_msg = m
            break
    assert tool_call_msg is not None, (
        f"expected an AIMessage with tool_calls in output; got "
        f"{[type(m).__name__ for m in out['messages']]}"
    )
    tcs = tool_call_msg.tool_calls
    assert len(tcs) == 1
    tc = tcs[0]
    name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name")
    assert name == "transfer_to_browser"
