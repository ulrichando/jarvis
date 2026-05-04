"""speak_gate is the structural cure: it refuses to terminate while
pending_tool_calls is non-empty OR pending_specialist is set.

The test enumerates every refusal condition and the release condition.
A regression here re-opens the "JARVIS lies about completion" bug —
keep this suite green at all times."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_speak_gate_releases_on_clean_state():
    from supervisor_graph.speak_gate import speak_gate_node
    from supervisor_graph.state import initial_state

    state = initial_state()
    # No pending tools, no pending specialist → can speak.
    out = speak_gate_node(state)
    assert out["__route__"] == "release"


def test_speak_gate_blocks_on_pending_tool_calls():
    from supervisor_graph.speak_gate import speak_gate_node
    from supervisor_graph.state import initial_state

    state = initial_state()
    state["pending_tool_calls"] = ["call_abc123"]
    out = speak_gate_node(state)
    assert out["__route__"] == "block_for_tool"


def test_speak_gate_blocks_on_pending_specialist():
    from supervisor_graph.speak_gate import speak_gate_node
    from supervisor_graph.state import initial_state

    state = initial_state()
    state["pending_specialist"] = "browser"
    out = speak_gate_node(state)
    assert out["__route__"] == "block_for_specialist"


def test_speak_gate_blocks_when_both_pending():
    from supervisor_graph.speak_gate import speak_gate_node
    from supervisor_graph.state import initial_state

    state = initial_state()
    state["pending_tool_calls"] = ["call_xyz"]
    state["pending_specialist"] = "browser"
    # Tool takes precedence in the routing label so debugging is easier.
    out = speak_gate_node(state)
    assert out["__route__"] == "block_for_tool"


def test_speak_gate_decision_for_branch():
    """The graph's conditional edge reads `__route__` and routes:
       release           → END
       block_for_tool    → tool_node
       block_for_specialist → specialist (waits for it)
    Verify the decision function maps these correctly."""
    from supervisor_graph.speak_gate import (
        speak_gate_node, speak_gate_branch,
    )
    assert speak_gate_branch({"__route__": "release"}) == "release"
    assert speak_gate_branch({"__route__": "block_for_tool"}) == "block_for_tool"
    assert speak_gate_branch({"__route__": "block_for_specialist"}) == "block_for_specialist"
