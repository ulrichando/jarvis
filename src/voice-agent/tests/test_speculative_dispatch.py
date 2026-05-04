"""Speculative dispatch — fires safe tools speculatively, reconciles
afterward."""
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_speculative_skipped_when_low_confidence():
    """Confidence below threshold → no speculative dispatch."""
    from supervisor_graph.speculative import speculative_dispatch_node
    from supervisor_graph.state import initial_state
    s = initial_state(user_query="hello")
    s["route"] = "TASK"
    s["route_confidence"] = 0.5  # below default threshold of 0.7
    out = speculative_dispatch_node(s)
    assert out.get("speculative_dispatch_id") is None


def test_speculative_skipped_when_route_not_task():
    from supervisor_graph.speculative import speculative_dispatch_node
    from supervisor_graph.state import initial_state
    s = initial_state(user_query="hi")
    s["route"] = "BANTER"
    s["route_confidence"] = 0.99
    out = speculative_dispatch_node(s)
    assert out.get("speculative_dispatch_id") is None


def test_speculative_skipped_for_destructive_predicted_tool():
    """If the predictor returns a non-safe tool (e.g. ext_click), skip."""
    from supervisor_graph.speculative import speculative_dispatch_node
    from supervisor_graph.state import initial_state
    s = initial_state(user_query="click the button")
    s["route"] = "TASK"
    s["route_confidence"] = 0.95
    with patch("supervisor_graph.speculative._predict_tool",
               return_value="ext_click"):
        out = speculative_dispatch_node(s)
    assert out.get("speculative_dispatch_id") is None


def test_speculative_fires_for_safe_predicted_tool():
    from supervisor_graph.speculative import speculative_dispatch_node
    from supervisor_graph.state import initial_state
    s = initial_state(user_query="open YouTube")
    s["route"] = "TASK"
    s["route_confidence"] = 0.95
    with patch("supervisor_graph.speculative._predict_tool",
               return_value="transfer_to_browser"):
        out = speculative_dispatch_node(s)
    assert out.get("speculative_dispatch_id") is not None


def test_reconcile_uses_cached_result_when_tool_matches():
    from supervisor_graph.speculative import reconcile_speculative_result
    state = {
        "speculative_dispatch_id": "spec_123",
        "speculative_result": {"tool": "transfer_to_browser",
                                "result": "tab opened", "ok": True},
    }
    real_call = {"name": "transfer_to_browser", "args": {"request": "open YouTube"}}
    out = reconcile_speculative_result(state, real_call)
    assert out["use_cached"] is True


def test_reconcile_discards_when_real_tool_differs():
    from supervisor_graph.speculative import reconcile_speculative_result
    state = {
        "speculative_dispatch_id": "spec_123",
        "speculative_result": {"tool": "transfer_to_browser",
                                "result": "tab opened", "ok": True},
    }
    real_call = {"name": "transfer_to_desktop", "args": {"request": "open Chrome app"}}
    out = reconcile_speculative_result(state, real_call)
    assert out["use_cached"] is False
