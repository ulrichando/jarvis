"""V2 graph assembly — grounding_gate inserted between speak_gate and END."""
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


def _ai(content: str = "", tool_calls=None):
    from langchain_core.messages import AIMessage
    return AIMessage(content=content, tool_calls=tool_calls or [])


def test_v2_graph_compiles_with_blackboard_flag():
    """When JARVIS_BLACKBOARD=1, build_graph must compile without error."""
    from supervisor_graph.graph import build_graph
    with patch.dict(os.environ, {"JARVIS_BLACKBOARD": "1"}):
        g = build_graph(specialist_tools=[])
    assert g is not None


def test_v2_graph_grounding_releases_when_no_claims():
    """Banter content (no past-tense claim) sails through the gate."""
    from supervisor_graph.graph import build_graph
    from supervisor_graph.state import initial_state

    fake_classifier = MagicMock()
    fake_classifier.invoke = MagicMock(
        return_value=MagicMock(content='{"route": "BANTER", "confidence": 0.9}')
    )
    fake_banter = MagicMock()
    fake_banter.invoke = MagicMock(return_value=_ai("Just fine, sir."))

    with patch.dict(os.environ, {"JARVIS_BLACKBOARD": "1"}), \
         patch("supervisor_graph.classify._build_classifier_chain",
               return_value=fake_classifier), \
         patch("supervisor_graph.dispatch._build_banter_llm",
               return_value=fake_banter), \
         patch("blackboard.client.BlackboardClient") as MockClient:
        MockClient.return_value.recent_tools = MagicMock(return_value=[])
        g = build_graph(specialist_tools=[])
        out = g.invoke(initial_state(user_query="how are you"))

    contents = [getattr(m, "content", "") for m in out["messages"]]
    assert any("fine" in c.lower() for c in contents)


def test_v1_graph_unchanged_when_v2_flag_off():
    """When JARVIS_BLACKBOARD is unset, the graph compiles to the
    v1 shape — grounding_gate is a no-op pass-through."""
    from supervisor_graph.graph import build_graph
    from supervisor_graph.state import initial_state

    fake_classifier = MagicMock()
    fake_classifier.invoke = MagicMock(
        return_value=MagicMock(content='{"route": "BANTER", "confidence": 0.9}')
    )
    fake_banter = MagicMock()
    fake_banter.invoke = MagicMock(return_value=_ai("Just fine, sir."))

    with patch.dict(os.environ, {"JARVIS_BLACKBOARD": "0"}), \
         patch("supervisor_graph.classify._build_classifier_chain",
               return_value=fake_classifier), \
         patch("supervisor_graph.dispatch._build_banter_llm",
               return_value=fake_banter):
        g = build_graph(specialist_tools=[])
        out = g.invoke(initial_state(user_query="how are you"))

    contents = [getattr(m, "content", "") for m in out["messages"]]
    assert any("fine" in c.lower() for c in contents)
