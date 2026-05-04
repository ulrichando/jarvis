"""specialist_node:
  1. emits a filler chunk ("One moment, sir.") exactly once
  2. invokes the existing RegistrySpecialist via the registered
     transfer tool — re-using the production specialists/agent.py
  3. clears pending_specialist + pending_tool_calls on completion.

The filler-once rule is enforced via state.handoff_filler_voiced."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


def test_specialist_node_emits_filler_once():
    from supervisor_graph.specialist import specialist_node
    from supervisor_graph.state import initial_state

    state = initial_state(user_query="open a tab")
    state["pending_specialist"] = "browser"
    # Mock specialist runtime to return a clean summary.
    with patch(
        "supervisor_graph.specialist._run_specialist",
        return_value="Tab opened, sir.",
    ):
        out1 = specialist_node(state)
        # Filler should be in messages.
        contents = [getattr(m, "content", "") for m in out1["messages"]]
        # Filler must be one of the known non-committal fillers.
        # Pinning the exact list rather than substring-matching avoids
        # a flaky pass when random.choice picks "Let me check." or
        # "Looking now." (no "moment" / "on it" substrings).
        from supervisor_graph.specialist import _FILLERS
        assert any(c in _FILLERS for c in contents), (
            f"expected one of _FILLERS in {contents!r}"
        )
        assert out1["handoff_filler_voiced"] is True

    # Second invocation in same state (rare; mostly defensive) must
    # NOT add another filler.
    state2 = initial_state(user_query="…")
    state2["pending_specialist"] = "browser"
    state2["handoff_filler_voiced"] = True
    with patch(
        "supervisor_graph.specialist._run_specialist",
        return_value="Tab opened, sir.",
    ):
        out2 = specialist_node(state2)
        contents = [getattr(m, "content", "") for m in out2["messages"]]
        assert not any("one moment" in c.lower() for c in contents), (
            "filler must be emitted at most once per handoff"
        )


def test_specialist_node_clears_pending_on_success():
    from supervisor_graph.specialist import specialist_node
    from supervisor_graph.state import initial_state

    state = initial_state()
    state["pending_specialist"] = "browser"
    state["pending_tool_calls"] = ["call_abc"]
    with patch(
        "supervisor_graph.specialist._run_specialist",
        return_value="Tab opened, sir.",
    ):
        out = specialist_node(state)
    assert out["pending_specialist"] is None
    assert out["pending_tool_calls"] == []
    assert out["last_tool_result"] == "Tab opened, sir."


def test_specialist_node_handles_specialist_failure():
    """If the specialist raises or returns None, the node must NOT
    leave pending_specialist set (would deadlock the graph). Instead
    it surfaces a failure summary as last_tool_result."""
    from supervisor_graph.specialist import specialist_node
    from supervisor_graph.state import initial_state

    state = initial_state()
    state["pending_specialist"] = "browser"
    state["pending_tool_calls"] = ["call_abc"]
    with patch(
        "supervisor_graph.specialist._run_specialist",
        side_effect=RuntimeError("specialist crashed"),
    ):
        out = specialist_node(state)
    assert out["pending_specialist"] is None
    assert out["pending_tool_calls"] == []
    assert "failed" in (out["last_tool_result"] or "").lower() \
        or "error" in (out["last_tool_result"] or "").lower()
