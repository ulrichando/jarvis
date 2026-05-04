"""Direct (non-handoff) tool calls run through LangGraph's prebuilt
ToolNode. The node executes each pending tool_call and emits a
ToolMessage. After execution, our cleanup step removes the call_id
from pending_tool_calls so speak_gate releases."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_tool_cleanup_clears_pending_on_tool_messages():
    """Given a state with pending_tool_calls=['x', 'y'] and ToolMessages
    for both x and y in messages, the cleanup function returns a state
    update with pending_tool_calls=[]."""
    from langchain_core.messages import ToolMessage, AIMessage
    from supervisor_graph.tools import clear_resolved_pending

    state = {
        "messages": [
            AIMessage(content="", tool_calls=[
                {"name": "f", "args": {}, "id": "x", "type": "tool_call"},
                {"name": "g", "args": {}, "id": "y", "type": "tool_call"},
            ]),
            ToolMessage(content="ok-x", tool_call_id="x"),
            ToolMessage(content="ok-y", tool_call_id="y"),
        ],
        "pending_tool_calls": ["x", "y"],
    }
    out = clear_resolved_pending(state)
    assert out["pending_tool_calls"] == []


def test_tool_cleanup_keeps_unresolved():
    from langchain_core.messages import ToolMessage, AIMessage
    from supervisor_graph.tools import clear_resolved_pending

    state = {
        "messages": [
            AIMessage(content="", tool_calls=[
                {"name": "f", "args": {}, "id": "x", "type": "tool_call"},
                {"name": "g", "args": {}, "id": "y", "type": "tool_call"},
            ]),
            ToolMessage(content="ok-x", tool_call_id="x"),
            # 'y' is still in flight — no ToolMessage yet.
        ],
        "pending_tool_calls": ["x", "y"],
    }
    out = clear_resolved_pending(state)
    assert out["pending_tool_calls"] == ["y"]
