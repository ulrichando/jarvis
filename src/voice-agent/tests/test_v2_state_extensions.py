"""V2 state additions — grounding retry budget + speculative dispatch tracking."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_state_has_v2_grounding_fields():
    from supervisor_graph.state import JarvisState
    keys = set(JarvisState.__annotations__.keys())
    assert "grounding_retry_count" in keys
    assert "grounding_rejected_claims" in keys


def test_state_has_v2_speculative_fields():
    from supervisor_graph.state import JarvisState
    keys = set(JarvisState.__annotations__.keys())
    assert "speculative_dispatch_id" in keys
    assert "speculative_result" in keys


def test_initial_state_zeroes_v2_fields():
    from supervisor_graph.state import initial_state
    s = initial_state(user_query="hi")
    assert s["grounding_retry_count"] == 0
    assert s["grounding_rejected_claims"] == []
    assert s["speculative_dispatch_id"] is None
    assert s["speculative_result"] is None
