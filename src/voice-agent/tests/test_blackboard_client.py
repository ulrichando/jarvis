"""BlackboardClient — typed read/write API over Redis."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def client():
    """Connects to localhost Redis and isolates with a unique prefix."""
    from blackboard.client import BlackboardClient
    prefix = f"test:{time.time_ns()}"
    c = BlackboardClient(prefix=prefix)
    yield c
    # Cleanup: delete every key under our prefix.
    for key in c._r.keys(f"{prefix}:*"):
        c._r.delete(key)


def test_write_and_read_screen_fact(client):
    from blackboard.schema import ScreenFact
    f = ScreenFact(
        active_app="chrome", foreground_url="https://example.com",
        tab_count=2, dom_summary="example.com homepage",
        captured_at=time.time(),
    )
    client.write_screen_fact(f)
    back = client.read_screen()
    assert back is not None
    assert back.active_app == "chrome"
    assert back.tab_count == 2


def test_screen_fact_ttl_expires(client):
    from blackboard.schema import ScreenFact
    # Write with 1-second TTL for fast test
    f = ScreenFact(active_app="ephemeral", captured_at=time.time())
    client.write_screen_fact(f, ttl_seconds=1)
    assert client.read_screen() is not None
    time.sleep(1.2)
    assert client.read_screen() is None


def test_write_and_read_tool_result(client):
    from blackboard.schema import ToolResult
    r = ToolResult(
        tool="ext_new_tab",
        args={"url": "https://youtube.com"},
        result="ok: tab opened",
        ok=True,
        ts=time.time(),
        call_id="call_test_001",
    )
    client.write_tool_result(r)
    back = client.read_tool_result("call_test_001")
    assert back is not None
    assert back.tool == "ext_new_tab"
    assert back.ok is True


def test_recent_tools_returns_in_chronological_order(client):
    from blackboard.schema import ToolResult
    base = time.time()
    for i in range(5):
        client.write_tool_result(ToolResult(
            tool=f"tool_{i}", args={}, result="ok", ok=True,
            ts=base + i, call_id=f"call_{i}",
        ))
    recent = client.recent_tools(limit=3)
    # Most recent first.
    assert len(recent) == 3
    assert recent[0].call_id == "call_4"
    assert recent[1].call_id == "call_3"
    assert recent[2].call_id == "call_2"


def test_write_and_read_intent(client):
    from blackboard.schema import Intent
    i = Intent(
        turn_id="turn_test_42", route="TASK", confidence=0.91,
        raw_text="open a new tab", ts=time.time(),
    )
    client.write_intent(i)
    back = client.read_intent("turn_test_42")
    assert back is not None
    assert back.route == "TASK"


def test_read_screen_when_empty_returns_none(client):
    assert client.read_screen() is None
