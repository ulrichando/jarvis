"""Speculative-safe tool whitelist — destructive ops never run speculatively."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.mark.parametrize("tool_name,expected", [
    # Browser navigation — safe to dispatch speculatively (idempotent).
    ("transfer_to_browser", True),
    # The browser specialist's individual tools (if dispatched directly):
    ("ext_navigate", True),
    ("ext_new_tab", True),
    ("ext_screenshot", True),
    ("ext_observe", True),
    ("web_search", True),
    # Destructive — must never be speculative.
    ("ext_click", False),
    ("ext_type", False),
    ("ext_submit", False),
    ("ext_keypress", False),
    ("transfer_to_desktop", False),
    ("transfer_to_planner", False),
    ("delegate", False),
])
def test_is_speculative_safe(tool_name, expected):
    from supervisor_graph.speculative import is_speculative_safe
    assert is_speculative_safe(tool_name) is expected
