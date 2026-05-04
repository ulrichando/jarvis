"""Pydantic round-trip + field validation for the three channel families."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_screen_fact_round_trip():
    from blackboard.schema import ScreenFact
    f = ScreenFact(
        active_app="chrome",
        foreground_url="https://youtube.com",
        tab_count=3,
        dom_summary="YouTube homepage with search bar",
        captured_at=time.time(),
    )
    j = f.model_dump_json()
    back = ScreenFact.model_validate_json(j)
    assert back.active_app == "chrome"
    assert back.tab_count == 3


def test_screen_fact_uncertain_path():
    from blackboard.schema import ScreenFact
    f = ScreenFact(uncertain=True, reason="screenshot capture failed")
    assert f.active_app is None
    assert f.uncertain is True


def test_tool_result_round_trip():
    from blackboard.schema import ToolResult
    r = ToolResult(
        tool="ext_new_tab",
        args={"url": "https://youtube.com"},
        result="ok: tab opened",
        ok=True,
        ts=time.time(),
        call_id="call_abc123",
    )
    j = r.model_dump_json()
    back = ToolResult.model_validate_json(j)
    assert back.tool == "ext_new_tab"
    assert back.ok is True


def test_tool_result_failure_recorded():
    from blackboard.schema import ToolResult
    r = ToolResult(
        tool="ext_navigate",
        args={"url": "https://blocked.example"},
        result="error: connection refused",
        ok=False,
        ts=time.time(),
        call_id="call_xyz",
    )
    assert r.ok is False


def test_intent_round_trip():
    from blackboard.schema import Intent
    i = Intent(
        turn_id="turn_42",
        route="TASK",
        confidence=0.95,
        raw_text="open a new tab",
        ts=time.time(),
    )
    j = i.model_dump_json()
    back = Intent.model_validate_json(j)
    assert back.route == "TASK"
    assert back.confidence == 0.95
