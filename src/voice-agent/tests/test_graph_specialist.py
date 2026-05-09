"""specialist_node:
  1. emits a filler chunk ("One moment.") exactly once
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


def test_specialist_node_does_NOT_re_emit_tool_call():
    """REGRESSION GUARD (live-observed 2026-05-04): an earlier version
    of specialist_node re-emitted the most-recent transfer_to_* tool_call
    as a defensive duplicate. The LLM adapter walks every appended
    AIMessage and surfaces tool_calls from each, so the duplicate caused
    two identical handoffs to fire on the same turn. AgentSession
    rejected with "expected to receive only one AgentTask from the tool
    executions" → turn errored → supervisor re-ran → fresh filler every
    cycle ("On it." then "Looking now." etc.) until the user gave up.

    The fix: specialist_node MUST NOT add an AIMessage with tool_calls
    to its output. task_dispatch_node's AIMessage in state.messages
    already carries the tool_call; the adapter surfaces it once.
    """
    from supervisor_graph.specialist import specialist_node
    from supervisor_graph.state import initial_state

    state = initial_state()
    state["pending_specialist"] = "browser"
    state["pending_tool_calls"] = ["call_abc"]
    state["messages"] = [_ai_with_tool_call(
        "transfer_to_browser", {"request": "open a tab"}, "call_abc"
    )]
    out = specialist_node(state)
    # No output message may carry tool_calls — only filler content.
    for m in out["messages"]:
        tcs = getattr(m, "tool_calls", None) or []
        assert not tcs, (
            f"specialist_node must not re-emit tool_calls; "
            f"got {tcs!r} on a {type(m).__name__}. This re-opens the "
            f"double-handoff bug (AgentSession rejects with 'expected "
            f"only one AgentTask')."
        )
