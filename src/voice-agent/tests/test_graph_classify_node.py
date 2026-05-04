"""classify_node mutates JarvisState in place: sets `route` and
`route_confidence`. Regex match → TASK with confidence 1.0 (skip LLM).
Regex miss → call LLM."""
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


def test_classify_node_regex_match_skips_llm():
    from supervisor_graph.classify import classify_node
    from supervisor_graph.state import initial_state

    state = initial_state(user_query="open a new tab")
    # Patch the LLM classifier; it MUST NOT be called when regex matches.
    with patch(
        "supervisor_graph.classify.classify_with_llm"
    ) as mock_llm:
        out = classify_node(state)
    assert mock_llm.called is False
    assert out["route"] == "TASK"
    assert out["route_confidence"] == 1.0


def test_classify_node_regex_miss_falls_back_to_llm():
    from supervisor_graph.classify import classify_node
    from supervisor_graph.state import initial_state

    state = initial_state(user_query="how are you")
    with patch(
        "supervisor_graph.classify.classify_with_llm",
        return_value={"route": "BANTER", "confidence": 0.85},
    ) as mock_llm:
        out = classify_node(state)
    assert mock_llm.call_count == 1
    assert out["route"] == "BANTER"
    assert out["route_confidence"] == 0.85
