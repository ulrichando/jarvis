"""V2 state additions — grounding retry budget tracking."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_state_has_v2_grounding_fields():
    from supervisor_graph.state import JarvisState
    keys = set(JarvisState.__annotations__.keys())
    assert "grounding_retry_count" in keys
    assert "grounding_rejected_claims" in keys


def test_initial_state_zeroes_v2_fields():
    from supervisor_graph.state import initial_state
    s = initial_state(user_query="hi")
    assert s["grounding_retry_count"] == 0
    assert s["grounding_rejected_claims"] == []
