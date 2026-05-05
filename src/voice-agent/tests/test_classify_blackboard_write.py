"""classify_node writes its intent record to the blackboard for
diagnostic / telemetry use."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


def test_classify_node_writes_intent_when_v2_on():
    from supervisor_graph.classify import classify_node
    from supervisor_graph.state import initial_state

    written = []

    class _Stub:
        def write_intent(self, i):
            written.append(i)

    state = initial_state(user_query="open YouTube")
    with patch.dict(os.environ, {"JARVIS_BLACKBOARD": "1"}), \
         patch("blackboard.client.BlackboardClient", return_value=_Stub()):
        out = classify_node(state)

    assert len(written) == 1
    intent = written[0]
    assert intent.route == "TASK"
    assert intent.raw_text == "open YouTube"


def test_classify_node_does_NOT_write_when_v2_off():
    from supervisor_graph.classify import classify_node
    from supervisor_graph.state import initial_state

    written = []

    class _Stub:
        def write_intent(self, i):
            written.append(i)

    state = initial_state(user_query="open YouTube")
    with patch.dict(os.environ, {"JARVIS_BLACKBOARD": "0"}), \
         patch("blackboard.client.BlackboardClient", return_value=_Stub()):
        classify_node(state)

    assert len(written) == 0
