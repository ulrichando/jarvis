"""JarvisState is the contract every node reads from and writes to.
Pin its shape so a refactor can't silently drop a channel."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_state_required_channels_present():
    from supervisor_graph.state import JarvisState
    # TypedDict introspection — annotations dict carries the channels.
    keys = set(JarvisState.__annotations__.keys())
    required = {
        # Conversation
        "messages", "user_query", "audio_meta",
        # Routing
        "route", "route_confidence",
        # State-shape gate (load-bearing)
        "pending_tool_calls", "pending_specialist",
        "last_tool_result", "handoff_filler_voiced",
        # Recovery
        "failed_providers", "retry_attempt",
    }
    missing = required - keys
    assert not missing, f"JarvisState missing channels: {missing}"


def test_initial_state_factory():
    from supervisor_graph.state import JarvisState, initial_state
    s = initial_state(user_query="hello")
    assert s["user_query"] == "hello"
    assert s["pending_tool_calls"] == []
    assert s["pending_specialist"] is None
    assert s["handoff_filler_voiced"] is False
    assert s["retry_attempt"] == 0
    assert s["failed_providers"] == []
    assert s["messages"] == []
